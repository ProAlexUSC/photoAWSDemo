from common.db import get_connection
from common.tracing import run_traced
from langfuse import observe


@observe(name="get_photo_ids")
def _get_photo_ids(batch_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT photo_id FROM photos WHERE batch_id = %s ORDER BY photo_id",
            (batch_id,),
        )
        photo_ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
    return {"batch_id": batch_id, "photo_ids": photo_ids}


def handler(event, context):
    return run_traced(_get_photo_ids, event, event["batch_id"])
