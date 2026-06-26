"""phone_verified на users + значение 'sms' в enum otpchannel

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Новое значение enum. ALTER TYPE ... ADD VALUE нельзя выполнять внутри
    #    транзакционного блока — переключаем соединение в autocommit.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE otpchannel ADD VALUE IF NOT EXISTS 'sms'")

    # 2) Флаг подтверждения телефона и канал, которым он подтверждён.
    op.add_column(
        "users",
        sa.Column("phone_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column("phone_verified_via", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "phone_verified_via")
    op.drop_column("users", "phone_verified")
    # Значение enum 'sms' не удаляем — PostgreSQL не поддерживает удаление
    # значений enum, а наличие лишнего лейбла безвредно.
