"""имя клиента в заказе (orders.customer_name)

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("customer_name", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "customer_name")
