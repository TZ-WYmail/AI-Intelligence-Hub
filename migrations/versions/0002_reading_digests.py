"""Persist reading state and daily briefings."""

import sqlalchemy as sa
from alembic import op

from app.models import UTCDateTime

revision = "0002"
down_revision = "0001"


def upgrade():
    op.add_column("articles", sa.Column("read_at", UTCDateTime(), nullable=True))
    op.add_column("articles", sa.Column("bookmarked", sa.Boolean(), nullable=False, server_default="0"))
    op.create_index("ix_articles_bookmarked", "articles", ["bookmarked"])
    op.create_table(
        "digests",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("period_start", UTCDateTime(), nullable=False),
        sa.Column("period_end", UTCDateTime(), nullable=False),
        sa.Column("generated_at", UTCDateTime(), nullable=False),
        sa.Column("article_count", sa.Integer(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.UniqueConstraint("period_start", "period_end"),
    )
    op.create_index("ix_digests_period_start", "digests", ["period_start"])


def downgrade():
    op.drop_table("digests")
    with op.batch_alter_table("articles") as batch:
        batch.drop_index("ix_articles_bookmarked")
        batch.drop_column("bookmarked")
        batch.drop_column("read_at")
