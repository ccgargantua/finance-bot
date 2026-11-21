# Project Structure
./ (root)
| - doc/
| - src/finance_bot/
| - skills/
| - tests/
| - claude-sheets/ (git submodule)
| - template.ods
| - requirements.txt
| - .env.example
| - CLAUDE.md
| - HANDOFF.md
| - README.md

* `doc/` contains documentation for both usage and development
* `src/finance_bot/` contains the bot (modules listed in `doc/development.md`); run with `python -m finance_bot` from `src/`
* `skills/` holds bot skills (`<name>/SKILL.md` + scripts), written at runtime after Discord approval
* `tests/` contains stdlib unittest tests
* `claude-sheets/` is the spreadsheet CLI submodule
* `template.ods` is the public spreadsheet template; `spreadsheet.ods` (real data) is gitignored and must never be committed or modified by tooling other than the bot
* `HANDOFF.md` contains context from the previous claude session. Read this.
* `README.md` user-facing README.md

# Response Style

Casual yet direct, minimal yet comprehensive. Every additional character is a waste of user's time and tokens,
both precious resources. Don't miss any details, and turn your critical assumptions into questions, rather than
hoping your training is enough to infer. When asked to review something (yours, the user's, LLM-generated,
doesn't matter) be thorough but don't scrutinize to infinity. What's important is addressing critical flaws,
not scrutinizing minor details with little side effect.
