"""Add per-effect pill-buff counters for permanent combat buffs.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # JSON-encoded ``{effect_key: count}`` for permanent combat-buff pills
    # (buff_speed, buff_def, buff_sword_dmg, buff_element_*). Each consume
    # increments the relevant counter up to a per-key cap (default 20),
    # and ``compute_combat_stats`` reads the counts to apply permanent
    # stat bonuses. Stored as a string for SQLite portability.
    op.add_column(
        "players",
        sa.Column(
            "pill_buff_counts",
            sa.String(length=512),
            server_default="{}",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("players", "pill_buff_counts")
