"""Configuration for the weather forecast feed.

Tweak models, weights, shift times, and confidence thresholds here —
no need to edit fetch.py.
"""

MODELS = ["ukmo_seamless", "ecmwf_ifs025", "gfs_seamless"]

# Blend weights are inverse-MAE, derived from score.py over 98 scored shifts
# (Holbury, Apr-Jun 2026 — summer only). Re-run score.py after more seasons of
# data and update these; weights need not sum to 1 (blend() normalises).
RAIN_WEIGHTS = {
    "ukmo_seamless": 0.37,
    "ecmwf_ifs025": 0.29,
    "gfs_seamless": 0.34,
}

GUST_WEIGHTS = {
    "ukmo_seamless": 0.31,
    "ecmwf_ifs025": 0.45,  # ECMWF clearly best for gusts here
    "gfs_seamless": 0.24,
}

# Temp min and max rank the models differently, so they get separate weights.
TEMP_MIN_WEIGHTS = {
    "ukmo_seamless": 0.38,
    "ecmwf_ifs025": 0.33,
    "gfs_seamless": 0.29,
}

TEMP_MAX_WEIGHTS = {
    "ukmo_seamless": 0.48,  # UKMO clearly best for daytime highs here
    "ecmwf_ifs025": 0.23,
    "gfs_seamless": 0.29,
}

# Confidence bands: (max spread for this band, label). Falls through to "Low".
RAIN_CONF = [(0.3, "High"), (1.0, "Medium")]
GUST_CONF = [(3.0, "High"), (7.0, "Medium")]
TEMP_CONF = [(1.0, "High"), (3.0, "Medium")]  # °C spread across models

TIMEZONE = "Europe/London"

# Fixed 12h work shifts (local time). `end` is exclusive.
# `fetch_from`/`fetch_to` define when during the day the script is allowed
# to fetch for that shift.
SHIFTS = {
    "day": {
        "start": "04:30",
        "end": "16:30",
        "fetch_from": "03:30",
        "fetch_to": "04:30",
    },
    "night": {
        "start": "16:30",
        "end": "04:30",
        "fetch_from": "15:30",
        "fetch_to": "16:30",
    },
}

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
LOCATIONS_FILE = "locations.csv"
OUTPUT_FILE = "data/forecast.csv"

# --- Local verification stack (forecast accuracy vs ERA5 actuals) ---
DB_FILE = "data/forecast.db"
VERIFICATION_FILE = "data/verification.csv"
# ERA5 reanalysis (the "actual weather" source) lags real time, so only
# score shifts that ended at least this many days ago.
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ACTUALS_DELAY_DAYS = 5

# Bias correction. score.py learns per-variable offsets into BIAS_FILE.
# APPLY_BIAS_CORRECTION is OFF by default so forecast.csv is unchanged; flip to
# True to fold the offsets into the blend written to the CSV.
BIAS_FILE = "data/bias.json"
APPLY_BIAS_CORRECTION = False
BIAS_MIN_SAMPLES = 20
