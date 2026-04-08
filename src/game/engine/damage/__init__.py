"""Damage calculation engine — public API."""
from .pipeline import calculate_damage
from .result import DamageResult
from src.game.engine.stats import AttackStats, DefenseStats

__all__ = ["calculate_damage", "DamageResult", "AttackStats", "DefenseStats"]
