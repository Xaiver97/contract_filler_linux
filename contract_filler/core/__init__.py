# -*- coding: utf-8 -*-
"""供用电合同手写填充 · 核心包"""

from .noise import SmoothNoise1D, BaselineDrifter          # noqa: F401
from .ink import (sample_background, compute_ink_color,    # noqa: F401
                  apply_ink_bleed, add_grain, fade,
                  match_scan_texture, soften_region)
from .fonts import FontManager                              # noqa: F401
from .renderer import HandwriteRenderer, StrokeStyle        # noqa: F401
from .fingerprint import FingerprintStamper                 # noqa: F401
from .composer import ContractComposer                      # noqa: F401

__all__ = [
    "SmoothNoise1D", "BaselineDrifter",
    "sample_background", "compute_ink_color", "apply_ink_bleed",
    "add_grain", "fade", "match_scan_texture", "soften_region",
    "FontManager", "HandwriteRenderer", "StrokeStyle",
    "FingerprintStamper", "ContractComposer",
]
