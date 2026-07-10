"""Trấn Sơn Thú — weekly per-sect boss instances + participation tallies.

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sect_boss_instances",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("sect_id", sa.Integer(), sa.ForeignKey("sects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("boss_key", sa.String(length=64), nullable=False),
        sa.Column("week_key", sa.String(length=12), nullable=False),
        sa.Column("hp_max", sa.BigInteger(), nullable=False),
        sa.Column("hp_current", sa.BigInteger(), nullable=False),
        sa.Column("spawned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("killed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finisher_player_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rewards_distributed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("sect_id", "week_key", name="uq_sect_boss_week"),
    )
    op.create_index("ix_sect_boss_active", "sect_boss_instances", ["is_active"])

    op.create_table(
        "sect_boss_participations",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("boss_instance_id", sa.Integer(), sa.ForeignKey("sect_boss_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("damage_dealt", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("attack_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reward_claimed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("boss_instance_id", "player_id", name="uq_sect_boss_part"),
    )
    op.create_index("ix_sect_boss_part_player", "sect_boss_participations", ["player_id"])


def downgrade() -> None:
    op.drop_index("ix_sect_boss_part_player", table_name="sect_boss_participations")
    op.drop_table("sect_boss_participations")
    op.drop_index("ix_sect_boss_active", table_name="sect_boss_instances")
    op.drop_table("sect_boss_instances")
