"""单测覆盖 common.tracing 的 4 个公共符号：
- kwargs_from_event
- init_trace_id
- traced_handler
- run_traced

用 unittest.mock.patch 隔离 get_client，不发真实 Langfuse 请求。
init_trace_id 直接走 real Langfuse client（create_trace_id 是纯函数哈希，不依赖网络）。
"""

import os
from unittest import mock

import pytest
from common.tracing import (
    init_trace_id,
    kwargs_from_event,
    run_traced,
    traced_handler,
)


@pytest.fixture(autouse=True)
def _langfuse_env():
    """给 Langfuse SDK 一个占位 key，避免 get_client() 在无 env 时告警。"""
    with mock.patch.dict(
        os.environ,
        {
            "LANGFUSE_PUBLIC_KEY": "test",
            "LANGFUSE_SECRET_KEY": "test",
            "LANGFUSE_HOST": "http://localhost:3000",
        },
    ):
        yield


# ---------- kwargs_from_event ----------


def test_kwargs_from_event_empty_returns_empty_dict():
    assert kwargs_from_event({}) == {}


def test_kwargs_from_event_trace_id_only():
    assert kwargs_from_event({"langfuse_trace_id": "abc"}) == {"langfuse_trace_id": "abc"}


def test_kwargs_from_event_trace_and_parent():
    event = {
        "langfuse_trace_id": "abc",
        "langfuse_parent_observation_id": "span-1",
    }
    kw = kwargs_from_event(event)
    assert kw == {
        "langfuse_trace_id": "abc",
        "langfuse_parent_observation_id": "span-1",
    }


def test_kwargs_from_event_empty_strings_ignored():
    event = {
        "langfuse_trace_id": "",
        "langfuse_parent_observation_id": "",
    }
    assert kwargs_from_event(event) == {}


# ---------- init_trace_id ----------


def test_init_trace_id_returns_32_hex():
    tid = init_trace_id("some-request-id")
    assert isinstance(tid, str)
    assert len(tid) == 32
    int(tid, 16)  # 必须合法 hex


def test_init_trace_id_deterministic_for_same_seed():
    assert init_trace_id("seed-A") == init_trace_id("seed-A")


def test_init_trace_id_different_seeds_differ():
    assert init_trace_id("seed-A") != init_trace_id("seed-B")


def test_init_trace_id_swallows_exception_returns_empty():
    with mock.patch("common.tracing.get_client") as gc:
        gc.side_effect = RuntimeError("boom")
        assert init_trace_id("whatever") == ""


# ---------- traced_handler ----------


def test_traced_handler_calls_flush_on_exit():
    with mock.patch("common.tracing.get_client") as gc:
        client = mock.MagicMock()
        gc.return_value = client
        with traced_handler():
            pass
        client.flush.assert_called_once()


def test_traced_handler_flush_exception_suppressed():
    with mock.patch("common.tracing.get_client") as gc:
        client = mock.MagicMock()
        client.flush.side_effect = RuntimeError("flush boom")
        gc.return_value = client
        # 不应抛
        with traced_handler():
            pass
        client.flush.assert_called_once()


def test_traced_handler_flush_called_even_when_body_raises():
    with mock.patch("common.tracing.get_client") as gc:
        client = mock.MagicMock()
        gc.return_value = client
        with pytest.raises(ValueError), traced_handler():
            raise ValueError("body boom")
        client.flush.assert_called_once()


# ---------- run_traced ----------


def test_run_traced_no_trace_fields_calls_fn_without_trace_kwargs():
    fn = mock.MagicMock(return_value="result")
    with mock.patch("common.tracing.get_client") as gc:
        client = mock.MagicMock()
        gc.return_value = client
        result = run_traced(fn, {}, 1, 2, x=3)

    assert result == "result"
    fn.assert_called_once_with(1, 2, x=3)
    # 不应注入 langfuse_* kwarg
    call_kwargs = fn.call_args.kwargs
    assert "langfuse_trace_id" not in call_kwargs
    assert "langfuse_parent_observation_id" not in call_kwargs
    client.flush.assert_called_once()


def test_run_traced_injects_trace_kwargs_from_event():
    fn = mock.MagicMock(return_value="ok")
    event = {
        "langfuse_trace_id": "trace-xyz",
        "langfuse_parent_observation_id": "span-root",
    }
    with mock.patch("common.tracing.get_client") as gc:
        client = mock.MagicMock()
        gc.return_value = client
        result = run_traced(fn, event, 99)

    assert result == "ok"
    fn.assert_called_once_with(
        99,
        langfuse_trace_id="trace-xyz",
        langfuse_parent_observation_id="span-root",
    )
    client.flush.assert_called_once()


def test_run_traced_flushes_even_when_fn_raises():
    def boom(*_args, **_kwargs):
        raise RuntimeError("fn boom")

    with mock.patch("common.tracing.get_client") as gc:
        client = mock.MagicMock()
        gc.return_value = client
        with pytest.raises(RuntimeError, match="fn boom"):
            run_traced(boom, {"langfuse_trace_id": "t"}, 1)
        client.flush.assert_called_once()
