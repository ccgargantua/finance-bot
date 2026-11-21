# Development

## Layout

```
src/finance_bot/
  __main__.py        entry: load config, start bot
  config.py          .env loading, env -> frozen Config, fail-fast validation, constants (max_tokens, iteration cap)
  discord_client.py  FinanceBot: gating, !reset, per-channel ConversationStore + lock, typing, reply splitting, attachment download
  attachments.py     pure: classify/sanitize/unique_name, content-block building, size estimate (UserTurn)
  inbox.py           inbox/<channel_id>/ save + wipe (filesystem)
  messaging.py       pure: split_message, strip_mention, should_respond
  conversation.py    pure: Conversation (append-only messages + frozen system), ConversationStore (idle wipe, size ceiling, on_wipe hook)
  agent.py           run_turn: manual agentic loop, stop_reason handling, rollback
  prompt.py          system prompt: role + sheet reference from claude-sheets SKILL.md + skill index snapshot
  approval.py        discord Approve/Reject view, request_approval; Approver type
  procs.py           run_capture (asyncio subprocess, timeout, truncation), child_env
  tools/registry.py  constant TOOL_SCHEMAS + dispatch(name, input) -> ToolOutput
  tools/sheet.py     ALLOWED_SHEET_ACTIONS, build_sheet_argv, sheet_run
  tools/skills.py    skill store: validation, path safety, list/read/write/diff, script_run
skills/<name>/       SKILL.md (+ *.py); gitignored
tests/test_logic.py  unittest, no network (fake API client)
```

Run from `src/` (`python -m finance_bot`); modules import as `finance_bot.*`. Tests: `.venv/bin/python -m unittest discover -s tests` (49 tests). Under system `python3` the 7 agent-loop tests skip (no `anthropic`).

## Agent loop

Manual loop on `AsyncAnthropic().beta.messages.stream(...)` + `get_final_message()` (anthropic 2.6.0), not the tool runner: the runner's internal message list is not exposed, and dropping tool turns edits history. Request: `thinking={"type":"adaptive"}`, `output_config={"effort": EFFORT}`, `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`, no forced `tool_choice`, no thinking budget. Per turn: up to 25 model calls; all `tool_use` blocks of a response are executed in order and answered in one user message of `tool_result` blocks (`is_error: true` on failures, including user-rejected approvals).

**History is append-only.** Opus 5.5 binds thinking blocks to the exact prefix (system, tools, earlier messages) that produced them; editing, reordering or removing earlier turns makes later thinking blocks 400 on new accounts. So per channel a `Conversation` holds messages that are only ever appended (every `response.content` unchanged, every `tool_result` message), and the `system` blocks and `TOOL_SCHEMAS` are frozen when the conversation starts. The skill index in `system` is therefore a snapshot (the prompt says so; `list_skills` is live).

**Failure handling.** Before each turn the loop records a mark. On refusal, `max_tokens`, `anthropic.APIError` or any exception, the conversation is truncated to the mark (whole-turn rollback). That only drops a tail that was never replayed, so remaining thinking blocks stay valid, and no `tool_use` is left without its `tool_result`. Cost: sheet edits made earlier in a rolled-back turn stay in the file but the model forgets them; the user is told the exchange was dropped. At the iteration cap the history already ends with an answered `tool_result` message, so it is kept as is; the next user message is merged into that user turn by the API.

**Lifetime.** `ConversationStore.acquire` runs at the start of each message: a conversation idle longer than `HISTORY_IDLE_MINUTES` (default 30) is discarded (checked lazily, so no timer), and one over `HISTORY_MAX_CHARS` (default 400000, approx 100k tokens, measured as serialized block size) is discarded with a notice in the reply. The ceiling is checked only at the start of a message, so one turn can grow past it; each tool output is truncated to 20000 chars (`procs.OUTPUT_LIMIT`). A wipe starts a new conversation with a fresh system prompt. Nothing is trimmed from the front or middle. `!reset` wipes it too (each wipe also deletes the channel's inbox). State is in memory only.

Turns per channel are serialised with a lock. The prompt-cache breakpoint is on the static system block (covers tools + it).

## Attachments

Allowed users' attachments (a message may have no text) are handled in `FinanceBot.collect_turn`, inside the channel lock after `acquire`. Per attachment: `rejection(name, attachment.size)` runs before download (unsupported extension, over 25 MB -> not saved, note block to the model); else `read()`, `inbox.save` to `inbox/<channel_id>/<sanitized name>` (`[A-Za-z0-9._-]`, no path parts, `-N` suffix on collision; repo-root-relative path, scripts run with cwd = repo root), then blocks: `Attachment: <name> saved at <path> (<size>, <kind>)` + optional inline block. Text (.csv .tsv .txt .ofx .qfx .qif .json; UTF-8, errors replaced): inline up to 20000 chars, else a truncated preview. PDF: base64 `document` if <= 20 MB. Images (.png .jpg .jpeg .gif .webp): base64 `image` if <= 5 MB. Per message, inlined PDF+image bytes are capped at 20 MB (`MAX_MESSAGE_INLINE_BYTES`, base64 ~27 MB < 32 MB request limit): `collect_turn` passes a decreasing budget in attachment order; later ones are saved only. Text is exempt. .xlsx .xls .ods: save only. `run_turn(..., user_content, size)` takes a str or block list; `size` overrides `measure` so the ceiling ignores base64 (image = 6400 chars, PDF = 12000 per page counted by `/Type /Page`, min 1); API payload dicts carry no extra fields. Base64 stays in history and is resent each call.

`inbox/<channel_id>/` is deleted via `ConversationStore(on_wipe=...)` on `!reset`, idle wipe and size-ceiling wipe; all of `inbox/` is cleared at startup (conversations are in memory). `inbox/` is gitignored.

## Tools

| tool | args | notes |
|---|---|---|
| `sheet` | `action`, `args[]` | argv `[SHEET_PYTHON, sheet.py, action, SPREADSHEET_PATH, *args]`, no shell, 120 s timeout. Action must be in `ALLOWED_SHEET_ACTIONS` (all CLI actions except create, convert, status, stop). `--ops-file` and `--path` rejected (arbitrary file reads). |
| `list_skills` | | name + description |
| `read_skill` | `name` | SKILL.md + script sources |
| `propose_skill` | `name`, `skill_md`, `scripts{}` | validates, posts for approval, writes on approve; existing skill = update with diff |
| `run_script` | `skill`, `script`, `args[]` | approval before each run |

Tool failures return `is_error: true` results. When adding a sheet action to the CLI, add it to `ALLOWED_SHEET_ACTIONS`; the prompt picks up its docs from the submodule automatically.

## Skills

`skills/` is gitignored (except `.gitkeep`): skills are private runtime state, never committed. A skill is `skills/<name>/SKILL.md` with frontmatter `name` (== directory) and `description`, plus optional `*.py`. Names `[a-z0-9-]{1,64}`; scripts `[A-Za-z0-9_-]+.py`, no paths. Every path is resolved and must have the skills dir (or the skill dir) as direct parent, so symlinks out are rejected. Limits: SKILL.md 20k chars, scripts 50k chars each, 10 per skill. The index (name: description) is snapshotted into `system` when a conversation starts (see [Agent loop](#agent-loop)); a skill approved mid-conversation appears in the prompt after the next wipe, and `list_skills` is always live. Bodies load via `read_skill`.

Scripts get env `SPREADSHEET_PATH`, `SHEET_PYTHON`, `SHEET_CLI` and should call the CLI as `[SHEET_PYTHON, SHEET_CLI, action, SPREADSHEET_PATH, ...]`, stdlib only.

`prompt.py` extracts the `# Disambiguation` through `# Actions...` sections of `claude-sheets/.claude/skills/sheet/SKILL.md` (everything before `# Example`), dropping the test-harness Rules. If those headings change, startup fails loudly.

## Dependencies

`requirements.txt`: `anthropic==2.6.0`, `discord.py==2.7.1`, `httpx2>=2.12.0`. Install into `.venv` (Debian system python is PEP 668 managed).

- `anthropic` is pinned to 2.6.0, the newest release at least 2 weeks old at setup (2026-11-20); 2.7+ were too new. The request shape (betas, `fallbacks`, `thinking`, `output_config`) was verified against 2.6.0's signatures. Re-verify before bumping.
- `httpx2>=2.12.0` is a security floor (5 advisories fixed by 2.12.0); it currently resolves to 2.16.2, accepted by the user.
- New dependencies: audit (authenticity, advisories, release age >= 2 weeks) before installing.

## Security notes

Only `ALLOWED_USER_IDS` are processed or may click buttons. Generated scripts are unsandboxed Python run as the bot user: approval is the only gate, so read the attachments. Child processes get an allowlisted env.
