# finance-bot

Claude-powered Discord bot, written in Python, for personal finance.

## Features

* Queries and modifies the finance spreadsheet (`spreadsheet.ods`) through the `claude-sheets` CLI (LibreOffice UNO).
* On request, creates skills and Python scripts so the bot can do new recurring things with the data. Every skill write and every script run needs Approve/Reject in Discord.
* Only allowlisted Discord users are served. Per-channel history, wiped after 30 idle minutes or by `!reset`.
* Accepts attachments (CSV/OFX/QIF/JSON/text, PDF, images, spreadsheets) up to 25 MB, saved to `inbox/` until the conversation is wiped.

## Quickstart

```
git clone --recursive <repo> && cd finance-bot
cp template.ods spreadsheet.ods
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in; loaded automatically
cd src && ../.venv/bin/python -m finance_bot
```

`.env` at the repo root is loaded at startup; real environment variables take precedence.

Needs LibreOffice and `python3-uno` on the system. Setup details, env vars, approval flow, systemd: [doc/usage.md](doc/usage.md). Architecture: [doc/development.md](doc/development.md).

Tests: `.venv/bin/python -m unittest discover -s tests`

## Spreadsheet

`spreadsheet.ods` (your data) is gitignored. `template.ods` is the public starting point: same sheets, formulas and formatting, all personal inputs cleared, owners named Person 1 / Person 2. Empty inputs show `#DIV/0!`/`#N/A` until filled in.
