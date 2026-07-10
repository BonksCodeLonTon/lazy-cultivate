"""Sect shop weekly-limit purchase ledger (Tàng Bảo Các).

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sect_shop_purchases",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("sect_id", sa.Integer(), sa.ForeignKey("sects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_key", sa.String(length=64), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("week_key", sa.String(length=12), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "sect_id", "player_id", "item_key", "grade", "week_key",
            name="uq_sect_shop_purchase",
        ),
    )
    op.create_index(
        "ix_sect_shop_purchases_player", "sect_shop_purchases", ["player_id", "week_key"]
    )


def downgrade() -> None:
    op.drop_index("ix_sect_shop_purchases_player", table_name="sect_shop_purchases")
    op.drop_table("sect_shop_purchases")
