import json
import os

import boto3
from common.batch_manager import PgBatchManager
from common.db import get_connection
from common.tracing import attach_aws_runtime_context, init_trace_id, traced_handler
from langfuse import get_client, observe, propagate_attributes


@observe(name="photo_pipeline")
def _run(batch_id: int, s3_keys: list[str], trace_id: str, env: str) -> dict:
    client = get_client()
    # 把 root span 重命名为 {env}-batch-{id}，trace.name 也会跟着变，
    # 避开"trace 外层 + root span 内层都叫 photo_pipeline"的 UI 视觉重复
    client.update_current_span(name=f"{env}-batch-{batch_id}")
    # trace 容器级别的 Input/Output（Langfuse UI Preview tab 直接显示），
    # 否则 trace 外层 Input: undefined / Output: undefined
    client.set_current_trace_io(input={"batch_id": batch_id, "s3_keys": s3_keys})
    attach_aws_runtime_context()  # 挂 aws.request_id / log_url / app.env 到 root span metadata
    parent_obs_id = client.get_current_observation_id() or ""

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
    result = {
        "batch_id": batch_id,
        "execution_arn": exe["executionArn"],
        "langfuse_trace_id": trace_id,
        # Scheduler 自己的 obs_id；e2e 脚本跑 Worker docker 时作 LANGFUSE_PARENT_OBS_ID 透下去
        "langfuse_parent_observation_id": parent_obs_id,
    }
    client.set_current_trace_io(output=result)
    return result


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
            result = _run(batch_id, s3_keys, trace_id, env, langfuse_trace_id=trace_id)

        return {"statusCode": 200, "body": json.dumps(result)}
