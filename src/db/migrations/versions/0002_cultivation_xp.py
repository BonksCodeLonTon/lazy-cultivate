"""Add cultivation XP columns and active_axis to players table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("players", sa.Column("active_axis", sa.String(16), server_default="qi", nullable=False))
    op.add_column("players", sa.Column("body_xp",      sa.Integer(), server_default="0",  nullable=False))
    op.add_column("players", sa.Column("qi_xp",        sa.Integer(), server_default="0",  nullable=False))
    op.add_column("players", sa.Column("formation_xp", sa.Integer(), server_default="0",  nullable=False))


def downgrade() -> None:
    op.drop_column("players", "formation_xp")
    op.drop_column("players", "qi_xp")
    op.drop_column("players", "body_xp")
    op.drop_column("players", "active_axis")
