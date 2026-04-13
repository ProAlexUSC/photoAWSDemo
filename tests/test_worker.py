import json
import os
import sys
from importlib import reload
from unittest import mock

import numpy as np
import pytest


# Pre-mock heavy ML dependencies that are not installed in the dev environment
_mock_onnxruntime = mock.MagicMock()
_mock_onnxruntime.get_available_providers.return_value = ["CPUExecutionProvider"]
sys.modules.setdefault("onnxruntime", _mock_onnxruntime)
sys.modules.setdefault("insightface", mock.MagicMock())
sys.modules.setdefault("insightface.app", mock.MagicMock())
sys.modules.setdefault("cv2", mock.MagicMock())


@pytest.fixture(autouse=True)
def env():
    with mock.patch.dict(os.environ, {
        "DATABASE_URL": "postgresql://dev:dev@localhost:5432/photo_pipeline",
        "LOCAL_DEV": "true",
        "AWS_ENDPOINT_URL": "http://localhost:4566",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_DEFAULT_REGION": "us-east-1",
        "BATCH_ID": "1",
        "S3_KEYS": json.dumps(["test/photo_0.jpg"]),
    }):
        yield


def _make_fake_face():
    face = mock.MagicMock()
    face.normed_embedding = np.random.randn(512).astype(np.float32)
    face.bbox = np.array([10.0, 20.0, 110.0, 140.0])
    return face


def _reload_worker():
    import worker.main
    reload(worker.main)
    return worker.main


def test_process_batch_downloads_from_s3():
    mod = _reload_worker()
    with mock.patch.object(mod, "FaceAnalysis") as MockFA, \
         mock.patch.object(mod, "boto3") as mock_boto, \
         mock.patch.object(mod, "PgBatchManager") as MockManager, \
         mock.patch.object(mod, "get_connection") as mock_conn, \
         mock.patch.object(mod, "cv2") as mock_cv2:

        mock_s3 = mock.MagicMock()
        mock_boto.client.return_value = mock_s3
        mock_s3.get_object.return_value = {
            "Body": mock.MagicMock(read=lambda: b"\xff\xd8\xff\xe0fake-jpeg")
        }
        mock_cv2.imdecode.return_value = np.zeros((100, 100, 3), dtype=np.uint8)

        model_instance = MockFA.return_value
        model_instance.get.return_value = [_make_fake_face()]

        manager_instance = MockManager.return_value
        manager_instance.get_photo_id.return_value = 100
        mock_conn.return_value = mock.MagicMock()

        mod.process_batch()

        mock_s3.get_object.assert_called_once_with(
            Bucket="photo-uploads", Key="test/photo_0.jpg"
        )


def test_process_batch_writes_embeddings():
    mod = _reload_worker()
    with mock.patch.object(mod, "FaceAnalysis") as MockFA, \
         mock.patch.object(mod, "boto3") as mock_boto, \
         mock.patch.object(mod, "PgBatchManager") as MockManager, \
         mock.patch.object(mod, "get_connection") as mock_conn, \
         mock.patch.object(mod, "cv2") as mock_cv2:

        mock_s3 = mock.MagicMock()
        mock_boto.client.return_value = mock_s3
        mock_s3.get_object.return_value = {
            "Body": mock.MagicMock(read=lambda: b"\xff\xd8\xff\xe0fake-jpeg")
        }
        mock_cv2.imdecode.return_value = np.zeros((100, 100, 3), dtype=np.uint8)

        fake_face = _make_fake_face()
        model_instance = MockFA.return_value
        model_instance.get.return_value = [fake_face]

        manager_instance = MockManager.return_value
        manager_instance.get_photo_id.return_value = 100
        mock_conn.return_value = mock.MagicMock()

        mod.process_batch()

        manager_instance.insert_embedding.assert_called_once()
        call_args = manager_instance.insert_embedding.call_args
        assert call_args[1]["photo_id"] == 100 or call_args[0][0] == 100
        # Check embedding is 512-dim
        embedding_arg = call_args[1].get("embedding") or call_args[0][1]
        assert len(embedding_arg) == 512
        # Check bbox has x key
        bbox_arg = call_args[1].get("bbox") or call_args[0][2]
        assert "x" in bbox_arg


def test_process_batch_marks_completion():
    mod = _reload_worker()
    with mock.patch.object(mod, "FaceAnalysis") as MockFA, \
         mock.patch.object(mod, "boto3") as mock_boto, \
         mock.patch.object(mod, "PgBatchManager") as MockManager, \
         mock.patch.object(mod, "get_connection") as mock_conn, \
         mock.patch.object(mod, "cv2") as mock_cv2:

        mock_s3 = mock.MagicMock()
        mock_boto.client.return_value = mock_s3
        mock_s3.get_object.return_value = {
            "Body": mock.MagicMock(read=lambda: b"\xff\xd8\xff\xe0fake-jpeg")
        }
        mock_cv2.imdecode.return_value = np.zeros((100, 100, 3), dtype=np.uint8)

        model_instance = MockFA.return_value
        model_instance.get.return_value = [_make_fake_face()]

        manager_instance = MockManager.return_value
        manager_instance.get_photo_id.return_value = 100
        mock_conn.return_value = mock.MagicMock()

        mod.process_batch()

        manager_instance.mark_photo_complete.assert_called_once_with(100, face_count=1)
        manager_instance.mark_batch_complete.assert_called_once_with(1)
