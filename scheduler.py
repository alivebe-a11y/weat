"""In-container scheduler for the local weather stack.

Runs two jobs on a timer instead of relying on GitHub's (unreliable) cron:

  * forecast - every FETCH_INTERVAL_MIN minutes: collect the upcoming shift's
    forecast, then mirror forecast.csv to GitHub so the Excel link stays live.
  * verify   - once a day at VERIFY_HOUR (local): pull ERA5 actuals for newly
    completed shifts and rescore.

Environment:
    FETCH_INTERVAL_MIN   forecast cadence in minutes (default 30)
    VERIFY_HOUR          hour (0-23, local) to run actuals+score (default 6)
    plus the GITHUB_* vars read by mirror.py
"""

import logging
import os

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import actuals
import config
import fetch
import mirror
import score

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scheduler")


def forecast_job() -> None:
    log.info("=== forecast job ===")
    try:
        fetch.main()
        mirror.push_csv()
    except Exception:
        log.exception("forecast job failed")


def verify_job() -> None:
    log.info("=== verify job ===")
    try:
        actuals.main()
        score.main()
    except Exception:
        log.exception("verify job failed")


def main() -> None:
    interval = int(os.environ.get("FETCH_INTERVAL_MIN", "30"))
    verify_hour = int(os.environ.get("VERIFY_HOUR", "6"))

    sched = BlockingScheduler(
        timezone=config.TIMEZONE,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600},
    )
    sched.add_job(forecast_job, IntervalTrigger(minutes=interval), id="forecast")
    sched.add_job(verify_job, CronTrigger(hour=verify_hour, minute=0), id="verify")

    log.info(
        "Scheduler up: forecast every %dm, verify daily at %02d:00 %s",
        interval,
        verify_hour,
        config.TIMEZONE,
    )
    # Prime immediately so a fresh container produces data without waiting.
    forecast_job()
    sched.start()


if __name__ == "__main__":
    main()
