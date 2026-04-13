import json
import os
from importlib import reload
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def env():
    with mock.patch.dict(os.environ, {
        "DATABASE_URL": "postgresql://dev:dev@localhost:5432/photo_pipeline",
        "LOCAL_DEV": "true",
    }):
        yield


def _reload_handler():
    import scheduler.handler
    reload(scheduler.handler)
    return scheduler.handler


def test_handler_creates_batch_and_returns_batch_id():
    mod = _reload_handler()
    with mock.patch.object(mod, "PgBatchManager") as MockManager, \
         mock.patch.object(mod, "get_connection") as mock_conn:
        instance = MockManager.return_value
        instance.create_batch.return_value = 42
        mock_conn.return_value = mock.MagicMock()

        event = {
            "request_id": "test-001",
            "user_id": 1,
            "s3_keys": ["a.jpg", "b.jpg"],
        }
        result = mod.handler(event, None)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["batch_id"] == 42
        instance.create_batch.assert_called_once_with("test-001", 1, ["a.jpg", "b.jpg"])


def test_handler_skips_batch_submit_when_local():
    mod = _reload_handler()
    with mock.patch.object(mod, "PgBatchManager") as MockManager, \
         mock.patch.object(mod, "get_connection") as mock_conn, \
         mock.patch.object(mod, "boto3") as mock_boto:
        instance = MockManager.return_value
        instance.create_batch.return_value = 1
        mock_conn.return_value = mock.MagicMock()

        mod.handler({"request_id": "r", "user_id": 1, "s3_keys": ["a.jpg"]}, None)

        mock_boto.client.assert_not_called()


def test_handler_submits_batch_job_when_not_local():
    with mock.patch.dict(os.environ, {"LOCAL_DEV": "false", "GPU_JOB_QUEUE": "arn:queue", "JOB_DEFINITION": "arn:jobdef"}):
        mod = _reload_handler()
        with mock.patch.object(mod, "PgBatchManager") as MockManager, \
             mock.patch.object(mod, "get_connection") as mock_conn, \
             mock.patch.object(mod, "boto3") as mock_boto:
            instance = MockManager.return_value
            instance.create_batch.return_value = 7
            mock_conn.return_value = mock.MagicMock()
            mock_batch_client = mock.MagicMock()
            mock_boto.client.return_value = mock_batch_client

            mod.handler({"request_id": "r", "user_id": 1, "s3_keys": ["a.jpg"]}, None)

            mock_batch_client.submit_job.assert_called_once()
            call_kwargs = mock_batch_client.submit_job.call_args[1]
            assert call_kwargs["jobQueue"] == "arn:queue"
