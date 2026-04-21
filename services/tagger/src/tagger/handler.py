import json

from common.db import get_connection
from common.tracing import attach_aws_runtime_context, run_traced
from langfuse import get_client, observe


@observe(name="stage2_tag_photo")
def _tag_photo(photo_id):
    tags = {"scene": "outdoor", "objects": ["person", "tree"], "mood": "happy"}
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE photos SET tags = %s WHERE photo_id = %s RETURNING s3_key",
            (json.dumps(tags), photo_id),
        )
        row = cur.fetchone()
        s3_key = row[0] if row else None
        conn.commit()
    finally:
        conn.close()

    result = {"photo_id": photo_id, "status": "tagged", "s3_key": s3_key, "tag_count": len(tags)}
    get_client().update_current_span(input={"photo_id": photo_id, "s3_key": s3_key}, output=result)
    attach_aws_runtime_context(extra={"s3_key": s3_key, "tag_count": len(tags)})
    return result


def handler(event, context):
    return run_traced(_tag_photo, event, event["photo_id"], lambda_context=context)
