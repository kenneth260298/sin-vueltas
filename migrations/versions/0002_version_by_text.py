"""Version documents by their extracted text instead of their raw bytes.

Pages that embed tokens, view state or dates change on every visit even when the
content people read is the same. From this revision on, a new version is created
only when the extracted text changes.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-06
"""

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE_STATEMENTS = [
    "ALTER TABLE document_versions ADD COLUMN text_hash text",
    # Versions recorded before this revision have no extracted text. Reusing their
    # content hash keeps them unique so the column can be NOT NULL.
    "UPDATE document_versions SET text_hash = content_hash WHERE text_hash IS NULL",
    "ALTER TABLE document_versions ALTER COLUMN text_hash SET NOT NULL",
    """
    ALTER TABLE document_versions
        DROP CONSTRAINT document_versions_document_id_content_hash_key
    """,
    """
    ALTER TABLE document_versions
        ADD CONSTRAINT document_versions_document_id_text_hash_key
        UNIQUE (document_id, text_hash)
    """,
]

DOWNGRADE_STATEMENTS = [
    """
    ALTER TABLE document_versions
        DROP CONSTRAINT document_versions_document_id_text_hash_key
    """,
    """
    ALTER TABLE document_versions
        ADD CONSTRAINT document_versions_document_id_content_hash_key
        UNIQUE (document_id, content_hash)
    """,
    "ALTER TABLE document_versions DROP COLUMN text_hash",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
