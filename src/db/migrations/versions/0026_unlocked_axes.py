"""Axis lock (season-2) — players.unlocked_axes + progress-based backfill.

New rule: the cultivation axis is picked at registration and locked; extra
axes unlock only by consuming Đạo Nguyên Thạch. Existing players keep every
axis they have actually cultivated (any realm/level/xp progress) plus their
current active axis — nobody loses access to progress they already earned.

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column(
            "unlocked_axes",
            sa.String(length=32),
            nullable=False,
            server_default="qi",
        ),
    )
    # Grandfather clause: start from the active axis, then append any axis
    # with real progress. Canonical order (body,qi,formation) is re-imposed
    # by the app-layer parser, so append order here doesn't matter.
    op.execute("UPDATE players SET unlocked_axes = active_axis")
    op.execute(
        "UPDATE players SET unlocked_axes = unlocked_axes || ',body' "
        "WHERE position('body' in unlocked_axes) = 0 "
        "AND (body_realm > 0 OR body_level > 1 OR body_xp > 0)"
    )
    op.execute(
        "UPDATE players SET unlocked_axes = unlocked_axes || ',qi' "
        "WHERE position('qi' in unlocked_axes) = 0 "
        "AND (qi_realm > 0 OR qi_level > 1 OR qi_xp > 0)"
    )
    op.execute(
        "UPDATE players SET unlocked_axes = unlocked_axes || ',formation' "
        "WHERE position('formation' in unlocked_axes) = 0 "
        "AND (formation_realm > 0 OR formation_level > 1 OR formation_xp > 0)"
    )


def downgrade() -> None:
    op.drop_column("players", "unlocked_axes")
