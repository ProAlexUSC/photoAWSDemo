import json
import os
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
    ):
        instance = MockManager.return_value
        instance.create_batch.return_value = 42
        mock_conn.return_value = mock.MagicMock()
        mock_sfn = mock.MagicMock()
        mock_boto.client.return_value = mock_sfn

        event = {"request_id": "test-001", "user_id": 1, "s3_keys": ["a.jpg", "b.jpg"]}
        result = mod.handler(event, None)

        body = json.loads(result["body"])
        assert result["statusCode"] == 200
        assert body["batch_id"] == 42
        instance.create_batch.assert_called_once_with("test-001", 1, ["a.jpg", "b.jpg"])
        mock_boto.client.assert_called_once_with("stepfunctions")
        mock_sfn.start_execution.assert_called_once()
        call_kwargs = mock_sfn.start_execution.call_args[1]
        assert "batch-42" in call_kwargs["name"]
        payload = json.loads(call_kwargs["input"])
        assert payload["batch_id"] == 42
        assert payload["s3_keys"] == ["a.jpg", "b.jpg"]
