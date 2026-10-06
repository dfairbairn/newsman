"""
Facade for email account operations.
Supports multiple backends (currently: oauth, imap). Extend by adding a new
backend module under access/ and a matching branch in AccountQuery.__init__.
"""
import argparse
import datetime
import os
import pickle
import re
import time
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

import toml

from access import oauth as access_oauth
from access import imap as access_imap
from storage.database import ensure_db
from storage.models import Email
from storage.sanitizer import sanitize_email

STORAGE_PATH = 'output'
CONFIG_PATH = 'newsletter.toml'
BODY_PART_SEP = '\n\n\n\nXXXXX\n\n\n\n'


def _pick_body(html_parts, text_parts) -> str:
    """Prefer HTML parts (richest for the sanitizer); fall back to plain text."""
    return BODY_PART_SEP.join(html_parts if html_parts else text_parts)


def _parse_duration(spec: str) -> datetime.datetime:
    """Parse a duration like '1w', '3d', '24h' into a past datetime (now - delta)."""
    m = re.fullmatch(r'(\d+)\s*([wdh])', spec.strip().lower())
    if not m:
        raise ValueError(f"Invalid duration '{spec}'; use e.g. 1w, 3d, 24h")
    n, unit = int(m.group(1)), m.group(2)
    delta = {'w': datetime.timedelta(weeks=n),
             'd': datetime.timedelta(days=n),
             'h': datetime.timedelta(hours=n)}[unit]
    return datetime.datetime.now() - delta


def load_config(path=CONFIG_PATH) -> dict:
    """Load newsletter.toml if present, else return an empty config."""
    if not os.path.exists(path):
        return {}
    return toml.load(path)


def _decode_mime_header(value: str) -> str:
    """Decode an RFC 2047 encoded header (e.g. '=?utf-8?q?...?=') to plain text."""
    if not value:
        return ''
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _collect_gmail_parts(payload, html_parts, text_parts):
    """Recursively gather decoded text/html and text/plain bodies from a Gmail payload."""
    mime = payload.get('mimeType', '')
    data = payload.get('body', {}).get('data', '')
    if data and mime == 'text/html':
        html_parts.append(access_oauth.decode_b64url(data))
    elif data and mime == 'text/plain':
        text_parts.append(access_oauth.decode_b64url(data))
    for part in payload.get('parts', []):
        _collect_gmail_parts(part, html_parts, text_parts)


def _parse_email(raw_message, label) -> Email:
    """Parse a Gmail API message dict (oauth backend) into an Email."""
    headers = {h['name']: h['value'] for h in raw_message['payload'].get('headers', [])}
    html_parts, text_parts = [], []
    _collect_gmail_parts(raw_message['payload'], html_parts, text_parts)
    message_id = next((v for k, v in headers.items() if k.lower() == 'message-id'), '')
    return Email(
        subject=headers.get('Subject', ''),
        body=_pick_body(html_parts, text_parts),
        timestamp=int(raw_message.get('internalDate', 0)) // 1000,
        sender=headers.get('From', ''),
        label=label,
        message_id=message_id,
    )


def _parse_email_imap(msg, label) -> Email:
    """Parse an IMAP RFC822 email.message.Message (imap backend) into an Email."""
    html_parts, text_parts = [], []
    for part in msg.walk():  # walk() yields the message itself if non-multipart
        ctype = part.get_content_type()
        disposition = str(part.get('Content-Disposition', ''))
        if 'attachment' in disposition or ctype not in ('text/plain', 'text/html'):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or 'utf-8'
        decoded = payload.decode(charset, errors='replace')
        (html_parts if ctype == 'text/html' else text_parts).append(decoded)

    timestamp = 0
    date_hdr = msg.get('Date', '')
    if date_hdr:
        try:
            timestamp = int(parsedate_to_datetime(date_hdr).timestamp())
        except (TypeError, ValueError):
            timestamp = 0

    return Email(
        subject=_decode_mime_header(msg.get('Subject', '')),
        body=_pick_body(html_parts, text_parts),
        timestamp=timestamp,
        sender=_decode_mime_header(msg.get('From', '')),
        label=label,
        message_id=(msg.get('Message-ID') or '').strip(),
    )


class AccountQuery:
    def __init__(self, backend, token_path='token.json', config=None):
        config = config if config is not None else load_config()
        if backend == 'oauth':
            service = access_oauth.connect(token_path)
            self._fetch_labels = lambda: access_oauth.fetch_labels(service)
            self._fetch_raw = lambda label, **kw: access_oauth.fetch_messages(service, label, **kw)
            self._parse = _parse_email
        elif backend == 'imap':
            imap_cfg = config.get('imap', {})
            host = imap_cfg.get('host', 'imap.gmail.com')
            user = imap_cfg.get('sender')
            # Gmail IMAP accepts only an App Password, not the account password,
            # so prefer app_password when present.
            password = imap_cfg.get('app_password') or imap_cfg.get('password')
            if not user or not password:
                raise ValueError(
                    "imap backend requires 'sender' and 'app_password' (or 'password') "
                    f"under [imap] in {CONFIG_PATH}"
                )
            conn = access_imap.connect(host, user, password)
            self._fetch_labels = lambda: access_imap.fetch_labels(conn)
            self._fetch_raw = lambda label, **kw: access_imap.fetch_messages(conn, label, **kw)
            self._parse = _parse_email_imap
        else:
            raise ValueError(f"Unknown backend: '{backend}'")

    def list_labels(self):
        labels = self._fetch_labels()
        if not labels:
            print("No labels found.")
            return
        for l in labels:
            print(l['name'])

    def user_labels(self) -> list[str]:
        """Label names to ingest when none is specified: user labels only, excluding
        Gmail system containers ([Gmail]/*) and default labels (INBOX, SENT, ...)."""
        names = [l['name'] for l in self._fetch_labels()]
        return [n for n in names
                if not n.startswith('[Gmail]') and n not in access_oauth.DEFAULT_LABELS]

    def list_emails(self, label, n=1) -> list[Email]:
        """Return the N most recent emails under label as Email objects."""
        return [self._parse(m, label) for m in self._fetch_raw(label, max_results=n)]

    def ingest(self, db, label=None, unread=False, since=None, max_results=None,
               sanitize=True) -> tuple[int, int]:
        """Fetch emails and store them via the DB. If label is None, ingest across all
        user labels. unread/since/max_results are passed to the backend; bodies are
        sanitized unless sanitize=False. Returns (newly_inserted, total_fetched);
        duplicates are skipped by (message_id, label)."""
        labels = [label] if label else self.user_labels()
        total_inserted = total_fetched = 0
        for lbl in labels:
            try:
                raw = self._fetch_raw(lbl, max_results=max_results, unseen_only=unread, since=since)
            except Exception as e:
                print(f"  ! skip '{lbl}': {e}")
                continue
            inserted = 0
            for r in raw:
                email = self._parse(r, lbl)
                if sanitize:
                    email.body = sanitize_email(email.body)
                if db.insert_email_obj(email) is not None:
                    inserted += 1
            total_inserted += inserted
            total_fetched += len(raw)
            print(f"  {lbl}: +{inserted} new / {len(raw)} fetched")
        return total_inserted, total_fetched

    def fetch_and_pickle(self, label, n=1):
        """Retrieve the N most recent emails under label and write each to a pickle file."""
        os.makedirs(STORAGE_PATH, exist_ok=True)
        raw_messages = self._fetch_raw(label, max_results=n)
        for raw in raw_messages:
            email = self._parse(raw, label)
            subject = re.sub(r'[^\w\-]', '_', email.subject or 'no_subject')[:30]
            label_slug = label.replace(' ', '_').replace('/', '.')
            fname = f"{label_slug}_{subject}_{email.timestamp}_{round(time.time())}.pkl"
            fpath = os.path.join(STORAGE_PATH, fname)
            with open(fpath, 'wb') as f:
                pickle.dump(raw, f)
            print(f"Wrote {fpath}")


def main():
    parser = argparse.ArgumentParser(description="Query an email account.")
    parser.add_argument('--backend', required=True, choices=['oauth', 'imap'],
                        help="Account access method (required)")
    sub = parser.add_subparsers(dest='command')

    sub.add_parser('labels', help="List all non-default labels")

    p_list = sub.add_parser('list', help="List recent emails under a label")
    p_list.add_argument('label')
    p_list.add_argument('-n', '--count', type=int, default=5)

    p_pickle = sub.add_parser('pickle', help="Fetch and write emails to pickle files")
    p_pickle.add_argument('label')
    p_pickle.add_argument('-n', '--count', type=int, default=1)

    p_ingest = sub.add_parser(
        'ingest', help="Fetch emails into the SQLite store (all user labels by default)")
    p_ingest.add_argument('label', nargs='?', default=None,
                          help="Label to ingest (default: all user labels)")
    p_ingest.add_argument('--unread', action='store_true',
                          help="Only unread messages")
    p_ingest.add_argument('--since', default=None,
                          help="Only messages newer than this, e.g. 1w, 3d, 24h "
                               "(default 1w; ignored if --unread with no --since)")
    p_ingest.add_argument('-n', '--count', type=int, default=None,
                          help="Cap messages per label (default: no cap)")
    p_ingest.add_argument('--no-sanitize', action='store_true',
                          help="Store raw bodies without running the sanitizer")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    q = AccountQuery(backend=args.backend)

    if args.command == 'labels':
        q.list_labels()

    elif args.command == 'list':
        emails = q.list_emails(args.label, args.count)
        if not emails:
            print("No emails found.")
        for e in emails:
            print(f"[{e.label}] {e.sender}  —  {e.subject}")

    elif args.command == 'pickle':
        q.fetch_and_pickle(args.label, args.count)

    elif args.command == 'ingest':
        # Timespan mode is the default; pure --unread (no --since) ingests all unread.
        if args.since:
            since = _parse_duration(args.since)
        elif not args.unread:
            since = _parse_duration('1w')
        else:
            since = None

        scope = args.label or 'ALL user labels'
        mode = []
        if args.unread:
            mode.append('unread')
        if since:
            mode.append(f"since {since:%Y-%m-%d}")
        print(f"Ingesting [{scope}] ({', '.join(mode) or 'all recent'})"
              f"{'' if not args.no_sanitize else ' [raw, no sanitize]'} ...")

        db = ensure_db()
        try:
            inserted, total = q.ingest(
                db, label=args.label, unread=args.unread, since=since,
                max_results=args.count, sanitize=not args.no_sanitize,
            )
            print(f"Done: {inserted} new / {total} fetched "
                  f"(skipped {total - inserted} duplicate(s)); {db.count_emails()} total in DB")
        finally:
            db.close()


if __name__ == '__main__':
    main()
