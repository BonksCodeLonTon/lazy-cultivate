"""Widen players.constitution_type for multi-slot Thể Tu support.

Thể Tu can now equip up to 8 standard Thể Chất + Hỗn Độn (stored as a
comma-separated list of keys). Original 64-char limit is too tight.

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-24
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "players",
        "constitution_type",
        existing_type=sa.String(length=64),
        type_=sa.String(length=512),
        existing_server_default="ConstitutionVanTuong",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "players",
        "constitution_type",
        existing_type=sa.String(length=512),
        type_=sa.String(length=64),
        existing_server_default="ConstitutionVanTuong",
        existing_nullable=False,
    )
