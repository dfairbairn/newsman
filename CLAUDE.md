# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A **newsletter ingestion and summarization system**: it ingests newsletter emails, cleans them, stores them in SQLite (`emails.db`), decomposes each into individual **stories** via Claude into a **separate** store (`summaries.db`), and answers natural-language questions over those stories. The email **source is an implementation detail behind a pluggable backend** (`access/`) — Gmail is the current backend, not the point of the project. Treat any specific inbox (Gmail OAuth, IMAP, etc.) as swappable. Early-stage: no lint config or build step yet, but there is a stdlib `unittest` suite under `tests/`.

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
python query_account.py sanitize -f file.eml       # sanitize a local .eml/.pkl and print result (no backend)
python query_account.py sanitize -p file.pkl --html  # ...as sanitized HTML instead of clean text

# summarization engine (needs [llm].api_key in newsletter.toml). Two-pass: Haiku counts
# stories per email, Sonnet extracts them (so roundups aren't collapsed into one story).
python summarize.py extract              # decompose ALL un-summarized emails into stories (Batch API)
python summarize.py extract Risky-Biz --sync -n 2   # one label, synchronous, cap 2 (cheap smoke test)
python summarize.py extract --quarantine-sketchy    # divert injection-flagged emails to quarantine
python summarize.py query "how many ransomware incidents in the last year? list them"
python summarize.py stats                # story counts per newsletter (+ quarantine count)
python summarize.py quarantine           # list emails diverted to the quarantine bin
python -m summaries.db                   # create/validate storage/summaries.db

python -m unittest discover -s tests          # run the full test suite (stdlib unittest)
python -m unittest tests.test_summaries -v    # just the summarization tests
```

## Architecture

The pipeline is **fetch → parse → sanitize → store (emails.db) → extract stories → store (summaries.db) → query**. The first half (through emails.db) is driven by `AccountQuery.ingest()` in `query_account.py`; `process_and_store()` is the shared per-email sanitize→scan→store step (also used by file ingest). The second half (stories + querying) lives in the `summaries/` package behind the `summarize.py` CLI.

- **`access/` — pluggable account backends.** Each module (`oauth.py`, `imap.py`) exposes `connect(...)`, `fetch_labels(x)`, `fetch_messages(x, label, max_results=None, unseen_only=False, since=None, mark_read=False)`. Both honor the same filters (imap via `UNSEEN`/`SINCE` search; oauth via `is:unread`/`after:`). Return shapes differ (oauth → Gmail API dicts; imap → `email.message.Message`), so each has its own parser. **mark_read:** default `False` ⇒ imap uses `BODY.PEEK[]` in a readonly mailbox (never marks read — used by `list`/`pickle`/`eml`); `True` ⇒ writable + `RFC822` fetch sets `\Seen` (oauth removes the `UNREAD` label). Note IMAP `SINCE` is day-granular, so `--since 24h` rounds to the day.
- **`query_account.py` — facade + CLI.** `AccountQuery.__init__` picks a backend, binds its fetch via `self._fetch_raw(label, **kw)`, selects the parser (`self._parse`); add a backend by writing `access/<name>.py` + a branch. `--backend` is optional at the argparse level but enforced in `main()` for account ops; **file ingest (`-f`/`-p`) needs no backend**. Config from `newsletter.toml` via `load_config()`. Parsers emit `storage.models.Email` (`message_id` from RFC822 header; body = HTML part if present else plain, via `_pick_body`); `_parse_any` dispatches by type so pickle/eml of either shape parse correctly. `ingest(db, label=None, unread, since, max_results, sanitize, mark_read=True, scanner)` loops over `user_labels()` (excludes `[Gmail]/*` and `DEFAULT_LABELS`) when no label given. **CLI defaults: timespan 1 week, and marks emails read** (`--keep-unread` to preserve, `--since`/`--unread` to change scope). There is also a `sanitize -f/-p [--html]` subcommand that sanitizes a local .eml/.pkl and prints it (no backend). `setup_logging()` reads `[logging].level` (default info) and, by default, **also writes to `[logging].file`** (default `logs/newsletter.log`; set `file = ""` to disable).
- **`storage/` — persistence + sanitization.** `models.py`: `Email` dataclass (+ `injection_flags`). `database.py`: `DatabaseManager` + `ensure_db()` over one `emails` table. **Idempotent**: `INSERT OR IGNORE` against a *partial* unique index on `(message_id, label)` (`WHERE message_id <> ''`) — one row per message per label; empty-message_id rows never collide. `init_db()` self-migrates (adds `message_id`/`injection_flags`, drops the old single-column index). Run as `python -m storage.database`.
- **`storage/sanitizer.py` — cleaning + anti-injection** (BeautifulSoup + bleach; needs `beautifulsoup4`, `bleach`, `html5lib`). `sanitize_to_text(raw)` is the ingest entry point: sanitizes HTML (removes `script`/`style`/`head`/dangerous tags *with contents*, strips `on*`/`style`/unsafe `src`/`href`, drops tracking params, removes images, re-linkifies) **then reduces to structure-preserving clean text** via `html_to_structured_text` — it keeps paragraph breaks and turns `<hr>` into a `---` divider (`hr` is in `ALLOWED_TAGS` so it survives bleach). That coarse structure is what the story **counter** relies on to see item boundaries in roundups; `html_to_text` (flat, single-line) is retained for other callers. `PromptInjectionScanner` applies static regex heuristics (instruction-override, role-reassignment, reveal/suppress, exfiltration, chat delimiters, unicode-tag chars, long base64) returning indicator names; ingest records them in `emails.injection_flags` and logs a warning. It flags only — never mutates content.
- **`summaries/` — summarization engine** (needs `anthropic`, `pydantic`). `models.py`: Pydantic `Story` (title, summary, `category` enum, keywords, urls) + `NewsletterExtraction`. `db.py`: `SummaryDB`/`ensure_summary_db()` over the **separate** `storage/summaries.db` — `stories` + `story_keywords` + an **FTS5** index + a `summarized_emails` ledger keyed by `(message_id, label)` + a **`quarantine`** table; read-only `search_stories`/`count_stories`/`list_labels` back the query tools (FTS text sanitized into quoted tokens). **`extract.py` — two-pass, idempotent:** a cheap **counter** pass (`count_model`, default `claude-haiku-5-5`) estimates how many stories an email holds (+ whether it's a list), then an **extractor** pass (`extract_model`, default `claude-sonnet-5-5`) splits/summarizes guided by that count — so link-roundups aren't collapsed and long essays stay one story. Both use **structured outputs** (`output_config.format` + a hand-built strict schema), *not* forced `tool_choice` (the 5.5 models reject forced tool use) and omit `thinking` (its "off" value differs per model). Default path is the **Batch API** (extractor) with the counter run synchronously; `--sync` does both inline. **`--quarantine-sketchy`** diverts emails whose `injection_flags` are set to the quarantine bin and records them in the ledger so future runs skip them. `query.py`: `run_query()` uses the SDK **Tool Runner** with `@beta_tool` read-only tools; cites each story with a Gmail `rfc822msgid` deep link. Models + `[llm].api_key` (or `anthropic_api_key`) come from `newsletter.toml`.
- **`summarize.py` — CLI** (replaces the former `llm_summarize.py`): `extract` (`--sync`, `--quarantine-sketchy`), `query`, `stats`, `quarantine`.
- **`tests/` — stdlib `unittest` suite** (`test_newsletter.py` + `test_summaries.py`, builders in `helpers.py`; no pytest/deps). Covers parsers, sanitizer, injection scanner, DB dedup/migration, logging, file loaders, and the summaries layer (FTS, keyword/label/date filters, composite-key ledger, extraction plumbing, query helpers) — all **without network/API calls**. `TestGroundTruth` validates parsing against two real `.eml` newsletters in `misc/ground_truth/` (exact headers, no body truncation, ≥90% plain-text word coverage). Run from the repo root so imports resolve.

## Secrets & environment

- `.gitignore` covers `newsletter.toml`, `token.json`, and `storage/*.db`. **Not yet ignored**: `misc/` (holds `client3_secret.json` + an old token) — don't `git add` it.
- `newsletter.toml` is loaded by `load_config()`. For Gmail IMAP, `[imap].app_password` must be a 16-char App Password (2-Step Verification required); the real account password is rejected by Gmail's IMAP. `[llm].api_key` holds the Anthropic key for `summarize.py` (one `[llm]` table only — TOML rejects duplicates).
- `util_create_token.py` (oauth bootstrap) reads `client3_secret.json` from the **current directory**, but the file lives in `misc/` — copy it to the repo root (or fix the path) before running.
- **Dev environment note:** the in-repo `venv/` was created under WSL (its interpreter symlinks resolve to `/usr/bin/python3.14`), so it does **not** run from Windows PowerShell or Git Bash. Invoke it through WSL, e.g. `wsl.exe bash -c "cd /mnt/c/.../newsletter-aggregator && venv/bin/python ..."`.
