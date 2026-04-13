import json
from psycopg2.extensions import connection as PgConnection


class PgBatchManager:
    def __init__(self, conn: PgConnection):
        self.conn = conn

    def create_batch(self, request_id: str, user_id: int, s3_keys: list[str]) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT batch_id FROM photo_batches WHERE request_id = %s",
            (request_id,),
        )
        existing = cur.fetchone()
        if existing:
            return existing[0]

        cur.execute(
            """INSERT INTO photo_batches (request_id, user_id, total, status)
               VALUES (%s, %s, %s, 'pending')
               ON CONFLICT (request_id) DO NOTHING
               RETURNING batch_id""",
            (request_id, user_id, len(s3_keys)),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "SELECT batch_id FROM photo_batches WHERE request_id = %s",
                (request_id,),
            )
            row = cur.fetchone()
        batch_id = row[0]

        for s3_key in s3_keys:
            cur.execute(
                """INSERT INTO photos (batch_id, s3_key, status)
                   VALUES (%s, %s, 'pending')""",
                (batch_id, s3_key),
            )
        return batch_id

    def get_photo_id(self, batch_id: int, s3_key: str) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT photo_id FROM photos WHERE batch_id = %s AND s3_key = %s",
            (batch_id, s3_key),
        )
        return cur.fetchone()[0]

    def insert_embedding(self, photo_id: int, embedding: list[float], bbox: dict) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO face_embeddings (photo_id, embedding, bbox)
               VALUES (%s, %s::vector, %s::jsonb)""",
            (photo_id, str(embedding), json.dumps(bbox)),
        )

    def mark_photo_complete(self, photo_id: int, face_count: int) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """UPDATE photos SET status = 'completed', face_count = %s
               WHERE photo_id = %s""",
            (face_count, photo_id),
        )

    def mark_batch_complete(self, batch_id: int) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM photos WHERE batch_id = %s AND status = 'completed'",
            (batch_id,),
        )
        completed = cur.fetchone()[0]

        cur.execute(
            "SELECT total FROM photo_batches WHERE batch_id = %s",
            (batch_id,),
        )
        total = cur.fetchone()[0]

        if completed >= total:
            cur.execute(
                """UPDATE photo_batches SET status = 'completed', completed = %s
                   WHERE batch_id = %s""",
                (completed, batch_id),
            )
        else:
            cur.execute(
                """UPDATE photo_batches SET status = 'processing', completed = %s
                   WHERE batch_id = %s""",
                (completed, batch_id),
            )
