"""Record where each version came from: the crawler or a manual capture.

Some official sites reject bots, so a person saves the page from a browser and
captures it. Reviewers and audits must be able to tell the two apart.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-06
"""

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE document_versions
            ADD COLUMN origin text NOT NULL DEFAULT 'crawler'
            CONSTRAINT document_versions_origin_check CHECK (origin IN ('crawler', 'manual'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE document_versions DROP COLUMN origin")
