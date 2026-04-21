import json
import os
from contextlib import nullcontext
from importlib import reload
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def env():
    with mock.patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://dev:dev@localhost:5432/photo_pipeline",
            "STATE_MACHINE_ARN": (
                "arn:aws:states:us-east-1:000000000000:stateMachine:photo-pipeline"
            ),
            # 给 Langfuse SDK 一个占位 key，避免 @observe / propagate_attributes 在无 env 时报错
            "LANGFUSE_PUBLIC_KEY": "test",
            "LANGFUSE_SECRET_KEY": "test",
            "LANGFUSE_HOST": "http://localhost:3000",
        },
    ):
        yield


def _reload_handler():
    import scheduler.handler

    reload(scheduler.handler)
    return scheduler.handler


def test_handler_creates_batch_and_starts_execution():
    mod = _reload_handler()
    with (
        mock.patch.object(mod, "PgBatchManager") as MockManager,
        mock.patch.object(mod, "get_connection") as mock_conn,
        mock.patch.object(mod, "boto3") as mock_boto,
        mock.patch.object(mod, "propagate_attributes", return_value=nullcontext()) as mock_prop,
    ):
        instance = MockManager.return_value
        instance.create_batch.return_value = 42
        mock_conn.return_value = mock.MagicMock()
        mock_sfn = mock.MagicMock()
        mock_sfn.start_execution.return_value = {
            "executionArn": (
                "arn:aws:states:us-east-1:000000000000:execution:photo-pipeline:batch-42"
            )
        }
        mock_boto.client.return_value = mock_sfn

        event = {"request_id": "test-001", "user_id": 1, "s3_keys": ["a.jpg", "b.jpg"]}
        result = mod.handler(event, None)

        body = json.loads(result["body"])
        assert result["statusCode"] == 200
        assert body["batch_id"] == 42
        assert body["execution_arn"] == (
            "arn:aws:states:us-east-1:000000000000:execution:photo-pipeline:batch-42"
        )
        instance.create_batch.assert_called_once_with("test-001", 1, ["a.jpg", "b.jpg"])
        mock_boto.client.assert_called_once_with("stepfunctions")
        mock_sfn.start_execution.assert_called_once()
        call_kwargs = mock_sfn.start_execution.call_args[1]
        assert "batch-42" in call_kwargs["name"]
        payload = json.loads(call_kwargs["input"])
        assert payload["batch_id"] == 42
        assert payload["s3_keys"] == ["a.jpg", "b.jpg"]
        # Langfuse trace 字段必须存在（未配 key 时可为空串）
        assert "langfuse_trace_id" in payload
        assert isinstance(payload["langfuse_trace_id"], str)
        assert "langfuse_parent_observation_id" in payload
        assert isinstance(payload["langfuse_parent_observation_id"], str)
        mock_prop.assert_called_once_with(
            user_id="1",
            session_id="unknown-batch-42",
            tags=["env:unknown", "photo-pipeline"],
            trace_name="photo_pipeline",
        )
