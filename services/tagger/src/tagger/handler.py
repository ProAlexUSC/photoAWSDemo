import json

from common.db import get_connection
from langsmith import traceable


@traceable(name="stage2_tag_photo")
def handler(event, context):
    photo_id = event["photo_id"]
    tags = {"scene": "outdoor", "objects": ["person", "tree"], "mood": "happy"}
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE photos SET tags = %s WHERE photo_id = %s",
            (json.dumps(tags), photo_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"photo_id": photo_id, "status": "tagged"}
