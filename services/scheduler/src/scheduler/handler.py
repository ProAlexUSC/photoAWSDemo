import json
import os

import boto3
from common.batch_manager import PgBatchManager
from common.db import get_connection


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

    sfn = boto3.client("stepfunctions")
    sfn.start_execution(
        stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        name=f"batch-{batch_id}",
        input=json.dumps({"batch_id": batch_id, "s3_keys": s3_keys}),
    )

    return {
        "statusCode": 200,
        "body": json.dumps({"batch_id": batch_id}),
    }
