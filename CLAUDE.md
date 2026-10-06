# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A **newsletter ingestion and summarization system**: it ingests newsletter emails, cleans them, stores them in SQLite, and (planned) summarizes them with an LLM. The email **source is an implementation detail behind a pluggable backend** (`access/`) — Gmail is the current backend, not the point of the project. Treat any specific inbox (Gmail OAuth, IMAP, etc.) as swappable. Early-stage: no lint config or build step yet, but there is a stdlib `unittest` suite under `tests/`.

## Commands

```bash
pip install -r requirements.txt          # install deps (Python 3.10+; code uses list[...] generics)

python util_create_token.py              # one-time OAuth bootstrap -> writes token.json

# --backend (oauth|imap) required for account ops; NOT for file ingest (-f/-p)
python query_account.py --backend imap labels            # list non-default labels/folders
python query_account.py --backend imap list <label> -n 5 # print N recent emails under a label
python query_account.py --backend imap pickle <label>    # dump raw messages to output/*.pkl
python query_account.py --backend imap eml <label>       # dump raw messages to output/*.eml

# ingest = fetch -> sanitize(to clean text) -> scan injection -> store (idempotent). No label = ALL user labels.
python query_account.py --backend imap ingest                    # timespan mode, default last 1 week
python query_account.py --backend imap ingest --since 3d         # last 3 days (units: w/d/h)
python query_account.py --backend imap ingest --unread           # ALL unread, every label, no time limit
python query_account.py --backend imap ingest --unread --keep-unread  # don't mark read (default marks read)
python query_account.py --backend imap ingest -n 5 --no-sanitize # cap per label; store raw
python query_account.py ingest <label> -f file.eml               # ingest a local .eml (no backend)
python query_account.py ingest <label> -p file.pkl               # ingest a local pickle (no backend)

python -m storage.database               # create/migrate/validate storage/emails.db (run as module)
python -m storage.sanitizer              # sanitizer + injection-scanner self-test

python -m unittest tests.test_newsletter -v   # run the test suite (132 tests, stdlib unittest)
python -m unittest discover -s tests          # same, via discovery
```

## Architecture

The flow is **fetch → parse → sanitize → store → summarize**, with the fetch layer abstracted behind a backend interface. `AccountQuery.ingest()` in `query_account.py` runs it end to end; `process_and_store()` is the shared per-email sanitize→scan→store step (also used by file ingest).

- **`access/` — pluggable account backends.** Each module (`oauth.py`, `imap.py`) exposes `connect(...)`, `fetch_labels(x)`, `fetch_messages(x, label, max_results=None, unseen_only=False, since=None, mark_read=False)`. Both honor the same filters (imap via `UNSEEN`/`SINCE` search; oauth via `is:unread`/`after:`). Return shapes differ (oauth → Gmail API dicts; imap → `email.message.Message`), so each has its own parser. **mark_read:** default `False` ⇒ imap uses `BODY.PEEK[]` in a readonly mailbox (never marks read — used by `list`/`pickle`/`eml`); `True` ⇒ writable + `RFC822` fetch sets `\Seen` (oauth removes the `UNREAD` label). Note IMAP `SINCE` is day-granular, so `--since 24h` rounds to the day.
- **`query_account.py` — facade + CLI.** `AccountQuery.__init__` picks a backend, binds its fetch via `self._fetch_raw(label, **kw)`, selects the parser (`self._parse`); add a backend by writing `access/<name>.py` + a branch. `--backend` is optional at the argparse level but enforced in `main()` for account ops; **file ingest (`-f`/`-p`) needs no backend**. Config from `newsletter.toml` via `load_config()`. Parsers emit `storage.models.Email` (`message_id` from RFC822 header; body = HTML part if present else plain, via `_pick_body`); `_parse_any` dispatches by type so pickle/eml of either shape parse correctly. `ingest(db, label=None, unread, since, max_results, sanitize, mark_read=True, scanner)` loops over `user_labels()` (excludes `[Gmail]/*` and `DEFAULT_LABELS`) when no label given. **CLI defaults: timespan 1 week, and marks emails read** (`--keep-unread` to preserve, `--since`/`--unread` to change scope). `setup_logging()` reads `[logging].level` (default info).
- **`storage/` — persistence + sanitization.** `models.py`: `Email` dataclass (+ `injection_flags`). `database.py`: `DatabaseManager` + `ensure_db()` over one `emails` table. **Idempotent**: `INSERT OR IGNORE` against a *partial* unique index on `(message_id, label)` (`WHERE message_id <> ''`) — one row per message per label; empty-message_id rows never collide. `init_db()` self-migrates (adds `message_id`/`injection_flags`, drops the old single-column index). Run as `python -m storage.database`.
- **`storage/sanitizer.py` — cleaning + anti-injection** (BeautifulSoup + bleach; needs `beautifulsoup4`, `bleach`, `html5lib`). `sanitize_to_text(raw)` is the ingest entry point: sanitizes HTML (removes `script`/`style`/`head`/dangerous tags *with contents*, strips `on*`/`style`/unsafe `src`/`href`, drops tracking params, removes images, re-linkifies) **then reduces to clean text** (strips zero-width/invisible spacer chars, collapses whitespace) — stored bodies are small, low-noise, summarizer-ready. `PromptInjectionScanner` applies static regex heuristics (instruction-override, role-reassignment, reveal/suppress, exfiltration, chat delimiters, unicode-tag chars, long base64) returning indicator names; ingest records them in `emails.injection_flags` and logs a warning. It flags only — never mutates content.
- **`llm_summarize.py` — empty placeholder** for the summarization stage.
- **`tests/` — stdlib `unittest` suite** (`test_newsletter.py`, builders in `helpers.py`; no pytest/deps). 132 tests: parsers, sanitizer, injection scanner, DB dedup/migration, logging, file loaders. `TestGroundTruth` validates parsing against two real `.eml` newsletters in `misc/ground_truth/` — asserts exact subject/sender/message_id/timestamp, that the stored body equals the full decoded HTML part (no truncation), and that ≥90% of the plain-text part's words survive sanitization (content-loss guard). Run from the repo root so `import query_account` resolves.

## Secrets & environment

- `.gitignore` covers `newsletter.toml` and `token.json`. **Not yet ignored**: `storage/emails.db` and `misc/` (holds `client3_secret.json` + an old token) — don't `git add` them.
- `newsletter.toml` is loaded by `load_config()`. For Gmail IMAP, `[imap].app_password` must be a 16-char App Password (2-Step Verification required); the real account password is rejected by Gmail's IMAP.
- `util_create_token.py` (oauth bootstrap) reads `client3_secret.json` from the **current directory**, but the file lives in `misc/` — copy it to the repo root (or fix the path) before running.
- **Dev environment note:** the in-repo `venv/` was created under WSL (its interpreter symlinks resolve to `/usr/bin/python3.14`), so it does **not** run from Windows PowerShell or Git Bash. Invoke it through WSL, e.g. `wsl.exe bash -c "cd /mnt/c/.../newsletter-aggregator && venv/bin/python ..."`.
