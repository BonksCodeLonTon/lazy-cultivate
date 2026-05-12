"""Add constitution_tracker column to players.

Records every Thể Chất the player has ever activated so they can equip and
unequip from a permanent unlocks list without re-paying the activation cost.
Existing rows are backfilled from ``constitution_type`` (the currently
equipped list) since those entries were already unlocked by activation.

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("players")}
    if "constitution_tracker" in existing_cols:
        return
    op.add_column(
        "players",
        sa.Column(
            "constitution_tracker",
            sa.String(length=8192),
            server_default="ConstitutionPhamThe",
            nullable=False,
        ),
    )
    # Backfill: anything currently equipped was already unlocked, so seed
    # the tracker from constitution_type. Use COALESCE to handle any NULL
    # rows defensively even though the column is NOT NULL by schema.
    op.execute(
        "UPDATE players "
        "SET constitution_tracker = COALESCE(constitution_type, 'ConstitutionPhamThe')"
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("players")}
    if "constitution_tracker" in existing_cols:
        op.drop_column("players", "constitution_tracker")
