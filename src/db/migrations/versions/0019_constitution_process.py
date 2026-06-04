"""Add character_constitution_progress table (Constitution Process feature).

One row per (player, constitution) tracks a body's progression so it survives
body-swaps: ``level`` (1-9), in-band ``xp``, and ``gate_fails`` (pity counter
for the trial+material-gated ceiling breakthroughs). Unlike
``character_skill_mastery`` there is no ``hidden_unlocked`` column —
constitutions have no hidden band. Math/branching live in
``src.game.systems.constitution_process``; this table is pure persistence.

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: ``init_db()`` (``Base.metadata.create_all`` in main.py) may
    # have already created ``character_constitution_progress`` from the ORM
    # model on a prior bot startup, before alembic was run. On a fresh database
    # we create the full table here.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "character_constitution_progress" in inspector.get_table_names():
        return

    op.create_table(
        "character_constitution_progress",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "player_id",
            sa.Integer(),
            sa.ForeignKey("players.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("constitution_key", sa.String(length=64), nullable=False),
        sa.Column("level", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("xp", sa.Integer(), server_default="0", nullable=False),
        sa.Column("gate_fails", sa.SmallInteger(), server_default="0", nullable=False),
        sa.UniqueConstraint(
            "player_id", "constitution_key", name="uq_cons_proc_player_key"
        ),
    )
    op.create_index(
        "ix_cons_proc_player_id",
        "character_constitution_progress",
        ["player_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "character_constitution_progress" not in inspector.get_table_names():
        return
    op.drop_index(
        "ix_cons_proc_player_id",
        table_name="character_constitution_progress",
    )
    op.drop_table("character_constitution_progress")
