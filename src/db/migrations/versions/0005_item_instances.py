"""Replace player_equipment with item_instances (affix-based system).

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-15
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop old simple equipment table
    op.drop_index("ix_player_equipment_player_id", table_name="player_equipment")
    op.drop_table("player_equipment")

    # Create new instance-based table.
    # `slot` is the item's equipment slot type — always set, not used as unique key.
    # The repository enforces "one equipped item per slot" in application code.
    op.create_table(
        "item_instances",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("location", sa.String(16), nullable=False, server_default="bag"),
        sa.Column("slot", sa.String(16), nullable=False),
        sa.Column("base_key", sa.String(64), nullable=True),
        sa.Column("unique_key", sa.String(64), nullable=True),
        sa.Column(
            "affixes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "computed_stats",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("grade", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("display_name", sa.String(128), nullable=False, server_default=""),
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
    )
    op.create_index("ix_item_inst_player_id", "item_instances", ["player_id"])
    op.create_index("ix_item_inst_location", "item_instances", ["player_id", "location"])


def downgrade() -> None:
    op.drop_index("ix_item_inst_location", table_name="item_instances")
    op.drop_index("ix_item_inst_player_id", table_name="item_instances")
    op.drop_table("item_instances")

    op.create_table(
        "player_equipment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("slot", sa.String(16), nullable=False),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("grade", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "slot", name="uq_equipment_player_slot"),
    )
    op.create_index("ix_player_equipment_player_id", "player_equipment", ["player_id"])
