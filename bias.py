"""Learned bias offsets for the blended forecast.

score.py measures the mean signed error (bias) of the blend per variable and
writes offsets here; the offset is what you ADD to a forecast to centre it on
observed reality (e.g. gusts read ~3.5 mph low, so gust offset is +3.5).

By default these offsets change NOTHING in forecast.csv: fetch.py only applies
them when config.APPLY_BIAS_CORRECTION is True (it is False by default). They
are surfaced in the verification outputs and dashboard regardless.
"""

import json
from pathlib import Path

import config

# blend column -> observed ("actual") column it is scored against.
BIAS_VARS = {
    "rain_blend_mm": "rain_actual_mm",
    "gust_blend_mph": "gust_actual_mph",
    "temp_min_blend_c": "temp_min_actual_c",
    "temp_max_blend_c": "temp_max_actual_c",
}


def compute(pairs: dict[str, list[tuple[float, float]]], min_samples: int) -> dict:
    """pairs[blend_col] = [(forecast, actual), ...] -> {blend_col: offset}.

    offset = mean(actual - forecast). Only emitted when there are at least
    min_samples points; otherwise 0.0 (no correction) so a thin sample can't
    introduce a spurious shift.
    """
    offsets = {}
    for col, pts in pairs.items():
        if len(pts) >= min_samples:
            offsets[col] = round(sum(a - f for f, a in pts) / len(pts), 2)
        else:
            offsets[col] = 0.0
    return offsets


def load() -> dict:
    path = Path(config.BIAS_FILE)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return {}


def save(offsets: dict) -> None:
    path = Path(config.BIAS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(offsets, indent=2))
