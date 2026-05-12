"""Discord interaction safety helpers.

Discord invalidates an interaction token ~3 seconds after dispatch — any
``defer()`` or ``send_message()`` issued past that window raises
``discord.NotFound`` (error code 10062, "Unknown interaction") and crashes
the command callback. The same call also raises
``discord.InteractionResponded`` if a different code path already replied
(common with paginated views that race against a "back" button).

``safe_defer`` swallows both cases with a debug log so the calling cog can
keep going (typically by falling back to ``followup.send`` or just ending
silently). Use this when an interaction may have aged out before reaching
the defer call — slow DB lookups, follow-up button callbacks, status
commands that wait on a long-running query.
"""
from __future__ import annotations

import logging

import discord

log = logging.getLogger(__name__)


async def safe_defer(interaction: discord.Interaction, **kwargs) -> bool:
    """Defer ``interaction`` while tolerating expired / already-responded state.

    Returns True when the defer succeeded, False when the interaction was
    no longer answerable. Callers should branch on the return value when
    they need to know whether the followup channel is usable.
    """
    try:
        await interaction.response.defer(**kwargs)
        return True
    except discord.NotFound:
        # Token expired — no recovery is possible, the user will see the
        # default "interaction failed" toast on their side.
        log.debug(
            "safe_defer: interaction %s expired before defer",
            getattr(interaction, "id", "<unknown>"),
        )
        return False
    except discord.InteractionResponded:
        # Some earlier branch already responded; treat that as success so
        # the caller can continue to use ``followup``.
        return True
