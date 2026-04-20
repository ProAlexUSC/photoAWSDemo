import contextlib
import json
import os

import boto3
import cv2
import numpy as np
import onnxruntime
from common.batch_manager import PgBatchManager
from common.db import get_connection
from insightface.app import FaceAnalysis
from langfuse import get_client, observe


def _load_model() -> FaceAnalysis:
    providers = (
        ["CUDAExecutionProvider"]
        if "CUDAExecutionProvider" in onnxruntime.get_available_providers()
        else ["CPUExecutionProvider"]
    )
    model = FaceAnalysis(name="buffalo_l", providers=providers)
    model.prepare(ctx_id=0)
    return model


@observe(name="stage1_face_detect")
def _process_batch_inner(batch_id: int, s3_keys: list[str]) -> dict:
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
                obj = s3.get_object(Bucket="photo-uploads", Key=s3_key)
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

    # 可选：env 里带上 trace 上下文就挂到上游 trace；否则独立一条
    kw: dict[str, str] = {}
    tid = os.environ.get("LANGFUSE_TRACE_ID")
    if tid:
        kw["langfuse_trace_id"] = tid
    pid = os.environ.get("LANGFUSE_PARENT_OBS_ID")
    if pid:
        kw["langfuse_parent_observation_id"] = pid

    try:
        _process_batch_inner(batch_id, s3_keys, **kw)
    finally:
        # Worker 是短命进程，显式 flush 防止 span 丢
        with contextlib.suppress(Exception):
            get_client().flush()


if __name__ == "__main__":
    process_batch()
