# Money Guilt

A PyQt5 macOS menu-bar widget that shows rotating "guilt" stats about wasteful spending, using transactions synced from Plaid.

## Git workflow

- Commit finished, tested work yourself, one focused commit per change. Don't wait to be asked.
- Push to `origin master` (github.com/syuyrw/money-guilt) after committing.
- Never force-push or rewrite history without being asked.
- The repo is **public**. Before committing, make sure nothing private is staged: no `.env`, database, `merchant_overrides.json`, access tokens, or Plaid credentials. Run `./security_check.sh` after touching dependencies or code that handles credentials, and fix anything it flags.

## Private data

- The database, `.env` and `merchant_overrides.json` live in `~/Library/Application Support/MoneyGuilt` (owner-only, not iCloud-synced). `paths.py` owns the location. Never write them back into the project folder.
- The Plaid access token lives in the macOS Keychain (`secure_store.py`), with no plaintext fallback.
- Never print or log secrets, tokens, or transaction details. Keep log files owner-only.

## Running and testing

- Run with `python3 widget.py` using the Homebrew Python (`/usr/local/opt/python@3.14/bin/python3`). The project `.venv` can't load Qt's platform plugin; use it only for the dev tools (pip-audit, bandit).
- `./make_app.sh` builds and installs `/Applications/MoneyGuilt.app`. Re-run it if the project folder moves. Code changes only need the widget relaunched, not a rebuild.
- A running widget keeps its old code. After changing code, tell the user to restart it, or restart it only if they say so. When restarting, kill only the widget's own processes; never `pkill` by a broad pattern like `Python.app`.
- The widget allows only one instance (lock file in the temp folder).
- Run the tests before committing: `python3 test_app.py` (needs Qt, so the Homebrew Python) and `.venv/bin/python -m unittest test_security test_paths test_secure_store test_telemetry` (needs keyring/Flask, so the `.venv`).
- Wasted-dollar reporting (`telemetry.py`, `collector/server.py`) is opt-in and sends only an anonymous install ID, the running total and a count. Never add merchants, dates or per-transaction amounts to the report. `python3 wasted_total.py` prints your local total; it is never shown in the widget.

## Behaviour decisions

- Each transaction is asked about in the categorize dialog only once (`prompted` column). When nothing is new, the window says all have been categorized.
- Categorizing a merchant applies that answer to all of its unreviewed transactions.
- "Regret" means a transaction marked wasteful; "kept" means reviewed and not wasteful.
- Stats hide themselves when there isn't enough data. `HOURLY_RATE` and `MONTHLY_RENT` in the private `.env` enable the hours-of-work and rent stats.
- The menu bar icon only works when the widget runs inside Python's own app bundle, so the launcher hands off to `Python.app` instead of running Python from inside `MoneyGuilt.app`. The cost is a second Dock icon.

## Working style

- Confirm before anything hard to reverse or outward-facing that wasn't asked for. Look at a target before deleting or overwriting it.
- Report results as they are: if a test fails or a step was skipped, say so.
- Files that other sessions or the user changed on disk are deliberate; don't revert them.
