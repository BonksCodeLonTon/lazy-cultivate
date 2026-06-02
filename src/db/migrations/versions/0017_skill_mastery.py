"""Add character_skill_mastery table (Skill Mastery feature).

One row per (player, skill) tracks per-skill mastery progression: ``level``
(1-25), in-band ``xp``, ``gate_fails`` (pity counter for the item-gated
breakthroughs), and ``hidden_unlocked`` (the secret 20->21 gate has been
crossed). Math/branching live in ``src.game.systems.skill_mastery``; this
table is pure persistence.

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-01
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: ``init_db()`` (``Base.metadata.create_all`` in main.py) may
    # have already created ``character_skill_mastery`` from the ORM model on a
    # prior bot startup, before alembic was run. On a fresh database we create
    # the full table here.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "character_skill_mastery" in inspector.get_table_names():
        return

    op.create_table(
        "character_skill_mastery",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "player_id",
            sa.Integer(),
            sa.ForeignKey("players.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("skill_key", sa.String(length=64), nullable=False),
        sa.Column("level", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("xp", sa.Integer(), server_default="0", nullable=False),
        sa.Column("gate_fails", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column(
            "hidden_unlocked",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.UniqueConstraint("player_id", "skill_key", name="uq_mastery_player_skill"),
    )
    op.create_index(
        "ix_char_skill_mastery_player_id",
        "character_skill_mastery",
        ["player_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "character_skill_mastery" not in inspector.get_table_names():
        return
    op.drop_index(
        "ix_char_skill_mastery_player_id",
        table_name="character_skill_mastery",
    )
    op.drop_table("character_skill_mastery")
