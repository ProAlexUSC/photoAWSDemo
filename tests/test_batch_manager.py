import os
import psycopg2
import pytest
from common.batch_manager import PgBatchManager


@pytest.fixture(autouse=True)
def db_url():
    os.environ.setdefault("DATABASE_URL", "postgresql://dev:dev@localhost:5432/photo_pipeline")


@pytest.fixture
def conn():
    c = psycopg2.connect(os.environ["DATABASE_URL"])
    c.autocommit = False
    yield c
    c.rollback()
    c.close()


@pytest.fixture
def manager(conn):
    return PgBatchManager(conn)


def test_create_batch(manager, conn):
    batch_id = manager.create_batch("req-001", user_id=1, s3_keys=["a.jpg", "b.jpg"])
    assert batch_id is not None
    cur = conn.cursor()
    cur.execute("SELECT status, total FROM photo_batches WHERE batch_id = %s", (batch_id,))
    row = cur.fetchone()
    assert row == ("pending", 2)
    cur.execute("SELECT COUNT(*) FROM photos WHERE batch_id = %s", (batch_id,))
    assert cur.fetchone()[0] == 2


def test_create_batch_idempotent(manager):
    id1 = manager.create_batch("req-dup", user_id=1, s3_keys=["a.jpg"])
    id2 = manager.create_batch("req-dup", user_id=1, s3_keys=["a.jpg"])
    assert id1 == id2


def test_get_photo_id(manager):
    batch_id = manager.create_batch("req-002", user_id=1, s3_keys=["x.jpg"])
    photo_id = manager.get_photo_id(batch_id, "x.jpg")
    assert photo_id is not None


def test_insert_embedding(manager, conn):
    batch_id = manager.create_batch("req-003", user_id=1, s3_keys=["face.jpg"])
    photo_id = manager.get_photo_id(batch_id, "face.jpg")
    embedding = [0.1] * 512
    bbox = {"x": 10, "y": 20, "w": 100, "h": 120}
    manager.insert_embedding(photo_id, embedding, bbox)
    cur = conn.cursor()
    cur.execute("SELECT photo_id, bbox FROM face_embeddings WHERE photo_id = %s", (photo_id,))
    row = cur.fetchone()
    assert row[0] == photo_id
    assert row[1] == bbox


def test_mark_photo_complete(manager, conn):
    batch_id = manager.create_batch("req-004", user_id=1, s3_keys=["p.jpg"])
    photo_id = manager.get_photo_id(batch_id, "p.jpg")
    manager.mark_photo_complete(photo_id, face_count=3)
    cur = conn.cursor()
    cur.execute("SELECT status, face_count FROM photos WHERE photo_id = %s", (photo_id,))
    row = cur.fetchone()
    assert row == ("completed", 3)


def test_mark_batch_complete(manager, conn):
    batch_id = manager.create_batch("req-005", user_id=1, s3_keys=["a.jpg", "b.jpg"])
    for s3_key in ["a.jpg", "b.jpg"]:
        photo_id = manager.get_photo_id(batch_id, s3_key)
        manager.mark_photo_complete(photo_id, face_count=1)
    manager.mark_batch_complete(batch_id)
    cur = conn.cursor()
    cur.execute("SELECT status, completed FROM photo_batches WHERE batch_id = %s", (batch_id,))
    row = cur.fetchone()
    assert row == ("completed", 2)


def test_mark_batch_not_complete_if_pending(manager, conn):
    batch_id = manager.create_batch("req-006", user_id=1, s3_keys=["a.jpg", "b.jpg"])
    photo_id = manager.get_photo_id(batch_id, "a.jpg")
    manager.mark_photo_complete(photo_id, face_count=1)
    manager.mark_batch_complete(batch_id)
    cur = conn.cursor()
    cur.execute("SELECT status FROM photo_batches WHERE batch_id = %s", (batch_id,))
    assert cur.fetchone()[0] == "processing"
