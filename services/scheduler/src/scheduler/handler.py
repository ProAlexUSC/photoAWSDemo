import json
import os

import boto3
from common.batch_manager import PgBatchManager
from common.db import get_connection
from common.tracing import get_trace_headers
from langsmith import traceable


@traceable(name="photo_pipeline")
def handler(event, context):
    request_id = event["request_id"]
    user_id = event["user_id"]
    s3_keys = event["s3_keys"]

    conn = get_connection()
    try:
        manager = PgBatchManager(conn)
        batch_id = manager.create_batch(request_id, user_id, s3_keys)
        conn.commit()
    finally:
        conn.close()

    trace_ctx = get_trace_headers()

    sfn = boto3.client("stepfunctions")
    sfn.start_execution(
        stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        name=f"batch-{batch_id}",
        input=json.dumps({
            "batch_id": batch_id,
            "s3_keys": s3_keys,
            "langsmith_trace_context": trace_ctx,
        }),
    )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "batch_id": batch_id,
            "langsmith_trace_context": trace_ctx,
        }),
    }
