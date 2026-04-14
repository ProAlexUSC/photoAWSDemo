import json
import os

import boto3
from common.batch_manager import PgBatchManager
from common.config import is_local
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

    if not is_local():
        batch_client = boto3.client("batch")
        batch_client.submit_job(
            jobName=f"photo-batch-{batch_id}",
            jobQueue=os.environ["GPU_JOB_QUEUE"],
            jobDefinition=os.environ["JOB_DEFINITION"],
            containerOverrides={
                "environment": [
                    {"name": "BATCH_ID", "value": str(batch_id)},
                    {"name": "S3_KEYS", "value": json.dumps(s3_keys)},
                ]
            },
        )

    return {
        "statusCode": 200,
        "body": json.dumps({"batch_id": batch_id}),
    }
