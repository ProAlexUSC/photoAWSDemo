import json

from common.db import get_connection
from langsmith import traceable


@traceable(name="stage3_vlm_extract")
def handler(event, context):
    photo_id = event["photo_id"]
    vlm_result = {
        "description": "A person standing in a park",
        "entities": [{"type": "person", "name": "unknown"}],
        "location_guess": "urban park",
    }
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE photos SET vlm_result = %s WHERE photo_id = %s",
            (json.dumps(vlm_result), photo_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"photo_id": photo_id, "status": "extracted"}
