"""Persist the player's last-picked furnace for sticky alchemy selection.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Sticky furnace selection — the recipe detail view reads this on open
    # and defaults the dropdown to the player's last pick (when the key is
    # still owned and qualifies for the recipe's tier). Nullable: brand-new
    # players and pre-migration rows simply fall back to the auto-pick.
    op.add_column(
        "players",
        sa.Column("preferred_furnace_key", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("players", "preferred_furnace_key")
