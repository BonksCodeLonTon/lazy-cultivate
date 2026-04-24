"""Initial schema — all core tables.

Revision ID: 0001
Revises:
Create Date: 2026-04-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── players ──────────────────────────────────────────────────────────────
    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discord_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        # Cultivation realms
        sa.Column("body_realm", sa.Integer(), server_default="0", nullable=False),
        sa.Column("body_level", sa.Integer(), server_default="1", nullable=False),
        sa.Column("qi_realm", sa.Integer(), server_default="0", nullable=False),
        sa.Column("qi_level", sa.Integer(), server_default="1", nullable=False),
        sa.Column("formation_realm", sa.Integer(), server_default="0", nullable=False),
        sa.Column("formation_level", sa.Integer(), server_default="1", nullable=False),
        # Constitution
        sa.Column("constitution_type", sa.String(512), server_default="ConstitutionVanTuong", nullable=False),
        sa.Column("dao_ti_unlocked", sa.Boolean(), server_default="false", nullable=False),
        # Currencies
        sa.Column("merit", sa.Integer(), server_default="0", nullable=False),
        sa.Column("karma_accum", sa.Integer(), server_default="0", nullable=False),
        sa.Column("karma_usable", sa.Integer(), server_default="0", nullable=False),
        sa.Column("primordial_stones", sa.Integer(), server_default="0", nullable=False),
        # Combat session
        sa.Column("hp_current", sa.Integer(), server_default="0", nullable=False),
        sa.Column("mp_current", sa.Integer(), server_default="0", nullable=False),
        # Formation & titles
        sa.Column("active_formation", sa.String(64), nullable=True),
        sa.Column("main_title", sa.String(64), nullable=True),
        sa.Column("sub_title", sa.String(64), nullable=True),
        sa.Column("evil_title", sa.String(64), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("discord_id"),
    )
    op.create_index("ix_players_discord_id", "players", ["discord_id"], unique=True)

    # ── turn_trackers ─────────────────────────────────────────────────────────
    op.create_table(
        "turn_trackers",
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("turns_today", sa.Integer(), server_default="0", nullable=False),
        sa.Column("bonus_turns_remaining", sa.Integer(), server_default="440", nullable=False),
        sa.Column("last_tick_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merit_bonus_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("player_id"),
    )

    # ── inventory ─────────────────────────────────────────────────────────────
    op.create_table(
        "inventory",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("grade", sa.SmallInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "item_key", "grade", name="uq_inventory_player_item_grade"),
    )
    op.create_index("ix_inventory_player_id", "inventory", ["player_id"])

    # ── character_skills ──────────────────────────────────────────────────────
    op.create_table(
        "character_skills",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("skill_key", sa.String(64), nullable=False),
        sa.Column("slot_index", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "slot_index", name="uq_skill_player_slot"),
    )
    op.create_index("ix_char_skills_player_id", "character_skills", ["player_id"])

    # ── character_artifacts ───────────────────────────────────────────────────
    op.create_table(
        "character_artifacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("slot", sa.String(16), nullable=False),
        sa.Column("artifact_key", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "slot", name="uq_artifact_player_slot"),
    )
    op.create_index("ix_char_artifacts_player_id", "character_artifacts", ["player_id"])

    # ── character_formations ──────────────────────────────────────────────────
    op.create_table(
        "character_formations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("formation_key", sa.String(64), nullable=False),
        sa.Column("formation_realm", sa.Integer(), server_default="0", nullable=False),
        sa.Column("formation_level", sa.Integer(), server_default="1", nullable=False),
        sa.Column("mastery", sa.String(16), nullable=True),
        sa.Column("gem_slots", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("is_locked", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "formation_key", name="uq_formation_player_key"),
    )
    op.create_index("ix_char_formations_player_id", "character_formations", ["player_id"])

    # ── market_listings ───────────────────────────────────────────────────────
    op.create_table(
        "market_listings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("seller_id", sa.Integer(), nullable=False),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("grade", sa.SmallInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("shop_ref_price", sa.Integer(), nullable=False),
        sa.Column("currency_type", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["seller_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_market_seller_id", "market_listings", ["seller_id"])
    op.create_index("ix_market_grade", "market_listings", ["grade"])
    op.create_index("ix_market_item_key", "market_listings", ["item_key"])
    op.create_index("ix_market_expires_at", "market_listings", ["expires_at"])


def downgrade() -> None:
    op.drop_table("market_listings")
    op.drop_table("character_formations")
    op.drop_table("character_artifacts")
    op.drop_table("character_skills")
    op.drop_table("inventory")
    op.drop_table("turn_trackers")
    op.drop_table("players")
