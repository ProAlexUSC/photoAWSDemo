import json
import os

import boto3
import cv2
import numpy as np
import onnxruntime
from common.batch_manager import PgBatchManager
from common.db import get_connection
from common.tracing import attach_aws_batch_context, kwargs_from_env, traced_handler
from insightface.app import FaceAnalysis
from langfuse import get_client, observe


def _load_model() -> FaceAnalysis:
    providers = (
        ["CUDAExecutionProvider"]
        if "CUDAExecutionProvider" in onnxruntime.get_available_providers()
        else ["CPUExecutionProvider"]
    )
    # allowed_modules 限定只加载 detection + recognition 两个 ONNX（det_10g + w600k_r50）
    # 跳过 genderage / landmark_2d / landmark_3d 三个我们不用的模型，启动加速 ~40%
    model = FaceAnalysis(
        name="buffalo_l",
        providers=providers,
        allowed_modules=["detection", "recognition"],
    )
    model.prepare(ctx_id=0)
    return model


@observe(name="stage1_face_detect")
def _process_batch_inner(batch_id: int, s3_keys: list[str]) -> dict:
    attach_aws_batch_context()  # 挂 AWS Batch job_id / console_url / app.env 到本 span metadata
    # bucket 走 env 以适配 AWS（photo-uploads-<acct>-<region>）和本地 MiniStack（photo-uploads）
    s3_bucket = os.environ.get("S3_BUCKET", "photo-uploads")
    lf = get_client()
    s3 = boto3.client("s3")
    model = _load_model()

    total_faces = 0
    conn = get_connection()
    try:
        manager = PgBatchManager(conn)

        for s3_key in s3_keys:
            # 每张照片单开子 span，带 s3_key / face_count / embedding dim metadata
            with lf.start_as_current_observation(as_type="span", name="detect_one_photo") as obs:
                obj = s3.get_object(Bucket=s3_bucket, Key=s3_key)
                img_bytes = obj["Body"].read()
                img_array = np.frombuffer(img_bytes, dtype=np.uint8)
                img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

                faces = model.get(img)

                photo_id = manager.get_photo_id(batch_id, s3_key)
                embedding_dim = 0
                for face in faces:
                    bbox = {
                        "x": int(face.bbox[0]),
                        "y": int(face.bbox[1]),
                        "w": int(face.bbox[2] - face.bbox[0]),
                        "h": int(face.bbox[3] - face.bbox[1]),
                    }
                    emb = face.normed_embedding.tolist()
                    embedding_dim = len(emb)
                    manager.insert_embedding(photo_id=photo_id, embedding=emb, bbox=bbox)

                manager.mark_photo_complete(photo_id, face_count=len(faces))
                total_faces += len(faces)

                obs.update(
                    input={"s3_key": s3_key, "photo_id": photo_id},
                    output={"face_count": len(faces), "embedding_dim": embedding_dim},
                )

        manager.mark_batch_complete(batch_id)
        conn.commit()
    finally:
        conn.close()

    return {"photos": len(s3_keys), "total_faces": total_faces}


def process_batch():
    batch_id = int(os.environ["BATCH_ID"])
    s3_keys = json.loads(os.environ["S3_KEYS"])
    with traced_handler():
        _process_batch_inner(batch_id, s3_keys, **kwargs_from_env())


if __name__ == "__main__":
    process_batch()
