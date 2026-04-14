"""LangSmith 分布式 trace 传播"""

import os
from contextlib import contextmanager

try:
    from langsmith.run_helpers import get_current_run_tree, tracing_context
    from langsmith.run_trees import RunTree

    _HAS_LANGSMITH = True
except ImportError:
    _HAS_LANGSMITH = False


def get_trace_headers() -> dict:
    """从当前 @traceable 上下文提取 headers，供传播到下游 Lambda"""
    if not _HAS_LANGSMITH or os.environ.get("LANGSMITH_TRACING") != "true":
        return {}
    try:
        rt = get_current_run_tree()
        return rt.to_headers() if rt else {}
    except Exception:
        return {}


@contextmanager
def parent_trace_from(event: dict):
    """从 event 中恢复 parent trace context。

    AWS (LANGSMITH_TRACING=true):  直接设 parent，@traceable 已启用
    本地 (LANGSMITH_TRACING=false): 临时启用 tracing + 设 parent
    无 headers 或无 API key:       直接 yield
    """
    headers = event.get("langsmith_trace_context")
    if not headers or not _HAS_LANGSMITH or not os.environ.get("LANGSMITH_API_KEY"):
        yield
        return

    tracing_was_on = os.environ.get("LANGSMITH_TRACING") == "true"

    try:
        if not tracing_was_on:
            os.environ["LANGSMITH_TRACING"] = "true"

        parent = RunTree.from_headers(headers)
        with tracing_context(parent=parent):
            yield
    except Exception:
        yield
    finally:
        if not tracing_was_on:
            os.environ["LANGSMITH_TRACING"] = "false"
