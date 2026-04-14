import json

from common.db import get_connection
from common.tracing import parent_trace_from
from langsmith import traceable


@traceable(name="stage3_vlm_extract")
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
            "UPDATE photos SET vlm_result = %s WHERE photo_id = %s",
            (json.dumps(vlm_result), photo_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"photo_id": photo_id, "status": "extracted"}


def handler(event, context):
    with parent_trace_from(event):
        return _vlm_extract(event["photo_id"])
