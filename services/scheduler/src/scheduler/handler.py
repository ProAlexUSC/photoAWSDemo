import json
import os

import boto3
from common.batch_manager import PgBatchManager
from common.db import get_connection
from common.tracing import init_trace_id, traced_handler
from langfuse import get_client, observe, propagate_attributes


@observe(name="photo_pipeline")
def _run(batch_id: int, s3_keys: list[str]) -> dict:
    lf = get_client()
    parent_obs_id = lf.get_current_observation_id() or ""
    trace_id = lf.get_current_trace_id() or ""

    sfn = boto3.client("stepfunctions")
    exe = sfn.start_execution(
        stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        name=f"batch-{batch_id}",
        input=json.dumps(
            {
                "batch_id": batch_id,
                "s3_keys": s3_keys,
                "langfuse_trace_id": trace_id,
                "langfuse_parent_observation_id": parent_obs_id,
            }
        ),
    )

    return {
        "batch_id": batch_id,
        "execution_arn": exe["executionArn"],
        "langfuse_trace_id": trace_id,
    }


def handler(event, context):
    with traced_handler():
        request_id = event["request_id"]
        user_id = event["user_id"]
        s3_keys = event["s3_keys"]

        # 先 create_batch（trace 之外，DB I/O 不值得单独一个 span）
        conn = get_connection()
        try:
            manager = PgBatchManager(conn)
            batch_id = manager.create_batch(request_id, user_id, s3_keys)
            conn.commit()
        finally:
            conn.close()

        # 在 @observe 外挂 trace 级属性，_run 里的 root span 创建时即继承 user_id/session_id/tags
        trace_id = init_trace_id(request_id)
        with propagate_attributes(
            user_id=str(user_id),
            session_id=f"batch-{batch_id}",
            tags=["photo-pipeline"],
        ):
            result = _run(batch_id, s3_keys, langfuse_trace_id=trace_id)

        return {"statusCode": 200, "body": json.dumps(result)}
