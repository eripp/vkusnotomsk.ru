"""provider_uuid на otp_codes (для внешнего верификатора i-dgtl)

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "otp_codes",
        sa.Column("provider_uuid", sa.String(length=64), nullable=True),
    )
    # code_hash больше не обязателен (для провайдерских кодов он пустой)
    op.alter_column("otp_codes", "code_hash", server_default="")


def downgrade() -> None:
    op.drop_column("otp_codes", "provider_uuid")
