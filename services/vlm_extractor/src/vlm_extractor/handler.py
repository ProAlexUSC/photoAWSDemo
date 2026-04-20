import json

from common.db import get_connection
from common.tracing import run_traced
from langfuse import get_client, observe


# 当前是 mock：用 @observe(name=...) 生成普通 span。
#
# 真接入 VLM（如 OpenAI vision / GPT-4o）时推荐升级为 as_type="generation"，
# Langfuse UI 会在此 span 上解锁 LLM 专属字段：model、token 用量、成本估算、延迟分布。
# 示例（接入时改成）：
#
#   @observe(name="stage3_vlm_extract", as_type="generation")
#   def _vlm_extract(photo_id):
#       response = openai_client.chat.completions.create(
#           model="gpt-4o-mini",
#           messages=[...]
#       )
#       get_client().update_current_generation(
#           model="gpt-4o-mini",
#           model_parameters={"temperature": 0.2},
#           input=[...],
#           output=response.choices[0].message.content,
#           usage_details={
#               "input": response.usage.prompt_tokens,
#               "output": response.usage.completion_tokens,
#           },
#       )
#       return ...
@observe(name="stage3_vlm_extract")
def _vlm_extract(photo_id):
    vlm_result = {
        "description": "A person standing in a park",
        "entities": [{"type": "person", "name": "unknown"}],
        "location_guess": "urban park",
    }
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE photos SET vlm_result = %s WHERE photo_id = %s RETURNING s3_key",
            (json.dumps(vlm_result), photo_id),
        )
        row = cur.fetchone()
        s3_key = row[0] if row else None
        conn.commit()
    finally:
        conn.close()

    get_client().update_current_span(
        input={"photo_id": photo_id, "s3_key": s3_key},
        metadata={"s3_key": s3_key},
    )
    return {"photo_id": photo_id, "status": "extracted", "s3_key": s3_key}


def handler(event, context):
    return run_traced(_vlm_extract, event, event["photo_id"])
