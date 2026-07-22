"""публичный номер заказа order_number (YYMMDD-XXXX)

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("order_number", sa.String(20), nullable=True))
    # Бэкфилл существующих заказов: YYMMDD (из created_at) + 4 цифры от id,
    # с добавлением id для гарантии уникальности в пределах дня.
    op.execute("""
        UPDATE orders
        SET order_number = to_char(created_at, 'YYMMDD') || '-' ||
                           lpad((1000 + (id % 9000))::text, 4, '0')
        WHERE order_number IS NULL
    """)
    op.create_index("ix_orders_order_number", "orders", ["order_number"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_orders_order_number", table_name="orders")
    op.drop_column("orders", "order_number")
