# Newsletter Aggregator

Pulls newsletter emails from an inbox, sanitizes them, and stores them in SQLite for later summarization.

## Setup

```bash
pip install -r requirements.txt
cp newsletter.toml.example newsletter.toml   # then fill in your credentials
```

For Gmail IMAP, `[imap].app_password` must be a 16-char [App Password](https://myaccount.google.com/apppasswords) (requires 2-Step Verification). Set `[logging].level` to `debug` for verbose output.

## Query an inbox

```bash
python query_account.py --backend imap labels              # list labels
python query_account.py --backend imap list <label> -n 5   # preview recent emails
```

## Ingest emails

`ingest` runs fetch → sanitize → store. With no label it covers all user labels.

```bash
python query_account.py --backend imap ingest                 # last 1 week (default)
python query_account.py --backend imap ingest --since 3d      # last 3 days (w/d/h)
python query_account.py --backend imap ingest --unread        # all unread
python query_account.py --backend imap ingest <label> -n 10   # one label, cap 10
```

By default, ingested emails are **marked read**. To leave them unread:

```bash
python query_account.py --backend imap ingest --unread --keep-unread
```

## Export / import

```bash
python query_account.py --backend imap eml <label> -n 1       # save as .eml -> output/
python query_account.py --backend imap pickle <label> -n 1    # save as .pkl -> output/

python query_account.py ingest <label> -f some.eml            # ingest a local .eml
python query_account.py ingest <label> -p some.pkl            # ingest a local .pkl
```

Stored bodies are sanitized to clean (structure-preserving) text — scripts/trackers/styling
removed, paragraph breaks and `---` section dividers kept — and scanned for prompt-injection
indicators (recorded in `injection_flags`). Pass `--no-sanitize` to store raw bodies.

To sanitize a raw email on disk without ingesting:

```bash
python query_account.py sanitize -f some.eml          # print cleaned text + injection flags
python query_account.py sanitize -p some.pkl --html   # ...as sanitized HTML instead
```

## Summarize & query

Decompose ingested emails into individual **stories** (stored in a separate `summaries.db`),
then ask natural-language questions. Requires `[llm].api_key` (an Anthropic key) in
`newsletter.toml`. Extraction is **two-pass**: a cheap model (Haiku) counts the stories in each
email, then Sonnet extracts them — so roundup/"in other news" newsletters aren't collapsed into
one story, while long essays stay a single summarized story.

```bash
python summarize.py extract                       # decompose all un-summarized emails (Batch API, 50% cost)
python summarize.py extract Risky-Biz --sync -n 2 # one label, synchronous, cap 2 (quick/cheap)
python summarize.py extract --quarantine-sketchy  # divert injection-flagged emails to quarantine
python summarize.py query "how many ransomware incidents in the last year? list them with links"
python summarize.py query "what trended on Chinese social media over the last year?"
python summarize.py stats                         # story counts per newsletter (+ quarantine count)
python summarize.py quarantine                    # list quarantined emails
```

Extraction is idempotent (re-runs skip already-processed emails). With `--quarantine-sketchy`,
emails flagged by the injection scanner are diverted to a quarantine bin and never summarized.
Query answers cite each story with its newsletter, date, URLs, and a Gmail link back to the
original email. Logs are written to `logs/newsletter.log` by default.
