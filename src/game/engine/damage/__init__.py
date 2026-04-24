"""Damage calculation engine — public API."""
from .color import colorize_damage, to_ansi_block
from .combat_hit import (
    apply_damage_scaling,
    build_attack_stats,
    build_defense_stats,
    effective_damage_reduction,
    spd_evasion_bonus,
)
from .dot import calculate_dot_damage
from .pipeline import calculate_damage
from .result import DamageResult
from src.game.engine.stats import AttackStats, DefenseStats

__all__ = [
    "calculate_damage",
    "calculate_dot_damage",
    "colorize_damage",
    "to_ansi_block",
    "DamageResult",
    "AttackStats",
    "DefenseStats",
    "build_attack_stats",
    "build_defense_stats",
    "effective_damage_reduction",
    "apply_damage_scaling",
    "spd_evasion_bonus",
]
