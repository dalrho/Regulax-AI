# SOP preparation and evidence retrieval

SOP preparation is a backend service. It creates location-verified paragraph chunks, embeds them with the configured sentence-transformers model, and sets a version to `ready` only after all embeddings and chunk rows are saved successfully.

## Configuration

The default model is `BAAI/bge-base-en-v1.5` with 768 dimensions and a maximum chunk size of 1,800 characters. `SOP_EMBEDDING_MODEL` and `SOP_CHUNK_MAX_CHARS` can be changed; a model or chunk-size change produces a new preparation key and keeps earlier chunks for existing references. The vector column is fixed at 768 dimensions, so a different embedding dimension requires a schema migration before use.

Install the Python dependencies in `requirements.txt`, apply migrations, and run preparation as a workspace member:

```sh
python manage.py migrate
python manage.py prepare_sop_versions 12 13 --user-id 7
python manage.py search_sop_evidence "sanitation verification" --user-id 7 --workspace-id 3 --version-ids 12 13
```

The command output includes chunk IDs, SOP/version IDs, source character ranges, matched paths, and retrieved passage text. `retrieve_evidence()` is the application service entry point. Callers must pass the exact selected version IDs; the service rejects IDs outside the member's workspace and never broadens that selection.

## Storage behavior

PostgreSQL stores embeddings in native `vector(768)` columns and uses pgvector cosine distance for semantic ranking. The migration enables the `vector` extension. Local SQLite stores vector text in a text column and computes cosine similarity in Python, so the local fallback remains usable without a PostgreSQL server. Keyword ranking runs alongside semantic ranking; the two ranked lists are combined with reciprocal-rank fusion. Neighboring chunks from the same selected SOP version are returned as one source slice with exact offsets.

Canonical chunk rows are immutable and retained. Repeating preparation with the same source, model, chunker version, and chunk size reuses those row IDs. Changing the preparation inputs creates a different preparation key; the prior set remains stored while the version points at the newly completed set. On an error, the version is marked `failed`, an actionable message is stored, and no incomplete set becomes active.

## Verification limits

Retrieval/provider tests use `DeterministicEmbeddingProvider`; those vectors verify application behavior and are not production embeddings. Live use of `BAAI/bge-base-en-v1.5` is unverified in this environment: `sentence-transformers` is not installed here and the model has not been downloaded or executed. PostgreSQL/pgvector SQL is implemented for the configured Supabase PostgreSQL path, but could not be exercised against the remote database in this environment. Local SQLite verification does not prove the remote extension is installed or that its migration can run with the production database role.
