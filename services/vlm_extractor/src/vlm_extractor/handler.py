import json

from common.db import get_connection
from common.tracing import attach_aws_runtime_context, run_traced
from langfuse import get_client, observe


@observe(name="stage3_vlm_extract")
def _vlm_extract(photo_id):
    vlm_result = {
        "description": "A person standing in a park",
        "entities": [{"type": "person", "name": "unknown"}],
        "location_guess": "urban park",
    }
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE photos SET vlm_result = %s WHERE photo_id = %s RETURNING s3_key",
            (json.dumps(vlm_result), photo_id),
        )
        row = cur.fetchone()
        s3_key = row[0] if row else None
        conn.commit()
    finally:
        conn.close()

    # attach 放最后：Langfuse v4 的 update_current_span(metadata=...) 实际是 replace 语义，
    # 若 attach 在前，业务侧后续 update 会把 aws.* / app.env 冲掉 (观测到 vlm 丢 AWS 字段)
    get_client().update_current_span(
        input={"photo_id": photo_id, "s3_key": s3_key},
        metadata={"s3_key": s3_key},
    )
    attach_aws_runtime_context()
    return {"photo_id": photo_id, "status": "extracted", "s3_key": s3_key}


def handler(event, context):
    return run_traced(_vlm_extract, event, event["photo_id"], lambda_context=context)
