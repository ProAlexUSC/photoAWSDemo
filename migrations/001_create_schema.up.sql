-- migrate:up

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE photo_batches (
    batch_id    SERIAL PRIMARY KEY,
    request_id  TEXT UNIQUE,
    user_id     INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    completed   INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE photos (
    photo_id    SERIAL PRIMARY KEY,
    batch_id    INTEGER NOT NULL REFERENCES photo_batches(batch_id),
    s3_key      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    face_count  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE face_embeddings (
    face_id     SERIAL PRIMARY KEY,
    photo_id    INTEGER NOT NULL REFERENCES photos(photo_id),
    embedding   vector(512) NOT NULL,
    bbox        JSONB,
    cluster_id  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_face_embedding_hnsw
    ON face_embeddings USING hnsw (embedding vector_cosine_ops);
