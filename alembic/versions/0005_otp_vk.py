"""значение 'vk' в enum otpchannel

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE нельзя выполнять внутри транзакционного блока.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE otpchannel ADD VALUE IF NOT EXISTS 'vk'")


def downgrade() -> None:
    # PostgreSQL не поддерживает удаление значений enum; лишний лейбл безвреден.
    pass
