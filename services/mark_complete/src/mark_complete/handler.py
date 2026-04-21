from common.batch_manager import PgBatchManager
from common.db import get_connection
from common.tracing import attach_aws_runtime_context, run_traced
from langfuse import observe


@observe(name="mark_complete")
def _mark_complete(batch_id):
    attach_aws_runtime_context()
    conn = get_connection()
    try:
        mgr = PgBatchManager(conn)
        mgr.mark_batch_complete(batch_id)
        conn.commit()
    finally:
        conn.close()
    return {"batch_id": batch_id, "status": "completed"}


def handler(event, context):
    return run_traced(_mark_complete, event, event["batch_id"], lambda_context=context)
