"""CLI for the newsletter summarization engine.

    python summarize.py extract [label] [--since 1w] [-n N] [--sync]
    python summarize.py query "how many ransomware incidents in the last year?"
    python summarize.py stats

Extraction decomposes ingested emails (emails.db) into individual stories stored
in the separate summaries.db; query answers natural-language questions over them.
"""
import argparse

from query_account import load_config, setup_logging, _parse_duration


def main():
    parser = argparse.ArgumentParser(description="Newsletter summarization engine.")
    sub = parser.add_subparsers(dest='command')

    p_ex = sub.add_parser('extract', help="Decompose ingested emails into stories")
    p_ex.add_argument('label', nargs='?', default=None,
                      help="Only this newsletter label/substring (default: all)")
    p_ex.add_argument('--since', default=None, help="Only emails newer than e.g. 1w, 3d, 24h")
    p_ex.add_argument('-n', '--limit', type=int, default=None, help="Cap emails processed")
    p_ex.add_argument('--sync', action='store_true',
                      help="Synchronous extraction (default: Batch API, 50%% cheaper)")
    p_ex.add_argument('--quarantine-sketchy', action='store_true',
                      help="Divert injection-flagged emails to the quarantine bin instead of "
                           "summarizing them (they stay excluded from future runs)")

    p_q = sub.add_parser('query', help="Ask a natural-language question of the archive")
    p_q.add_argument('question')

    sub.add_parser('stats', help="Show story counts by newsletter")
    sub.add_parser('quarantine', help="List emails diverted to the quarantine bin")

    args = parser.parse_args()
    cfg = load_config()
    setup_logging(cfg)

    if not args.command:
        parser.print_help()
        return

    if args.command == 'extract':
        from summaries.extract import run_extract
        since_ts = None
        if args.since:
            since_ts = int(_parse_duration(args.since).timestamp())
        emails, stories, quarantined = run_extract(
            label=args.label, since=since_ts, limit=args.limit, sync=args.sync,
            quarantine_sketchy=args.quarantine_sketchy, config=cfg)
        msg = f"Extracted {stories} story(ies) from {emails} email(s)."
        if quarantined:
            msg += f" Quarantined {quarantined} flagged email(s)."
        print(msg)

    elif args.command == 'query':
        from summaries.query import run_query
        print(run_query(args.question, config=cfg))

    elif args.command == 'stats':
        from summaries.db import ensure_summary_db
        db = ensure_summary_db()
        try:
            print(f"Total stories: {db.total_stories()}")
            for row in db.list_labels():
                print(f"  {row['stories']:>4}  {row['label']}")
            qn = db.quarantine_count()
            if qn:
                print(f"Quarantined emails: {qn}  (see `summarize.py quarantine`)")
        finally:
            db.close()

    elif args.command == 'quarantine':
        from summaries.db import ensure_summary_db
        import datetime
        db = ensure_summary_db()
        try:
            rows = db.list_quarantine()
            if not rows:
                print("Quarantine bin is empty.")
            for r in rows:
                when = datetime.datetime.fromtimestamp(r['quarantined_ts']).strftime('%Y-%m-%d')
                print(f"[{when}] {r['label']} | {r['subject']}")
                print(f"    flags: {r['injection_flags']}")
        finally:
            db.close()


if __name__ == '__main__':
    main()
