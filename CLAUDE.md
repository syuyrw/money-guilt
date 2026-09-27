# Money Guilt

A PyQt5 macOS menu-bar widget that shows rotating "guilt" stats about wasteful spending, using transactions synced from Plaid.

## Project layout

- `app/` — the PyQt5 UI: `widget.py` (entry point), `settings_dialog.py`, `categorization_dialog.py`, `feedback_dialog.py`, `app_icon.py`, `styles.css`.
- `banking/` — everything that talks to Plaid and the Keychain: `plaid_client.py`, `link_app.py` (the Flask account-linking page, with its `static/`/`templates/`), `disconnect.py`, `plaid_sync.py`, `secure_store.py`.
- `data/` — local storage: `database.py`, `categorizer.py`, `paths.py`, `stats.py`, `stats_more.py`, `wasted_total.py`.
- `privacy/` — reporting and feedback: `telemetry.py`, `feedback.py`, and `notice.py` (the first-launch sharing notice; module name avoids colliding with the package name).
- `security/` — `check_private_data.py`, the private-data scanner the git hooks run.
- `scripts/` — one-off dev scripts kept only for their regression tests (`mian.py`).
- `tests/` — every test file. Each still runs the same way, just from this folder (see Running and testing below).
- Every module imports its neighbors by full package path (e.g. `from data import paths`), and every directly-run script inserts the project root onto `sys.path` itself, so it works whether launched directly, from the packaged `.app`, or imported by a test.

## Git workflow

- Commit finished, tested work yourself, one focused commit per change. Don't wait to be asked.
- **Push every commit automatically, immediately after committing, without asking** (`git push origin master`, github.com/syuyrw/money-guilt). Nothing is left local-only. If a push is rejected, fetch and merge or rebase; don't force it.
- Never force-push or rewrite history without being asked.
- Never bypass the pre-commit hook (`--no-verify`). Everything is pushed to a public repo automatically, so the hook that blocks secrets is the last check before it goes out.
- The repo is **public**. Before committing, make sure nothing private is staged: no `.env`, database, `merchant_overrides.json`, access tokens, or Plaid credentials. Run `./security_check.sh` after touching dependencies or code that handles credentials, and fix anything it flags.

## Private data

- The database, `.env` and `merchant_overrides.json` live in `~/Library/Application Support/MoneyGuilt` (owner-only, not iCloud-synced). `data/paths.py` owns the location. Never write them back into the project folder.
- The Plaid access token lives in the macOS Keychain (`banking/secure_store.py`), with no plaintext fallback.
- Never print or log secrets, tokens, or transaction details. Keep log files owner-only.
- **Transaction data lives only in the local database, never in the repo.** Nothing derived from it may be committed or pushed: no exports, screenshots of amounts, sample rows, or real merchant/amount pairs in tests, docs or fixtures (use invented data). `security/check_private_data.py` enforces it: it compares every commit against the transaction/account IDs in the local database, your Plaid token, merchant-plus-exact-amount lines, and data-shaped file names, and never prints what it found. `hooks/pre-commit` runs it on staged changes and `hooks/pre-push` on every commit about to leave the machine (pushes are automatic, so this is the last check). After cloning, run `./install_hooks.sh` once. `./security_check.sh` also scans the whole history.

## Running and testing

- Run with `python3 app/widget.py` using the Homebrew Python (`/usr/local/opt/python@3.14/bin/python3`). The project `.venv` can't load Qt's platform plugin; use it only for the dev tools (pip-audit, bandit).
- `./make_app.sh` builds and installs `/Applications/MoneyGuilt.app`. Re-run it if the project folder moves. Code changes only need the widget relaunched, not a rebuild.
- A running widget keeps its old code. After changing code, tell the user to restart it, or restart it only if they say so. When restarting, kill only the widget's own processes; never `pkill` by a broad pattern like `Python.app`.
- The widget allows only one instance (lock file in the temp folder).
- Run the tests before committing: `.venv/bin/python tests/test_app.py` (needs Qt and keyring, both in the `.venv`) and `.venv/bin/python -m unittest tests.test_security tests.test_paths tests.test_secure_store tests.test_telemetry tests.test_plaid_client tests.test_private_data tests.test_disconnect tests.test_plaid_sync` (needs keyring/Flask, so the `.venv`).
- **Transactions are pulled from Plaid at most once a calendar day** (`banking/plaid_sync.py`), at local midnight. The date of the last successful pull is written to disk (`plaid_sync.json` in the private data directory), not just held in memory, so restarting the widget doesn't trigger a second pull for the same day; if the widget wasn't running at midnight, the missed day's pull happens at the next startup instead of being skipped. A failed pull isn't marked done, so it's retried at the next opportunity rather than silently skipped for a day. Runs off the interface thread (`PlaidSyncWorker` in `app/widget.py`); a successful pull calls `advance_stat()` so the widget picks up the new data.
- Wasted-dollar reporting (`privacy/telemetry.py`, `collector/server.py`) is on by default with an opt-out (Settings, plus a first-launch notice explaining it and offering Turn Off Sharing) and sends only an anonymous install ID and the running dollar total (no counts, names or vendors). Never add merchants, dates or per-transaction amounts to the report. Users can delete what they reported (Settings, or `python3 privacy/telemetry.py delete`): that erases the collector's row, turns sharing off, forgets the install ID, and retries every 15 minutes if the server can't be reached. Keep PRIVACY.md true to this. `python3 data/wasted_total.py` prints your local total; it is never shown in the widget.

## Behaviour decisions

Standing requirements from the user (each was stated as "every time", "any time" or "always"):

- **Every time the widget starts, it pops up the categorize window** for up to ten new transactions. It stays silent when there are none. (`prompt_for_new_transactions`; the tray's Categorize Transactions still shows the "all done" state.)
- **Every launch starts on a random stat.**
- **Any dollar amount that represents waste is shown in red**, wherever it appears (value, second line, or inside a subtitle). Total spending is not waste, so it stays white. Stats mark the figure with `wasted_text`.
- **The large value text is always centred**, vertically and horizontally, on every stat at every window size. Tests enforce it; if a layout change breaks them, fix the layout, not the test.

Other decisions:

- Each transaction is asked about in the categorize dialog only once (`prompted` column). When nothing is new, the window says all have been categorized.
- Categorizing a merchant applies that answer to all of its unreviewed transactions.
- "Regret" means a transaction marked wasteful; "kept" means reviewed and not wasteful.
- Stats hide themselves when there isn't enough data. `HOURLY_RATE` and `MONTHLY_RENT` in the private `.env` enable the hours-of-work and rent stats.
- The menu bar icon only works when the widget runs inside Python's own app bundle, so the launcher hands off to `Python.app` instead of running Python from inside `MoneyGuilt.app`. The cost is a second Dock icon.

## Settings window

- Menu bar icon > Settings… (`app/settings_dialog.py`). Controls apply immediately, with no OK/Cancel, and the window holds no state of its own: it reads and writes through the widget's setters and the telemetry config. **Settings-page options live only there**: the menu bar icon holds actions (Hide Widget, Next Stat, Settings…, Categorize, Send Feedback, Quit), never a second copy of a setting. A duplicate would need its own syncing and can drift out of step; a test enforces this.
- Any new user setting goes here **and** is persisted in `QSettings` through a widget setter (`set_*`), with a default in `__init__`. Don't store settings in the dialog.
- It is created with no Qt parent on purpose: a child of the widget inherits the dark translucent stylesheet and mangles native controls. Word-wrapped notes get an exact height from font metrics (`_note`); letting Qt size them clipped text or left big gaps.
- "Delete My Reported Data" acts on the first click, in a background thread, and also turns sharing off (otherwise the next launch would re-upload the total). The startup categorize prompt is on by default and can be switched off here.

## Working style

- **Whenever the user says to do something every time, always, or from now on, add it to this file right away** (in the section it belongs to), then commit and push it. Don't leave it only in the conversation. One-off requests don't go here.
- Confirm before anything hard to reverse or outward-facing that wasn't asked for. Look at a target before deleting or overwriting it.
- Report results as they are: if a test fails or a step was skipped, say so.
- Files that other sessions or the user changed on disk are deliberate; don't revert them.
- `PLAID_ENV` must be exactly `sandbox` or `production` (unset means sandbox); anything else is an error, never production.
- Settings has **Disconnect Bank Account…** (revokes at Plaid before deleting the Keychain token) and **Delete Local Data…** (secure erase of transactions, accounts and merchant lessons); both confirm with Cancel as the default.
