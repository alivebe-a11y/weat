# Self-hosting the weather stack (Docker)

Runs the forecast collector, ERA5 verification, and (step 3) the dashboard on
your own always-on box — no dependence on GitHub's scheduler. `forecast.csv` is
still mirrored to GitHub so your Excel workbook's URL keeps working.

## What runs
A single `collector` container runs `scheduler.py`, which:

- **every `FETCH_INTERVAL_MIN` minutes** → collects the upcoming shift's forecast
  (`fetch.py`) and mirrors `forecast.csv` to GitHub (`mirror.py`);
- **daily at `VERIFY_HOUR`** → pulls ERA5 actuals (`actuals.py`) and rescores
  (`score.py`).

State lives in `./data` on the host: `forecast.csv` (the Excel mirror),
`forecast.db` (SQLite engine), and `verification.csv`.

## Setup
1. Install Docker + Docker Compose on the always-on device.
2. Clone this repo there (the existing `data/forecast.csv` history comes with it).
3. Configure:
   ```bash
   cp .env.example .env
   # edit .env: paste a GitHub token (Contents: read & write) to enable mirroring,
   # or leave GITHUB_TOKEN blank to run fully local.
   ```
4. Build and start:
   ```bash
   docker compose up -d --build
   ```
5. Watch it:
   ```bash
   docker compose logs -f collector
   ```

The container primes one forecast immediately on startup, so you'll see activity
right away.

## Notes
- Linux containers have proper CA certs, so the Windows-only `truststore` cert
  workaround isn't needed here.
- `.env` and `*.db` are gitignored. Only `forecast.csv` is mirrored to GitHub.
- You can keep the GitHub Actions workflow running in parallel during the
  cutover; the API mirror and the Actions push both target the same file and
  no-op when there's nothing new.
