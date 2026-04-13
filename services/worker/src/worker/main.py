import json
import os

import boto3
import cv2
import numpy as np
import onnxruntime
from insightface.app import FaceAnalysis

from common.batch_manager import PgBatchManager
from common.db import get_connection


def _load_model() -> FaceAnalysis:
    providers = (
        ["CUDAExecutionProvider"]
        if "CUDAExecutionProvider" in onnxruntime.get_available_providers()
        else ["CPUExecutionProvider"]
    )
    model = FaceAnalysis(name="buffalo_l", providers=providers)
    model.prepare(ctx_id=0)
    return model


def process_batch():
    batch_id = int(os.environ["BATCH_ID"])
    s3_keys = json.loads(os.environ["S3_KEYS"])

    s3 = boto3.client("s3")
    model = _load_model()

    conn = get_connection()
    try:
        manager = PgBatchManager(conn)

        for s3_key in s3_keys:
            obj = s3.get_object(Bucket="photo-uploads", Key=s3_key)
            img_bytes = obj["Body"].read()
            img_array = np.frombuffer(img_bytes, dtype=np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

            faces = model.get(img)

            photo_id = manager.get_photo_id(batch_id, s3_key)
            for face in faces:
                bbox = {
                    "x": int(face.bbox[0]),
                    "y": int(face.bbox[1]),
                    "w": int(face.bbox[2] - face.bbox[0]),
                    "h": int(face.bbox[3] - face.bbox[1]),
                }
                manager.insert_embedding(
                    photo_id=photo_id,
                    embedding=face.normed_embedding.tolist(),
                    bbox=bbox,
                )

            manager.mark_photo_complete(photo_id, face_count=len(faces))

        manager.mark_batch_complete(batch_id)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    process_batch()
