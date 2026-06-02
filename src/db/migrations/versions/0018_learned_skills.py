"""Add character_learned_skills table (persistent learned-skill library).

One row per (player, skill) records every *regular* skill the player has ever
learned. Separate from ``character_skills`` (slots 0–5 = the equipped combat
bar): the combat skill bar is built from ``character_skills`` rows, so
unequipping/forgetting must delete the slot row — but the matching library row
here preserves "learned" status, letting the player re-equip for free without
re-spending a scroll.

Existing players are backfilled from their current regular skill slots
(``slot_index < MAX_SKILL_SLOTS``) — anything already equipped was obviously
already learned.

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.db.models.skill import MAX_SKILL_SLOTS

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: ``init_db()`` (``Base.metadata.create_all`` in main.py) may
    # have already created ``character_learned_skills`` from the ORM model on a
    # prior bot startup, before alembic was run. On a fresh database we create
    # the full table here.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "character_learned_skills" in inspector.get_table_names():
        return

    op.create_table(
        "character_learned_skills",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "player_id",
            sa.Integer(),
            sa.ForeignKey("players.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("skill_key", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("player_id", "skill_key", name="uq_learned_player_skill"),
    )
    op.create_index(
        "ix_learned_skills_player_id",
        "character_learned_skills",
        ["player_id"],
    )

    # Backfill: every regular equipped skill (slot < MAX_SKILL_SLOTS) was
    # already learned. DISTINCT collapses the (player, skill) pairs; the table
    # is freshly created above so there are no pre-existing rows to conflict
    # with, but we keep the SELECT DISTINCT shape defensive against any future
    # re-run path.
    op.execute(
        "INSERT INTO character_learned_skills (player_id, skill_key) "
        "SELECT DISTINCT player_id, skill_key FROM character_skills "
        f"WHERE slot_index < {MAX_SKILL_SLOTS}"
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "character_learned_skills" not in inspector.get_table_names():
        return
    op.drop_index(
        "ix_learned_skills_player_id",
        table_name="character_learned_skills",
    )
    op.drop_table("character_learned_skills")
