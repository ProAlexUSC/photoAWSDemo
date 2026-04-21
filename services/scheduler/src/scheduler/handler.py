import json
import os

import boto3
from common.batch_manager import PgBatchManager
from common.db import get_connection
from common.tracing import attach_aws_runtime_context, init_trace_id, traced_handler
from langfuse import get_client, observe, propagate_attributes


@observe(name="photo_pipeline")
def _run(batch_id: int, s3_keys: list[str], trace_id: str) -> dict:
    attach_aws_runtime_context()
    parent_obs_id = get_client().get_current_observation_id() or ""

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
        # Scheduler 自己的 obs_id；e2e 脚本跑 Worker docker 时作 LANGFUSE_PARENT_OBS_ID 透下去
        "langfuse_parent_observation_id": parent_obs_id,
    }


def handler(event, context):
    with traced_handler():
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

        # env 维度的 tag + session 前缀用于 Langfuse UI 区分本地/云端 trace
        env = os.environ.get("APP_ENV", "unknown")
        trace_id = init_trace_id(request_id)
        with propagate_attributes(
            user_id=str(user_id),
            session_id=f"{env}-batch-{batch_id}",
            tags=[f"env:{env}", "photo-pipeline"],
        ):
            result = _run(batch_id, s3_keys, trace_id, langfuse_trace_id=trace_id)

        return {"statusCode": 200, "body": json.dumps(result)}
