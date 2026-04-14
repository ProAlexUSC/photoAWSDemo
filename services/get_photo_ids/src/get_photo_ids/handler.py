from common.db import get_connection


def handler(event, context):
    batch_id = event["batch_id"]
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT photo_id FROM photos WHERE batch_id = %s ORDER BY photo_id",
            (batch_id,),
        )
        photo_ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
    return {"batch_id": batch_id, "photo_ids": photo_ids}
