"""Damage calculation engine — public API."""
from .pipeline import calculate_damage
from .result import DamageResult

__all__ = ["calculate_damage", "DamageResult"]
