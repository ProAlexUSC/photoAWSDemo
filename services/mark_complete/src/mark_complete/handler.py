from common.batch_manager import PgBatchManager
from common.db import get_connection


def handler(event, context):
    batch_id = event["batch_id"]
    conn = get_connection()
    try:
        mgr = PgBatchManager(conn)
        mgr.mark_batch_complete(batch_id)
        conn.commit()
    finally:
        conn.close()
    return {"batch_id": batch_id, "status": "completed"}
