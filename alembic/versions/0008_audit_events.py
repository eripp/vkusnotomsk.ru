"""аудит важных действий (audit_events)

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ip", sa.String(45), nullable=False, index=True),
        sa.Column("action", sa.String(40), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("detail", sa.String(300), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("path", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
