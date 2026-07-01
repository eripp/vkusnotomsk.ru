"""роль у admin_users (admin / operator)

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "admin_users",
        sa.Column("role", sa.String(20), nullable=False, server_default="admin"),
    )


def downgrade() -> None:
    op.drop_column("admin_users", "role")
