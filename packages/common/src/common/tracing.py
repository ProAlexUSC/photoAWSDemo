"""Langfuse 分布式 trace 传播 + Lambda flush 保证 + AWS 资源元信息关联。

Scheduler 用 init_trace_id(seed=request_id) 生成确定性 32-hex trace_id，在 @observe
root span 内捕获自己的 observation_id 作为 parent，两个字段一起塞进 SFN input，下游
Lambda 通过 @observe 的 langfuse_trace_id + langfuse_parent_observation_id kwarg
挂到同一棵树（真父子嵌套，不是平铺 sibling）。

flush() 在每次 handler 退出前强制调用，避免 Warm Worker 冻结时在途 span 丢失。

attach_aws_lambda_context() / attach_aws_batch_context() 把 AWS runtime 元信息
（request_id / log_group / batch job_id / CloudWatch 直跳 URL）写进当前 span metadata，
实现 Langfuse span ↔ AWS 控制台日志/作业双向关联。
"""

import contextlib
import contextvars
import os
from contextlib import contextmanager

from langfuse import get_client

# run_traced / lambda_context_scope 设置；attach_aws_lambda_context 读 aws_request_id
_lambda_context_var: contextvars.ContextVar = contextvars.ContextVar("lambda_context", default=None)


def _app_env() -> str:
    return os.environ.get("APP_ENV", "unknown")


def _aws_region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""


def init_trace_id(seed: str) -> str:
    """基于 seed（request_id）生成 32-hex 确定性 trace_id。

    未配 Langfuse 或初始化失败时返回空字符串；下游 kwargs_from_event 识别为"无 trace"
    跳过，@observe 仍可工作但 span 不会关联到目标 trace（退化，不报错）。
    """
    try:
        return get_client().create_trace_id(seed=seed)
    except Exception:
        return ""


@contextmanager
def traced_handler():
    """Lambda handler 用 with 包住 handler 体，退出前强制 flush。

    适用于有 key / 无 key 两种场景；无 key 时 get_client() 返回的 no-op client
    的 flush() 也是 no-op。flush 本身异常不影响业务返回。
    """
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            get_client().flush()


def _extract_langfuse_kwargs(source: dict, trace_key: str, parent_key: str) -> dict:
    """从任意 dict-like source 抽 Langfuse 魔法 kwarg；空串/缺失跳过。"""
    kw: dict[str, str] = {}
    if tid := source.get(trace_key):
        kw["langfuse_trace_id"] = tid
    if pid := source.get(parent_key):
        kw["langfuse_parent_observation_id"] = pid
    return kw


def kwargs_from_event(event: dict) -> dict:
    """Lambda 用：event 里 langfuse_trace_id / langfuse_parent_observation_id 字段。"""
    return _extract_langfuse_kwargs(event, "langfuse_trace_id", "langfuse_parent_observation_id")


def kwargs_from_env(env: dict | None = None) -> dict:
    """Worker（非 Lambda）用：环境变量 LANGFUSE_TRACE_ID / LANGFUSE_PARENT_OBS_ID。"""
    return _extract_langfuse_kwargs(
        env if env is not None else os.environ,
        "LANGFUSE_TRACE_ID",
        "LANGFUSE_PARENT_OBS_ID",
    )


def _cloudwatch_log_url(region: str, log_group: str, log_stream: str) -> str:
    """AWS 控制台 CloudWatch log-events 查看器 URL，对 / [ ] 做 `$`-prefix 双重编码
    （$252F = %252F = %2F，控制台要求的特殊格式，非标准 URL 编码）。"""
    lg_enc = log_group.replace("/", "$252F")
    ls_enc = log_stream.replace("/", "$252F").replace("[", "$255B").replace("]", "$255D")
    return (
        f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}"
        f"#logsV2:log-groups/log-group/{lg_enc}/log-events/{ls_enc}"
    )


def attach_aws_lambda_context() -> None:
    """在 @observe 装饰的函数内调用。始终写 app.env；真 AWS Lambda 额外追加
    aws.request_id / log_url（MiniStack 不注入 AWS_LAMBDA_* → 跳过云端字段，不报错）。"""
    with contextlib.suppress(Exception):
        md: dict[str, str] = {"app.env": _app_env()}
        log_group = os.environ.get("AWS_LAMBDA_LOG_GROUP_NAME", "")
        if log_group:
            region = _aws_region()
            log_stream = os.environ.get("AWS_LAMBDA_LOG_STREAM_NAME", "")
            md["aws.function"] = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
            md["aws.log_group"] = log_group
            md["aws.log_stream"] = log_stream
            ctx = _lambda_context_var.get()
            if ctx is not None:
                md["aws.request_id"] = getattr(ctx, "aws_request_id", "") or ""
            if region and log_stream:
                md["aws.log_url"] = _cloudwatch_log_url(region, log_group, log_stream)
        get_client().update_current_span(metadata=md)


def attach_aws_batch_context() -> None:
    """Worker（AWS Batch）专用：挂 Batch runtime 元信息 + 控制台直跳 URL 到当前 span。
    本地 docker-compose 下 AWS_BATCH_* 不存在 → 字段留空，app.env 照常填。"""
    with contextlib.suppress(Exception):
        job_id = os.environ.get("AWS_BATCH_JOB_ID", "")
        region = _aws_region()
        md = {
            "app.env": _app_env(),
            "aws.batch.job_id": job_id,
            "aws.batch.job_attempt": os.environ.get("AWS_BATCH_JOB_ATTEMPT", ""),
            "aws.batch.jq_name": os.environ.get("AWS_BATCH_JQ_NAME", ""),
            "aws.batch.ce_name": os.environ.get("AWS_BATCH_CE_NAME", ""),
        }
        if job_id and region:
            md["aws.console_url"] = (
                f"https://{region}.console.aws.amazon.com/batch/home?region={region}"
                f"#jobs/detail/{job_id}"
            )
        get_client().update_current_span(metadata=md)


@contextmanager
def lambda_context_scope(context):
    """设置 contextvar 让后续 attach_aws_lambda_context() 能读到 aws_request_id。

    handler 有自定义逻辑（非 run_traced 套路）时用：
        def handler(event, context):
            with traced_handler(), lambda_context_scope(context):
                ...
    """
    token = _lambda_context_var.set(context)
    try:
        yield
    finally:
        _lambda_context_var.reset(token)


def run_traced(fn, event: dict, *args, lambda_context=None, **kwargs):
    """Lambda handler 专用 helper：
    1. 设 contextvar 让下游 attach_aws_lambda_context 能读到 aws_request_id
    2. 从 event 抽 langfuse_trace_id / langfuse_parent_observation_id 注入 fn 调用
    3. 退出前 flush

    用法：
        def handler(event, context):
            return run_traced(_tag_photo, event, event["photo_id"], lambda_context=context)
    """
    token = _lambda_context_var.set(lambda_context)
    try:
        with traced_handler():
            return fn(*args, **kwargs, **kwargs_from_event(event))
    finally:
        _lambda_context_var.reset(token)
