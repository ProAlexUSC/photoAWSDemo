"""Langfuse 分布式 trace 传播 + Lambda flush 保证。

Scheduler 用 init_trace_id(seed=request_id) 生成确定性 32-hex trace_id，在 @observe
root span 内捕获自己的 observation_id 作为 parent，两个字段一起塞进 SFN input，下游
Lambda 通过 @observe 的 langfuse_trace_id + langfuse_parent_observation_id kwarg
挂到同一棵树（真父子嵌套，不是平铺 sibling）。

flush() 在每次 handler 退出前强制调用，避免 Warm Worker 冻结时在途 span 丢失。
"""

import contextlib
from contextlib import contextmanager

from langfuse import get_client


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
    import os

    return _extract_langfuse_kwargs(
        env if env is not None else os.environ,
        "LANGFUSE_TRACE_ID",
        "LANGFUSE_PARENT_OBS_ID",
    )


def run_traced(fn, event: dict, *args, **kwargs):
    """Lambda handler 专用 helper：
    1. 从 event 抽 langfuse_trace_id / langfuse_parent_observation_id
    2. 把它们作为 @observe 魔法 kwarg 注入 fn 调用
    3. 退出前 flush

    用法：
        def handler(event, context):
            return run_traced(_tag_photo, event, event["photo_id"])
    """
    with traced_handler():
        return fn(*args, **kwargs, **kwargs_from_event(event))
