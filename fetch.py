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
    "shift_start_date",
    "shift_start_time",
    "shift_end_date",
    "shift_end_time",
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


def shift_window(name: str, start_date: date, tz) -> tuple[datetime, datetime]:
    """Build the (start, end) datetimes for a shift instance anchored to the
    date the shift *starts* on. Handles windows that wrap past midnight."""
    cfg = config.SHIFTS[name]
    start = datetime.combine(start_date, parse_hhmm(cfg["start"]), tzinfo=tz)
    end = datetime.combine(start_date, parse_hhmm(cfg["end"]), tzinfo=tz)
    if parse_hhmm(cfg["end"]) <= parse_hhmm(cfg["start"]):  # wraps past midnight
        end += timedelta(days=1)
    return start, end


def pick_shift(now_local: datetime) -> tuple[str, datetime, datetime]:
    """Return (shift_name, start_local_dt, end_local_dt) for the shift we
    should currently be forecasting.

    Normally this is the *next* shift to begin: we record the upcoming shift
    at any point during the preceding ~12 hours, so a single run landing
    anywhere in that long window is enough. Combined with the dedup check in
    main(), dropped or delayed scheduled runs no longer lose a shift.

    FORCE_SHIFT=day|night overrides the selection for manual/smoke runs and
    targets today's instance of that shift.
    """
    tz = now_local.tzinfo
    forced = os.environ.get("FORCE_SHIFT")
    if forced in config.SHIFTS:
        log.info("FORCE_SHIFT=%s overriding shift selection", forced)
        start, end = shift_window(forced, now_local.date(), tz)
        return forced, start, end

    # Among all upcoming shift starts (today and tomorrow), pick the soonest.
    candidates: list[tuple[datetime, str, datetime]] = []
    for name in config.SHIFTS:
        for day_offset in (0, 1):
            start, end = shift_window(
                name, now_local.date() + timedelta(days=day_offset), tz
            )
            if start > now_local:
                candidates.append((start, name, end))
    candidates.sort(key=lambda c: c[0])
    start, name, end = candidates[0]
    return name, start, end


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
        "shift_start_date": shift_start.date().isoformat(),
        "shift_start_time": shift_start.strftime("%H:%M"),
        "shift_end_date": shift_end.date().isoformat(),
        "shift_end_time": shift_end.strftime("%H:%M"),
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


def row_key(r: dict) -> tuple[str, str, str]:
    """Identity of a shift forecast: one row per location per shift instance."""
    return (str(r["location"]), str(r["shift"]), str(r["shift_start_date"]))


def normalise(row: dict) -> dict:
    """Project a built row onto CSV_HEADER as the strings csv will write,
    so it compares equal to a row round-tripped through the file."""
    out = {}
    for k in CSV_HEADER:
        v = row.get(k, "")
        out[k] = "" if v is None else str(v)
    return out


def same_forecast(a: dict, b: dict) -> bool:
    """Equal on every field except run_utc (the issue timestamp)."""
    return all(a[k] == b[k] for k in CSV_HEADER if k != "run_utc")


def load_rows() -> list[dict]:
    path = Path(config.OUTPUT_FILE)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="") as f:
        return [normalise(r) for r in csv.DictReader(f)]


def write_rows(rows: list[dict]) -> None:
    path = Path(config.OUTPUT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in CSV_HEADER})


def main() -> int:
    tz = ZoneInfo(config.TIMEZONE)
    now_local = datetime.now(tz)
    log.info("Run at %s", now_local.isoformat(timespec="seconds"))

    shift_name, shift_start, shift_end = pick_shift(now_local)
    log.info(
        "Target %s shift %s → %s",
        shift_name,
        shift_start.isoformat(timespec="minutes"),
        shift_end.isoformat(timespec="minutes"),
    )

    # pick_shift always targets the *next* shift to start, so we only ever
    # touch the upcoming shift's row. The instant a shift begins, the target
    # rolls to the following shift and the just-started row is frozen on the
    # last forecast issued before it started — no explicit cutoff needed.

    locations = load_locations()
    log.info("Locations: %s", ", ".join(l["name"] for l in locations))

    rows = load_rows()
    index = {row_key(r): i for i, r in enumerate(rows)}

    failures = 0
    changed = 0
    for loc in locations:
        try:
            data = fetch_openmeteo(loc["lat"], loc["lon"])
            _, per_model = slice_shift(data["hourly"], shift_start, shift_end)
            row = normalise(build_row(loc, shift_name, shift_start, shift_end, per_model))
            key = row_key(row)
            existing_i = index.get(key)
            if existing_i is None:
                rows.append(row)
                index[key] = len(rows) - 1
                changed += 1
                action = "added"
            elif same_forecast(row, rows[existing_i]):
                action = "unchanged"
            else:
                rows[existing_i] = row
                changed += 1
                action = "refreshed"
            log.info(
                "%s [%s]: rain=%smm (%s) gust=%smph (%s) temp=%s..%s°C (%s/%s)",
                loc["name"],
                action,
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
            failures += 1
            log.exception("Failed for %s", loc["name"])

    if changed:
        write_rows(rows)
        log.info("Wrote %d updated row(s) to %s", changed, config.OUTPUT_FILE)
    else:
        log.info("No forecast changes — file untouched.")

    if failures:
        log.error("%d of %d location(s) failed", failures, len(locations))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
