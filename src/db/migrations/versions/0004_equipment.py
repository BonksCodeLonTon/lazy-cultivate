"""Add player_equipment table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-15
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "player_equipment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("slot", sa.String(16), nullable=False),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("grade", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "slot", name="uq_equipment_player_slot"),
    )
    op.create_index("ix_player_equipment_player_id", "player_equipment", ["player_id"])


def downgrade() -> None:
    op.drop_index("ix_player_equipment_player_id", table_name="player_equipment")
    op.drop_table("player_equipment")
