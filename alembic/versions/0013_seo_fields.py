"""SEO-поля у товаров и категорий (meta_title, meta_description)

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("products", "categories"):
        op.add_column(table, sa.Column("meta_title", sa.String(255), nullable=True))
        op.add_column(table, sa.Column("meta_description", sa.String(500), nullable=True))


def downgrade() -> None:
    for table in ("products", "categories"):
        op.drop_column(table, "meta_description")
        op.drop_column(table, "meta_title")
