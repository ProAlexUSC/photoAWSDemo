-- migrate:down

DROP TABLE IF EXISTS face_embeddings;
DROP TABLE IF EXISTS photos;
DROP TABLE IF EXISTS photo_batches;
DROP EXTENSION IF EXISTS vector;
