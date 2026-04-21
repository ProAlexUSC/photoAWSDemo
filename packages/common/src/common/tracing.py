"""Langfuse 分布式 trace 传播 + Lambda flush + AWS runtime 元信息关联。

Scheduler 用 init_trace_id(seed=request_id) 生成确定性 32-hex trace_id，@observe
root span 内用 get_current_observation_id() 捕获自己的 observation_id 作为 parent_id，
两个字段经 SFN input 传给下游 Lambda，下游 @observe 的 langfuse_trace_id +
langfuse_parent_observation_id 魔法 kwarg（SDK v4 源码级特性，未公开文档）接管后把
span 挂到同一棵树（真父子嵌套而非平铺 sibling）。

attach_aws_runtime_context() 自动检测 Lambda / Batch runtime，把 request_id / log_group /
batch job_id / CloudWatch 或 Batch 控制台直跳 URL 写进当前 span metadata，实现
Langfuse span ↔ AWS 控制台双向跳转。
"""

import contextlib
import contextvars
import inspect
import os
from contextlib import contextmanager

from langfuse import get_client

# run_traced / lambda_context_scope 设置；attach_aws_runtime_context 读 aws_request_id
_lambda_context_var: contextvars.ContextVar = contextvars.ContextVar("lambda_context", default=None)


def init_trace_id(seed: str) -> str:
    """基于 seed（request_id）生成 32-hex 确定性 trace_id。
    无 Langfuse key 时返回空串，下游 _langfuse_kwargs 识别为"无 trace"退化，不报错。"""
    try:
        return get_client().create_trace_id(seed=seed)
    except Exception:
        return ""


@contextmanager
def traced_handler():
    """Lambda handler 的 with 包装，退出前强制 flush 避免 Warm Worker 冻结时丢 span。"""
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            get_client().flush()


def _langfuse_kwargs(source, trace_key: str, parent_key: str) -> dict:
    """从 event dict 或 env dict 抽 Langfuse 魔法 kwarg；空串/缺失跳过。"""
    kw: dict[str, str] = {}
    if tid := source.get(trace_key):
        kw["langfuse_trace_id"] = tid
    if pid := source.get(parent_key):
        kw["langfuse_parent_observation_id"] = pid
    return kw


def kwargs_from_event(event: dict) -> dict:
    """Lambda 从 SFN event 抽 langfuse_trace_id / langfuse_parent_observation_id。"""
    return _langfuse_kwargs(event, "langfuse_trace_id", "langfuse_parent_observation_id")


def kwargs_from_env(env=None) -> dict:
    """Worker 从 docker -e 注入的环境变量抽 LANGFUSE_TRACE_ID / LANGFUSE_PARENT_OBS_ID。"""
    return _langfuse_kwargs(
        env if env is not None else os.environ, "LANGFUSE_TRACE_ID", "LANGFUSE_PARENT_OBS_ID"
    )


def _cloudwatch_log_url(region: str, log_group: str, log_stream: str) -> str:
    """AWS 控制台 log-events 查看器 URL；对 / [ ] 做 `$`-prefix 双重编码（控制台专用非标编码）。"""
    lg_enc = log_group.replace("/", "$252F")
    ls_enc = log_stream.replace("/", "$252F").replace("[", "$255B").replace("]", "$255D")
    return (
        f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}"
        f"#logsV2:log-groups/log-group/{lg_enc}/log-events/{ls_enc}"
    )


def attach_aws_runtime_context(extra: dict | None = None) -> None:
    """挂 AWS runtime 元信息 + 业务自定义字段到当前 span metadata。自动识别 runtime：
    Lambda (AWS_LAMBDA_LOG_GROUP_NAME) / Batch (AWS_BATCH_JOB_ID) / 本地（都缺 → 只填 app.env）。

    `extra` 合并进同一次 update_current_span 调用，避免 Langfuse v4 metadata replace
    语义下"attach 在前 → 业务 update 在后把 aws.* 冲掉"的坑。

    用法：attach_aws_runtime_context(extra={"s3_key": s3_key, "tag_count": n})
    """
    with contextlib.suppress(Exception):
        md: dict[str, str] = {"app.env": os.environ.get("APP_ENV", "unknown")}
        if extra:
            md.update(extra)
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""

        if log_group := os.environ.get("AWS_LAMBDA_LOG_GROUP_NAME"):
            log_stream = os.environ.get("AWS_LAMBDA_LOG_STREAM_NAME", "")
            md["aws.function"] = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
            md["aws.log_group"] = log_group
            md["aws.log_stream"] = log_stream
            if ctx := _lambda_context_var.get():
                md["aws.request_id"] = getattr(ctx, "aws_request_id", "") or ""
            if region and log_stream:
                md["aws.log_url"] = _cloudwatch_log_url(region, log_group, log_stream)

        if job_id := os.environ.get("AWS_BATCH_JOB_ID"):
            md["aws.batch.job_id"] = job_id
            md["aws.batch.job_attempt"] = os.environ.get("AWS_BATCH_JOB_ATTEMPT", "")
            md["aws.batch.jq_name"] = os.environ.get("AWS_BATCH_JQ_NAME", "")
            md["aws.batch.ce_name"] = os.environ.get("AWS_BATCH_CE_NAME", "")
            if region:
                md["aws.console_url"] = (
                    f"https://{region}.console.aws.amazon.com/batch/home?region={region}"
                    f"#jobs/detail/{job_id}"
                )

        get_client().update_current_span(metadata=md)


@contextmanager
def lambda_context_scope(context):
    """设 contextvar 让 attach_aws_runtime_context() 能读到 aws_request_id。"""
    token = _lambda_context_var.set(context)
    try:
        yield
    finally:
        _lambda_context_var.reset(token)


def _bind_business_kwargs(fn, args: tuple, kwargs: dict) -> dict | None:
    """把业务参数绑定成命名 kwargs，避免 @observe 只看到匿名 args。"""
    try:
        signature = inspect.signature(fn)
        bound = signature.bind_partial(*args, **kwargs)
    except TypeError:
        return None

    if any(
        signature.parameters[name].kind == inspect.Parameter.POSITIONAL_ONLY
        for name in bound.arguments
    ):
        return None

    return dict(bound.arguments)


def run_traced(fn, event: dict, *args, lambda_context=None, **kwargs):
    """Lambda handler 标准套路：设 lambda_context → 抽 event 的 trace kwarg 注入 fn →
    退出 flush。用法：return run_traced(_fn, event, event["photo_id"], lambda_context=context)"""
    token = _lambda_context_var.set(lambda_context)
    try:
        with traced_handler():
            business_kwargs = _bind_business_kwargs(fn, args, kwargs)
            if business_kwargs is not None:
                return fn(**business_kwargs, **kwargs_from_event(event))

            return fn(*args, **kwargs, **kwargs_from_event(event))
    finally:
        _lambda_context_var.reset(token)
