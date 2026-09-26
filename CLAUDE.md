# Money Guilt

A PyQt5 macOS menu-bar widget that shows rotating "guilt" stats about wasteful spending, using transactions synced from Plaid.

## Git workflow

- Commit finished, tested work yourself, one focused commit per change. Don't wait to be asked.
- **Push every commit automatically, immediately after committing, without asking** (`git push origin master`, github.com/syuyrw/money-guilt). Nothing is left local-only. If a push is rejected, fetch and merge or rebase; don't force it.
- Never force-push or rewrite history without being asked.
- Never bypass the pre-commit hook (`--no-verify`). Everything is pushed to a public repo automatically, so the hook that blocks secrets is the last check before it goes out.
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
- Wasted-dollar reporting (`telemetry.py`, `collector/server.py`) is on by default with an opt-out (Settings, plus a first-launch notice explaining it and offering Turn Off Sharing) and sends only an anonymous install ID, the running total and a count. Never add merchants, dates or per-transaction amounts to the report. Users can delete what they reported (Settings, or `python3 telemetry.py delete`): that erases the collector's row, turns sharing off, forgets the install ID, and retries every 15 minutes if the server can't be reached. Keep PRIVACY.md true to this. `python3 wasted_total.py` prints your local total; it is never shown in the widget.

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

- Menu bar icon > Settings… (`settings_dialog.py`). Controls apply immediately, with no OK/Cancel, and the window holds no state of its own: it reads and writes through the widget's setters and the telemetry config. **Settings-page options live only there**: the menu bar icon holds actions (Hide Widget, Next Stat, Settings…, Categorize, Send Feedback, Quit), never a second copy of a setting. A duplicate would need its own syncing and can drift out of step; a test enforces this.
- Any new user setting goes here **and** is persisted in `QSettings` through a widget setter (`set_*`), with a default in `__init__`. Don't store settings in the dialog.
- It is created with no Qt parent on purpose: a child of the widget inherits the dark translucent stylesheet and mangles native controls. Word-wrapped notes get an exact height from font metrics (`_note`); letting Qt size them clipped text or left big gaps.
- "Delete My Reported Data" acts on the first click, in a background thread, and also turns sharing off (otherwise the next launch would re-upload the total). The startup categorize prompt is on by default and can be switched off here.

## Working style

- **Whenever the user says to do something every time, always, or from now on, add it to this file right away** (in the section it belongs to), then commit and push it. Don't leave it only in the conversation. One-off requests don't go here.
- Confirm before anything hard to reverse or outward-facing that wasn't asked for. Look at a target before deleting or overwriting it.
- Report results as they are: if a test fails or a step was skipped, say so.
- Files that other sessions or the user changed on disk are deliberate; don't revert them.
