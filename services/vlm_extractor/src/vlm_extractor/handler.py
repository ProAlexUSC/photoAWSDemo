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

    result = {"photo_id": photo_id, "status": "extracted", "s3_key": s3_key}
    get_client().update_current_span(input={"photo_id": photo_id, "s3_key": s3_key}, output=result)
    attach_aws_runtime_context(extra={"s3_key": s3_key})
    return result


def handler(event, context):
    return run_traced(_vlm_extract, event, event["photo_id"], lambda_context=context)
