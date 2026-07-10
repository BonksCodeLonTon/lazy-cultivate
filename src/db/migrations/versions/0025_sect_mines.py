"""Đại Chiến Khoáng Mạch — mine occupancy, wars, and per-player war attempts.

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sect_mines",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("mine_key", sa.String(length=32), nullable=False, unique=True),
        sa.Column("occupier_sect_id", sa.Integer(), sa.ForeignKey("sects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("occupied_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_payout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shield_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "mine_wars",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("mine_key", sa.String(length=32), nullable=False),
        sa.Column("attacker_sect_id", sa.Integer(), sa.ForeignKey("sects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("defender_sect_id", sa.Integer(), sa.ForeignKey("sects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attacker_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("defender_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("garrison_hp_max", sa.BigInteger(), nullable=True),
        sa.Column("garrison_hp_current", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("winner_sect_id", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mine_wars_status", "mine_wars", ["status"])
    op.create_index("ix_mine_wars_mine_status", "mine_wars", ["mine_key", "status"])

    op.create_table(
        "mine_war_attacks",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("war_id", sa.Integer(), sa.ForeignKey("mine_wars.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("attempts_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("war_id", "player_id", name="uq_mine_war_attack"),
    )
    op.create_index("ix_mine_war_attacks_war", "mine_war_attacks", ["war_id"])


def downgrade() -> None:
    op.drop_index("ix_mine_war_attacks_war", table_name="mine_war_attacks")
    op.drop_table("mine_war_attacks")
    op.drop_index("ix_mine_wars_mine_status", table_name="mine_wars")
    op.drop_index("ix_mine_wars_status", table_name="mine_wars")
    op.drop_table("mine_wars")
    op.drop_table("sect_mines")
