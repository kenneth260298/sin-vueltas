"""Store each chunk's section heading and the model that produced its embedding.

The model name lets the index re-embed only what a new provider or model needs;
a chunk has both an embedding and its model, or neither.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-06
"""

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None

UPGRADE_STATEMENTS = [
    "ALTER TABLE chunks ADD COLUMN heading text",
    "ALTER TABLE chunks ADD COLUMN embedding_model text",
    """
    ALTER TABLE chunks ADD CONSTRAINT chunks_embedding_has_model
        CHECK ((embedding IS NULL) = (embedding_model IS NULL))
    """,
]

DOWNGRADE_STATEMENTS = [
    "ALTER TABLE chunks DROP CONSTRAINT chunks_embedding_has_model",
    "ALTER TABLE chunks DROP COLUMN embedding_model",
    "ALTER TABLE chunks DROP COLUMN heading",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
