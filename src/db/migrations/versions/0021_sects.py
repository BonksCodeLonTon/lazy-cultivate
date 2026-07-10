"""Tông Môn (sect) core schema — sects, members, facilities, applications, logs, cooldowns.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sects",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("tag", sa.String(length=8), nullable=False, unique=True),
        sa.Column("leader_player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("exp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("funds", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("announcement", sa.String(length=500), nullable=True),
        sa.Column("emblem", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # Case-insensitive unique sect name.
    op.create_index("uq_sects_name_lower", "sects", [sa.text("lower(name)")], unique=True)

    op.create_table(
        "sect_members",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("sect_id", sa.Integer(), sa.ForeignKey("sects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rank", sa.String(length=16), nullable=False, server_default="de_tu"),
        sa.Column("contribution_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("contribution_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("donated_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("donation_date", sa.Date(), nullable=True),
        sa.Column("last_checkin_date", sa.Date(), nullable=True),
        sa.Column("mission_progress", sa.String(length=512), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("player_id", name="uq_sect_members_player"),
    )
    op.create_index("ix_sect_members_sect", "sect_members", ["sect_id"])

    op.create_table(
        "sect_facilities",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("sect_id", sa.Integer(), sa.ForeignKey("sects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("facility_key", sa.String(length=32), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("sect_id", "facility_key", name="uq_sect_facility"),
    )

    op.create_table(
        "sect_applications",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("sect_id", sa.Integer(), sa.ForeignKey("sects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("sect_id", "player_id", name="uq_sect_application"),
    )
    op.create_index("ix_sect_applications_player", "sect_applications", ["player_id"])

    op.create_table(
        "sect_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("sect_id", sa.Integer(), sa.ForeignKey("sects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_player_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sect_logs_sect_created", "sect_logs", ["sect_id", "created_at"])

    op.create_table(
        "sect_cooldowns",
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("rejoin_after", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("sect_cooldowns")
    op.drop_index("ix_sect_logs_sect_created", table_name="sect_logs")
    op.drop_table("sect_logs")
    op.drop_index("ix_sect_applications_player", table_name="sect_applications")
    op.drop_table("sect_applications")
    op.drop_table("sect_facilities")
    op.drop_index("ix_sect_members_sect", table_name="sect_members")
    op.drop_table("sect_members")
    op.drop_index("uq_sects_name_lower", table_name="sects")
    op.drop_table("sects")
