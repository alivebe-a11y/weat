"""Fetch multi-model weather forecast for each location and append a row
to data/forecast.csv for the upcoming work shift.

The script only writes a row when run during a shift's fetch window
(configurable in config.py). Outside those windows it exits quietly.

Override the window check for smoke-testing by setting FORCE_SHIFT=day|night.
"""

import csv
import logging
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("fetch")

CSV_HEADER = [
    "run_utc",
    "location",
    "lat",
    "lon",
    "shift",
    "shift_start_local",
    "shift_end_local",
    "rain_ukmo_mm",
    "rain_ecmwf_mm",
    "rain_gfs_mm",
    "rain_blend_mm",
    "rain_spread_mm",
    "rain_confidence",
    "gust_ukmo_mph",
    "gust_ecmwf_mph",
    "gust_gfs_mph",
    "gust_blend_mph",
    "gust_spread_mph",
    "gust_confidence",
    "temp_min_ukmo_c",
    "temp_min_ecmwf_c",
    "temp_min_gfs_c",
    "temp_min_blend_c",
    "temp_min_spread_c",
    "temp_min_confidence",
    "temp_max_ukmo_c",
    "temp_max_ecmwf_c",
    "temp_max_gfs_c",
    "temp_max_blend_c",
    "temp_max_spread_c",
    "temp_max_confidence",
]

# CSV column name suffix per model id.
MODEL_SHORT = {
    "ukmo_seamless": "ukmo",
    "ecmwf_ifs025": "ecmwf",
    "gfs_seamless": "gfs",
}


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def within(now_t: time, start: time, end: time) -> bool:
    """start inclusive, end exclusive, no wrap."""
    return start <= now_t < end


def pick_shift(now_local: datetime) -> tuple[str, datetime, datetime] | None:
    """Return (shift_name, start_local_dt, end_local_dt) if now is inside
    a fetch window, else None. Honours FORCE_SHIFT env var."""
    forced = os.environ.get("FORCE_SHIFT")
    today = now_local.date()
    tz = now_local.tzinfo

    def dt(d: date, hhmm: str) -> datetime:
        return datetime.combine(d, parse_hhmm(hhmm), tzinfo=tz)

    if forced in config.SHIFTS:
        cfg = config.SHIFTS[forced]
        start = dt(today, cfg["start"])
        end = dt(today, cfg["end"])
        if cfg["end"] <= cfg["start"]:  # wraps past midnight
            end += timedelta(days=1)
        log.info("FORCE_SHIFT=%s overriding window check", forced)
        return forced, start, end

    now_t = now_local.time()
    for name, cfg in config.SHIFTS.items():
        fetch_from = parse_hhmm(cfg["fetch_from"])
        fetch_to = parse_hhmm(cfg["fetch_to"])
        if within(now_t, fetch_from, fetch_to):
            start = dt(today, cfg["start"])
            end = dt(today, cfg["end"])
            if cfg["end"] <= cfg["start"]:
                end += timedelta(days=1)
            return name, start, end
    return None


def load_locations() -> list[dict]:
    with open(config.LOCATIONS_FILE, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["lat"] = float(r["lat"])
        r["lon"] = float(r["lon"])
    return rows


def fetch_openmeteo(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "precipitation,wind_gusts_10m,temperature_2m",
        "models": ",".join(config.MODELS),
        "wind_speed_unit": "mph",
        "forecast_days": 2,
        "timezone": config.TIMEZONE,
    }
    r = requests.get(config.OPEN_METEO_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def slice_shift(
    hourly: dict, shift_start: datetime, shift_end: datetime
) -> tuple[list[str], dict[str, list[float]]]:
    """Return (times_in_window, per_model_values) with 'precipitation' and
    'wind_gusts_10m' sliced to the shift window."""
    times = hourly["time"]  # ISO strings in local tz, e.g. "2026-04-17T05:00"
    # Open-Meteo returns times as naive local ISO strings; parse and attach tz.
    tz = shift_start.tzinfo
    parsed = [datetime.fromisoformat(t).replace(tzinfo=tz) for t in times]

    indices = [
        i for i, t in enumerate(parsed) if shift_start <= t < shift_end
    ]
    if not indices:
        raise RuntimeError(
            f"No hourly entries inside shift window {shift_start} → {shift_end}"
        )

    per_model: dict[str, dict[str, list[float]]] = {}
    for model in config.MODELS:
        rain_key = f"precipitation_{model}"
        gust_key = f"wind_gusts_10m_{model}"
        temp_key = f"temperature_2m_{model}"
        if rain_key not in hourly or gust_key not in hourly or temp_key not in hourly:
            raise RuntimeError(f"Missing model data for {model}")
        rain_vals = [hourly[rain_key][i] for i in indices]
        gust_vals = [hourly[gust_key][i] for i in indices]
        temp_vals = [hourly[temp_key][i] for i in indices]
        per_model[model] = {"rain": rain_vals, "gust": gust_vals, "temp": temp_vals}

    return [times[i] for i in indices], per_model


def blend(values: dict[str, float], weights: dict[str, float]) -> tuple[float, float]:
    """Return (weighted_average, spread). Drops models with None values."""
    pairs = [(v, weights[m]) for m, v in values.items() if v is not None]
    if not pairs:
        return float("nan"), float("nan")
    total_w = sum(w for _, w in pairs)
    weighted = sum(v * w for v, w in pairs) / total_w
    vs = [v for v, _ in pairs]
    spread = max(vs) - min(vs)
    return weighted, spread


def confidence(spread: float, bands: list[tuple[float, str]]) -> str:
    for threshold, label in bands:
        if spread < threshold:
            return label
    return "Low"


def build_row(
    loc: dict,
    shift_name: str,
    shift_start: datetime,
    shift_end: datetime,
    per_model: dict[str, dict[str, list[float]]],
) -> dict:
    rain_totals = {m: round(sum(v["rain"]), 2) for m, v in per_model.items()}
    gust_maxes = {m: round(max(v["gust"]), 1) for m, v in per_model.items()}
    temp_mins = {m: round(min(v["temp"]), 1) for m, v in per_model.items()}
    temp_maxes = {m: round(max(v["temp"]), 1) for m, v in per_model.items()}

    rain_blend_val, rain_spread = blend(rain_totals, config.RAIN_WEIGHTS)
    gust_blend_val, gust_spread = blend(gust_maxes, config.GUST_WEIGHTS)
    tmin_blend_val, tmin_spread = blend(temp_mins, config.TEMP_WEIGHTS)
    tmax_blend_val, tmax_spread = blend(temp_maxes, config.TEMP_WEIGHTS)

    row = {
        "run_utc": datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "location": loc["name"],
        "lat": loc["lat"],
        "lon": loc["lon"],
        "shift": shift_name,
        "shift_start_local": shift_start.isoformat(timespec="minutes"),
        "shift_end_local": shift_end.isoformat(timespec="minutes"),
        "rain_blend_mm": round(rain_blend_val, 2),
        "rain_spread_mm": round(rain_spread, 2),
        "rain_confidence": confidence(rain_spread, config.RAIN_CONF),
        "gust_blend_mph": round(gust_blend_val, 1),
        "gust_spread_mph": round(gust_spread, 1),
        "gust_confidence": confidence(gust_spread, config.GUST_CONF),
        "temp_min_blend_c": round(tmin_blend_val, 1),
        "temp_min_spread_c": round(tmin_spread, 1),
        "temp_min_confidence": confidence(tmin_spread, config.TEMP_CONF),
        "temp_max_blend_c": round(tmax_blend_val, 1),
        "temp_max_spread_c": round(tmax_spread, 1),
        "temp_max_confidence": confidence(tmax_spread, config.TEMP_CONF),
    }
    for model, short in MODEL_SHORT.items():
        row[f"rain_{short}_mm"] = rain_totals.get(model)
        row[f"gust_{short}_mph"] = gust_maxes.get(model)
        row[f"temp_min_{short}_c"] = temp_mins.get(model)
        row[f"temp_max_{short}_c"] = temp_maxes.get(model)
    return row


def append_row(row: dict) -> None:
    path = Path(config.OUTPUT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        if new_file:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in CSV_HEADER})


def main() -> int:
    tz = ZoneInfo(config.TIMEZONE)
    now_local = datetime.now(tz)
    log.info("Run at %s", now_local.isoformat(timespec="seconds"))

    picked = pick_shift(now_local)
    if picked is None:
        log.info("Outside any fetch window — nothing to do.")
        return 0
    shift_name, shift_start, shift_end = picked
    log.info(
        "Forecasting %s shift %s → %s",
        shift_name,
        shift_start.isoformat(timespec="minutes"),
        shift_end.isoformat(timespec="minutes"),
    )

    locations = load_locations()
    log.info("Locations: %s", ", ".join(l["name"] for l in locations))

    for loc in locations:
        try:
            data = fetch_openmeteo(loc["lat"], loc["lon"])
            _, per_model = slice_shift(data["hourly"], shift_start, shift_end)
            row = build_row(loc, shift_name, shift_start, shift_end, per_model)
            append_row(row)
            log.info(
                "%s: rain=%.2fmm (%s) gust=%.1fmph (%s) temp=%.1f..%.1f°C (%s/%s)",
                loc["name"],
                row["rain_blend_mm"],
                row["rain_confidence"],
                row["gust_blend_mph"],
                row["gust_confidence"],
                row["temp_min_blend_c"],
                row["temp_max_blend_c"],
                row["temp_min_confidence"],
                row["temp_max_confidence"],
            )
        except Exception:
            log.exception("Failed for %s — skipping", loc["name"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
