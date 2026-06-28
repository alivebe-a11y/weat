"""Score stored forecasts against ERA5 actuals.

Writes a tidy data/verification.csv (one row per shift per variable) for the
dashboard, and prints a per-model accuracy summary plus data-driven blend
weights you can copy into config.py.
"""

import csv
import logging
import sys

import config
import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("score")

MODELS_SHORT = ["ukmo", "ecmwf", "gfs"]

# variable -> (actual column, blend column, per-model column template)
VARIABLES = {
    "rain": ("rain_actual_mm", "rain_blend_mm", "rain_{}_mm"),
    "gust": ("gust_actual_mph", "gust_blend_mph", "gust_{}_mph"),
    "temp_min": ("temp_min_actual_c", "temp_min_blend_c", "temp_min_{}_c"),
    "temp_max": ("temp_max_actual_c", "temp_max_blend_c", "temp_max_{}_c"),
}

VERIFICATION_HEADER = [
    "location",
    "shift",
    "shift_start_date",
    "variable",
    "actual",
    "blend_forecast",
    "blend_error",
    "ukmo_error",
    "ecmwf_error",
    "gfs_error",
]


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    conn = store.connect()
    store.init_db(conn)
    store.import_csv(conn)

    forecasts = {
        (r["location"], r["shift"], r["shift_start_date"]): r
        for r in store.latest_forecasts(conn)
    }
    actuals = conn.execute("SELECT * FROM actuals").fetchall()
    conn.close()

    out_rows = []
    # abs-error accumulators: errors[variable]["blend"|model] = [abs errors]
    errors = {v: {"blend": [], **{m: [] for m in MODELS_SHORT}} for v in VARIABLES}

    for a in actuals:
        key = (a["location"], a["shift"], a["shift_start_date"])
        fc = forecasts.get(key)
        if fc is None:
            continue
        for var, (acol, bcol, mtmpl) in VARIABLES.items():
            actual = f(a[acol])
            blend = f(fc[bcol])
            if actual is None or blend is None:
                continue
            blend_err = round(blend - actual, 2)
            errors[var]["blend"].append(abs(blend_err))
            row = {
                "location": a["location"],
                "shift": a["shift"],
                "shift_start_date": a["shift_start_date"],
                "variable": var,
                "actual": actual,
                "blend_forecast": blend,
                "blend_error": blend_err,
            }
            for m in MODELS_SHORT:
                mv = f(fc[mtmpl.format(m)])
                err = round(mv - actual, 2) if mv is not None else None
                row[f"{m}_error"] = err if err is not None else ""
                if err is not None:
                    errors[var][m].append(abs(err))
            out_rows.append(row)

    with open(config.VERIFICATION_FILE, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=VERIFICATION_HEADER)
        writer.writeheader()
        writer.writerows(out_rows)
    log.info("Wrote %d verification row(s) to %s", len(out_rows), config.VERIFICATION_FILE)

    scored = len({(r["location"], r["shift"], r["shift_start_date"]) for r in out_rows})
    print(f"\nScored shifts with actuals: {scored}\n")
    if not out_rows:
        print("No overlap between forecasts and actuals yet — run actuals.py first")
        print("(and remember ERA5 lags ~5 days, so recent shifts won't score).")
        return 0

    def mae(vals):
        return sum(vals) / len(vals) if vals else None

    header = f"{'variable':9} {'n':>3} {'blend':>7} {'ukmo':>7} {'ecmwf':>7} {'gfs':>7}   recommended weights (ukmo/ecmwf/gfs)"
    print("Mean absolute error (lower = better):")
    print(header)
    print("-" * len(header))
    for var in VARIABLES:
        n = len(errors[var]["blend"])
        maes = {m: mae(errors[var][m]) for m in MODELS_SHORT}
        bl = mae(errors[var]["blend"])
        # inverse-error weights (skip models with no/zero error data)
        inv = {m: (1.0 / maes[m]) for m in MODELS_SHORT if maes[m]}
        tot = sum(inv.values())
        wts = {m: (inv.get(m, 0.0) / tot if tot else 0.0) for m in MODELS_SHORT}

        def s(x):
            return f"{x:7.2f}" if x is not None else f"{'--':>7}"

        wt_str = " / ".join(f"{wts[m]:.2f}" for m in MODELS_SHORT)
        print(f"{var:9} {n:>3} {s(bl)} {s(maes['ukmo'])} {s(maes['ecmwf'])} {s(maes['gfs'])}   {wt_str}")

    print(
        "\nWeights are inverse-MAE per variable. Treat as guidance until you have "
        "a few weeks of\nscored shifts - small samples are noisy. Map rain -> RAIN_WEIGHTS, "
        "gust -> GUST_WEIGHTS,\ntemp_min -> TEMP_MIN_WEIGHTS, temp_max -> TEMP_MAX_WEIGHTS in config.py."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
