"""Store how each document's text is extracted.

main: only the main content, chosen by trafilatura (the default).
full: all visible text without page chrome, for pages whose content sits in
      collapsed sections (FAQ accordions) that main-content detection drops.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06
"""

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE documents
            ADD COLUMN extraction text NOT NULL DEFAULT 'main'
            CONSTRAINT documents_extraction_check CHECK (extraction IN ('main', 'full'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE documents DROP COLUMN extraction")
