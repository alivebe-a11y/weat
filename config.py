"""Configuration for the weather forecast feed.

Tweak models, weights, shift times, and confidence thresholds here —
no need to edit fetch.py.
"""

MODELS = ["ukmo_seamless", "ecmwf_ifs025", "gfs_seamless"]

RAIN_WEIGHTS = {
    "ukmo_seamless": 0.4,
    "ecmwf_ifs025": 0.4,
    "gfs_seamless": 0.2,
}

GUST_WEIGHTS = {
    "ukmo_seamless": 0.5,
    "ecmwf_ifs025": 0.3,
    "gfs_seamless": 0.2,
}

# Confidence bands: (max spread for this band, label). Falls through to "Low".
RAIN_CONF = [(0.3, "High"), (1.0, "Medium")]
GUST_CONF = [(3.0, "High"), (7.0, "Medium")]

TIMEZONE = "Europe/London"

# Fixed 12h work shifts (local time). `end` is exclusive.
# `fetch_from`/`fetch_to` define when during the day the script is allowed
# to fetch for that shift.
SHIFTS = {
    "day": {
        "start": "05:00",
        "end": "17:00",
        "fetch_from": "04:00",
        "fetch_to": "06:00",
    },
    "night": {
        "start": "17:00",
        "end": "05:00",
        "fetch_from": "16:00",
        "fetch_to": "18:00",
    },
}

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
LOCATIONS_FILE = "locations.csv"
OUTPUT_FILE = "data/forecast.csv"
