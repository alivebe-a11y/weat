"""SQLite store for the local verification stack.

The DB is the internal engine; a byte-faithful copy of data/forecast.csv is
always exportable from it so the existing Excel/GitHub link keeps working.

- forecasts: a faithful mirror of forecast.csv (every row preserved, in order).
- actuals:   observed weather per shift (one row per location+shift+date).

Scoring uses only the latest forecast run per shift (see latest_forecasts).
"""

import csv
import sqlite3
from pathlib import Path

import config
from fetch import CSV_HEADER

ACTUAL_COLS = [
    "location",
    "shift",
    "shift_start_date",
    "rain_actual_mm",
    "gust_actual_mph",
    "temp_min_actual_c",
    "temp_max_actual_c",
    "source",
    "fetched_utc",
]


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.DB_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    cols = ",\n        ".join(f'"{c}" TEXT' for c in CSV_HEADER)
    conn.execute(f"CREATE TABLE IF NOT EXISTS forecasts (\n        {cols}\n    )")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS actuals (
            location TEXT,
            shift TEXT,
            shift_start_date TEXT,
            rain_actual_mm REAL,
            gust_actual_mph REAL,
            temp_min_actual_c REAL,
            temp_max_actual_c REAL,
            source TEXT,
            fetched_utc TEXT,
            PRIMARY KEY (location, shift, shift_start_date)
        )
        """
    )
    conn.commit()


def import_csv(conn: sqlite3.Connection, csv_path: str | None = None) -> int:
    """Replace the forecasts table with the exact contents of the CSV, in
    file order. Idempotent — safe to run before every actuals/score pass."""
    path = Path(csv_path or config.OUTPUT_FILE)
    if not path.exists() or path.stat().st_size == 0:
        return 0
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    placeholders = ",".join("?" for _ in CSV_HEADER)
    cols = ",".join(f'"{c}"' for c in CSV_HEADER)
    conn.execute("DELETE FROM forecasts")
    conn.executemany(
        f"INSERT INTO forecasts ({cols}) VALUES ({placeholders})",
        [[r.get(c, "") for c in CSV_HEADER] for r in rows],
    )
    conn.commit()
    return len(rows)


def export_csv(conn: sqlite3.Connection, csv_path: str | None = None) -> int:
    """Write forecasts back out byte-faithfully (same columns, order, rows)."""
    path = Path(csv_path or config.OUTPUT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute("SELECT * FROM forecasts ORDER BY rowid").fetchall()
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for r in rows:
            writer.writerow({c: (r[c] if r[c] is not None else "") for c in CSV_HEADER})
    return len(rows)


def latest_forecasts(conn: sqlite3.Connection):
    """One row per (location, shift, date): the most recent run (highest
    rowid, i.e. last appended). This is the forecast we score."""
    return conn.execute(
        """
        SELECT f.* FROM forecasts f
        JOIN (
            SELECT location, shift, shift_start_date, MAX(rowid) AS rid
            FROM forecasts
            GROUP BY location, shift, shift_start_date
        ) m ON f.rowid = m.rid
        ORDER BY f.shift_start_date, f.shift, f.location
        """
    ).fetchall()


def has_actual(conn, location: str, shift: str, shift_start_date: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM actuals WHERE location=? AND shift=? AND shift_start_date=?",
            (location, shift, shift_start_date),
        ).fetchone()
        is not None
    )


def upsert_actual(conn: sqlite3.Connection, actual: dict) -> None:
    updates = ",".join(
        f"{c}=excluded.{c}"
        for c in ACTUAL_COLS
        if c not in ("location", "shift", "shift_start_date")
    )
    cols = ",".join(ACTUAL_COLS)
    placeholders = ",".join(f":{c}" for c in ACTUAL_COLS)
    conn.execute(
        f"""
        INSERT INTO actuals ({cols}) VALUES ({placeholders})
        ON CONFLICT(location, shift, shift_start_date) DO UPDATE SET {updates}
        """,
        actual,
    )
    conn.commit()
