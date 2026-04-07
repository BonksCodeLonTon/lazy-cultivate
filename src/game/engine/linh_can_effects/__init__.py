"""Linh Căn combat effect modules.

Each module handles one Linh Căn's active combat effect.
Import the specific phase function you need:

  Pre-turn (on target):  phong.try_dodge
  Pre-turn (on actor):   quang.try_cleanse
  Pre-damage (on actor): kim.get_pen_pct
  On-hit (on actor):     hoa.on_hit, thuy.on_hit, loi.on_hit, am.on_hit
  Periodic (on self):    tho.check_shield

To add a new effect: create a new module here following the same phase convention.
"""
from . import am, hoa, kim, loi, phong, quang, tho, thuy

__all__ = ["am", "hoa", "kim", "loi", "phong", "quang", "tho", "thuy"]
