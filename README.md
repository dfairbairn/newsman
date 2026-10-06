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

Stored bodies are sanitized to clean text (scripts/trackers/styling removed) and scanned for
prompt-injection indicators, which are recorded in the `injection_flags` column. Pass
`--no-sanitize` to store raw bodies.
