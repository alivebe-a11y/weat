"""Fetch observed ("actual") weather from Open-Meteo's ERA5 archive for each
completed shift and store it, so forecasts can later be scored against reality.

ERA5 reanalysis lags real time, so only shifts that ended at least
config.ACTUALS_DELAY_DAYS ago are processed. Already-recorded shifts are
skipped, so this is cheap to run repeatedly.
"""

import logging
import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests

import config
import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("actuals")


def shift_bounds(row, tz) -> tuple[datetime, datetime]:
    def dt(date_str: str, time_str: str) -> datetime:
        h, m = (int(x) for x in time_str.split(":"))
        y, mo, d = (int(x) for x in date_str.split("-"))
        return datetime(y, mo, d, h, m, tzinfo=tz)

    start = dt(row["shift_start_date"], row["shift_start_time"])
    end = dt(row["shift_end_date"], row["shift_end_time"])
    return start, end


def fetch_archive(lat: float, lon: float, start_date: str, end_date: str) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": "precipitation,wind_gusts_10m,temperature_2m",
        "wind_speed_unit": "mph",
        "timezone": config.TIMEZONE,
    }
    r = requests.get(config.ARCHIVE_URL, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def observed(hourly: dict, start: datetime, end: datetime) -> dict:
    tz = start.tzinfo
    parsed = [datetime.fromisoformat(t).replace(tzinfo=tz) for t in hourly["time"]]
    idx = [i for i, t in enumerate(parsed) if start <= t < end]
    if not idx:
        raise RuntimeError(f"No archive hours inside {start} -> {end}")

    def col(name):
        return [hourly[name][i] for i in idx if hourly[name][i] is not None]

    rain, gust, temp = col("precipitation"), col("wind_gusts_10m"), col("temperature_2m")
    if not (rain and gust and temp):
        raise RuntimeError("Archive returned no usable values for window")
    return {
        "rain_actual_mm": round(sum(rain), 2),
        "gust_actual_mph": round(max(gust), 1),
        "temp_min_actual_c": round(min(temp), 1),
        "temp_max_actual_c": round(max(temp), 1),
    }


def main() -> int:
    tz = ZoneInfo(config.TIMEZONE)
    now = datetime.now(tz)
    cutoff = now - timedelta(days=config.ACTUALS_DELAY_DAYS)

    conn = store.connect()
    store.init_db(conn)
    imported = store.import_csv(conn)
    log.info("Synced %d forecast row(s) from %s", imported, config.OUTPUT_FILE)

    shifts = store.latest_forecasts(conn)
    pending = failures = added = 0
    for row in shifts:
        start, end = shift_bounds(row, tz)
        if end >= cutoff:
            continue  # too recent — ERA5 not available yet
        if store.has_actual(
            conn, row["location"], row["shift"], row["shift_start_date"]
        ):
            continue
        pending += 1
        try:
            data = fetch_archive(
                float(row["lat"]),
                float(row["lon"]),
                start.date().isoformat(),
                end.date().isoformat(),
            )
            obs = observed(data["hourly"], start, end)
            obs.update(
                location=row["location"],
                shift=row["shift"],
                shift_start_date=row["shift_start_date"],
                source="era5",
                fetched_utc=datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            store.upsert_actual(conn, obs)
            added += 1
            log.info(
                "%s %s %s: rain=%.2fmm gust=%.1fmph temp=%.1f..%.1fC",
                row["location"],
                row["shift"],
                row["shift_start_date"],
                obs["rain_actual_mm"],
                obs["gust_actual_mph"],
                obs["temp_min_actual_c"],
                obs["temp_max_actual_c"],
            )
        except Exception:
            failures += 1
            log.exception(
                "Failed actuals for %s %s %s",
                row["location"],
                row["shift"],
                row["shift_start_date"],
            )

    conn.close()
    log.info(
        "Done: %d new actual(s), %d pending checked, %d failed", added, pending, failures
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
