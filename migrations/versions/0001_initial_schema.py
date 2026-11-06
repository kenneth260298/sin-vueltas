"""Create the initial schema.

Tables:
    procedures         Procedures listed in data/sources.yaml.
    documents          Each fetchable page or PDF, stored once per URL (without #fragment).
    sources            Each source entry in sources.yaml; links a procedure to a document.
    document_versions  Every distinct download of a document, with its review status.
    chunks             Searchable fragments of approved versions (vector + Spanish full text).
    requirements       Structured requirements per procedure and applicant profile.

Revision ID: 0001
Revises:
Create Date: 2026-11-06
"""

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

# 768 dimensions keeps the embeddings provider open: both Gemini and OpenAI
# embedding models can return vectors of this size.
EMBEDDING_DIMENSIONS = 768

UPGRADE_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE procedures (
        id                text PRIMARY KEY,
        name              text NOT NULL,
        category          text NOT NULL,
        institution       text NOT NULL,
        varies_by_profile boolean NOT NULL DEFAULT false,
        created_at        timestamptz NOT NULL DEFAULT now(),
        updated_at        timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE documents (
        id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        url              text NOT NULL UNIQUE,
        format           text NOT NULL CHECK (format IN ('html', 'pdf')),
        etag             text,
        last_modified    text,
        last_checked_at  timestamptz,
        last_status_code integer,
        last_error       text,
        created_at       timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE sources (
        id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        procedure_id text NOT NULL REFERENCES procedures (id) ON DELETE CASCADE,
        document_id  bigint REFERENCES documents (id) ON DELETE SET NULL,
        url          text NOT NULL,
        format       text NOT NULL CHECK (format IN ('html', 'pdf', 'online_service')),
        status       text NOT NULL CHECK (status IN ('ok', 'review')),
        note         text,
        created_at   timestamptz NOT NULL DEFAULT now(),
        UNIQUE (procedure_id, url),
        -- Online services are cited but never crawled, so they have no document.
        CONSTRAINT sources_online_service_has_no_document
            CHECK ((format = 'online_service') = (document_id IS NULL))
    )
    """,
    "CREATE INDEX sources_document_id_idx ON sources (document_id)",
    """
    CREATE TABLE document_versions (
        id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        document_id    bigint NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
        content_hash   text NOT NULL,
        storage_path   text NOT NULL,
        extracted_text text,
        fetched_at     timestamptz NOT NULL DEFAULT now(),
        review_status  text NOT NULL DEFAULT 'pending'
            CHECK (review_status IN ('pending', 'approved', 'rejected', 'superseded')),
        reviewed_at    timestamptz,
        reviewed_by    text,
        UNIQUE (document_id, content_hash)
    )
    """,
    # At most one approved version per document: the one the assistant answers from.
    """
    CREATE UNIQUE INDEX document_versions_one_approved_idx
        ON document_versions (document_id)
        WHERE review_status = 'approved'
    """,
    f"""
    CREATE TABLE chunks (
        id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        version_id bigint NOT NULL REFERENCES document_versions (id) ON DELETE CASCADE,
        position   integer NOT NULL,
        content    text NOT NULL,
        embedding  vector({EMBEDDING_DIMENSIONS}),
        search_tsv tsvector GENERATED ALWAYS AS (to_tsvector('spanish', content)) STORED,
        UNIQUE (version_id, position)
    )
    """,
    "CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX chunks_search_tsv_idx ON chunks USING gin (search_tsv)",
    """
    CREATE TABLE requirements (
        id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        procedure_id      text NOT NULL REFERENCES procedures (id) ON DELETE CASCADE,
        profile           text NOT NULL DEFAULT 'any'
            CHECK (profile IN ('any', 'salaried', 'self_employed', 'retired', 'minor')),
        position          integer NOT NULL,
        description       text NOT NULL,
        source_version_id bigint REFERENCES document_versions (id) ON DELETE SET NULL,
        review_status     text NOT NULL DEFAULT 'pending'
            CHECK (review_status IN ('pending', 'approved', 'rejected', 'superseded')),
        created_at        timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX requirements_procedure_profile_idx ON requirements (procedure_id, profile)",
    "CREATE INDEX requirements_source_version_id_idx ON requirements (source_version_id)",
]

# Drop in reverse dependency order. The vector extension stays installed.
DOWNGRADE_STATEMENTS = [
    "DROP TABLE requirements",
    "DROP TABLE chunks",
    "DROP TABLE document_versions",
    "DROP TABLE sources",
    "DROP TABLE documents",
    "DROP TABLE procedures",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
