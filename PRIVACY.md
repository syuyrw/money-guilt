# Money Guilt Privacy Policy

Effective: September 25, 2026

Money Guilt is a desktop widget for macOS that shows how much of your spending you consider wasteful. This policy explains what it does with your information. The short version: your transactions stay on your computer, and the only thing the app can send anywhere is one anonymous number.

## What stays on your computer

Money Guilt reads your accounts and transactions from your bank through Plaid (see below) and keeps them in a database on your Mac. These never leave your computer through Money Guilt:

- your transactions (merchant names, dates, amounts) and account names and balances
- the categories and "wasteful" marks you choose, and what the app learns about merchants from them
- the settings you choose, such as your hourly rate or rent, if you enter them

Where it lives:

- The database, settings file and learned merchant categories are in `~/Library/Application Support/MoneyGuilt`. That folder is readable only by your macOS user account and is not part of iCloud sync.
- Your Plaid access token is stored in the macOS Keychain, not in a file.
- A log file at `~/Library/Logs/MoneyGuilt.log` records what the app is doing, such as start-up and errors. It is readable only by you and does not contain your transactions or credentials.

Money Guilt has no accounts, no sign-in, no advertising, and no analytics or tracking tools.

## The one number the app can share

By default, Money Guilt reports a single anonymous total to a server run by the developer. A report contains only:

1. **A random install ID.** It is generated on your Mac the first time a report is sent. It is not derived from your name, email, device, or bank.
2. **Your total wasted dollars**, the running sum of the transactions you marked wasteful.
3. **A transaction count**, how many transactions that total covers.
4. **A version number** for the report format.

It never includes merchant names, dates, individual purchase amounts, account details, balances, or anything from Plaid.

The server stores your latest report against your install ID and adds the totals from all installs together. The developer uses the combined figure for their own information. It is not sold, shared, or shown to other users.

Reports are sent over an encrypted (HTTPS) connection. As with any internet connection, the server's hosting provider may see your IP address in routine connection logs; the app does not store it with your report.

Reports are only sent when the developer has set up a collection server. If none is configured, nothing is sent.

### Turning it off

Sharing is on by default. You can turn it off at any time:

- click the Money Guilt icon in the menu bar and untick **Share Anonymous Wasted Total**, or
- click **Turn Off Sharing** in the notice shown the first time you open the app, or
- run `python3 telemetry.py disable` in the project folder.

Once off, no further reports are sent. Because the report is anonymous, turning sharing off does not remove a total you already sent. To have it deleted, contact the developer with your install ID (run `python3 telemetry.py status` to see it) and it will be removed.

## Feedback

**Send Feedback** in the menu bar opens a short form. What you type, and your email address if you choose to give one so the developer can reply, are sent to the developer's server and emailed to the developer. Nothing else is included: no spending data, no install ID. Feedback is kept so it can be read and answered, and you can ask for yours to be deleted (see Contact). If the form isn't available, the menu opens a draft email in your own mail app instead, and nothing is sent unless you send it.

## Plaid

To read your transactions, Money Guilt uses [Plaid](https://plaid.com). When you link a bank account, you do so through Plaid's own screens, and Plaid handles your bank login: Money Guilt never sees your bank username or password. Plaid receives and processes your information under its own privacy policy, available at https://plaid.com/legal. Money Guilt receives an access token from Plaid that lets it read your transactions, and keeps it in your Keychain.

## Security

Your data is protected by your Mac's own protections: file permissions that limit access to your user account, the Keychain for your Plaid token, and, if you have it turned on, FileVault disk encryption. The app also offers on-screen privacy options, such as hiding amounts when you are idle and keeping the widget out of screenshots and screen sharing. No software can promise complete security, and anyone with access to your unlocked Mac and user account can see what the app shows.

## Your choices

- **Stop sharing:** turn off sharing as described above.
- **Delete your data:** quit the app and delete `~/Library/Application Support/MoneyGuilt` and `~/Library/Logs/MoneyGuilt.log`. Remove the Money Guilt entry from Keychain Access if you want the access token gone too. You can also revoke Money Guilt's connection to your bank through Plaid.
- **Delete your shared total:** contact the developer, as described above.

## Children

Money Guilt is not directed at children under 13 and is not meant to be used by them.

## Changes

If this policy changes in a way that affects what is collected, the update will be posted here with a new effective date, and the app will tell you before it starts sending anything new.

## Contact

Questions or deletion requests: jordan@moneyguilt.com

---

*This document describes how the app is built. It is not legal advice; if you distribute Money Guilt widely, have a lawyer check that it meets the rules that apply where your users live.*
