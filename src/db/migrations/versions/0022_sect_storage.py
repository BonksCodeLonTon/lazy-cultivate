"""Kho Tàng Tông Môn — shared sect storage (stock slots + withdrawal requests).

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sect_storage_items",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("sect_id", sa.Integer(), sa.ForeignKey("sects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_key", sa.String(length=64), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("sect_id", "item_key", "grade", name="uq_sect_storage_slot"),
    )
    op.create_index("ix_sect_storage_sect", "sect_storage_items", ["sect_id"])

    op.create_table(
        "sect_storage_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("sect_id", sa.Integer(), sa.ForeignKey("sects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requester_player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_key", sa.String(length=64), nullable=False),
        sa.Column("grade", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("reviewed_by_player_id", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_sect_storage_req_sect_status", "sect_storage_requests", ["sect_id", "status"]
    )
    op.create_index(
        "ix_sect_storage_req_player", "sect_storage_requests", ["requester_player_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_sect_storage_req_player", table_name="sect_storage_requests")
    op.drop_index("ix_sect_storage_req_sect_status", table_name="sect_storage_requests")
    op.drop_table("sect_storage_requests")
    op.drop_index("ix_sect_storage_sect", table_name="sect_storage_items")
    op.drop_table("sect_storage_items")
