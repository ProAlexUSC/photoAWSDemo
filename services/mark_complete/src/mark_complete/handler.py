from common.batch_manager import PgBatchManager
from common.db import get_connection
from common.tracing import parent_trace_from
from langsmith import traceable


@traceable(name="mark_complete")
def _mark_complete(batch_id):
    conn = get_connection()
    try:
        mgr = PgBatchManager(conn)
        mgr.mark_batch_complete(batch_id)
        conn.commit()
    finally:
        conn.close()
    return {"batch_id": batch_id, "status": "completed"}


def handler(event, context):
    with parent_trace_from(event):
        return _mark_complete(event["batch_id"])
