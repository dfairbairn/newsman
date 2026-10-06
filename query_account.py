"""
Facade for email account operations.
Supports multiple backends (currently: oauth, imap). Extend by adding a new
backend module under access/ and a matching branch in AccountQuery.__init__.
"""
import argparse
import os
import pickle
import re
import time
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

import toml

from access import oauth as access_oauth
from access import imap as access_imap
from storage.models import Email

STORAGE_PATH = 'output'
CONFIG_PATH = 'newsletter.toml'
BODY_PART_SEP = '\n\n\n\nXXXXX\n\n\n\n'


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


def _parse_email(raw_message, label) -> Email:
    """Parse a Gmail API message dict (oauth backend) into an Email."""
    headers = {h['name']: h['value'] for h in raw_message['payload'].get('headers', [])}
    body_parts = []
    for part in raw_message['payload'].get('parts', []):
        data = part['body'].get('data', '')
        if data:
            body_parts.append(access_oauth.decode_b64url(data))
    return Email(
        subject=headers.get('Subject', ''),
        body=BODY_PART_SEP.join(body_parts),
        timestamp=int(raw_message.get('internalDate', 0)) // 1000,
        sender=headers.get('From', ''),
        label=label,
    )


def _parse_email_imap(msg, label) -> Email:
    """Parse an IMAP RFC822 email.message.Message (imap backend) into an Email."""
    body_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disposition = str(part.get('Content-Disposition', ''))
            if ctype in ('text/plain', 'text/html') and 'attachment' not in disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    body_parts.append(payload.decode(charset, errors='replace'))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            body_parts.append(payload.decode(charset, errors='replace'))

    timestamp = 0
    date_hdr = msg.get('Date', '')
    if date_hdr:
        try:
            timestamp = int(parsedate_to_datetime(date_hdr).timestamp())
        except (TypeError, ValueError):
            timestamp = 0

    return Email(
        subject=_decode_mime_header(msg.get('Subject', '')),
        body=BODY_PART_SEP.join(body_parts),
        timestamp=timestamp,
        sender=_decode_mime_header(msg.get('From', '')),
        label=label,
    )


class AccountQuery:
    def __init__(self, backend, token_path='token.json', config=None):
        config = config if config is not None else load_config()
        if backend == 'oauth':
            service = access_oauth.connect(token_path)
            self._fetch_labels = lambda: access_oauth.fetch_labels(service)
            self._fetch_raw = lambda label, n: access_oauth.fetch_messages(service, label, n)
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
            self._fetch_raw = lambda label, n: access_imap.fetch_messages(conn, label, n)
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

    def list_emails(self, label, n=1) -> list[Email]:
        """Return the N most recent emails under label as Email objects."""
        return [self._parse(m, label) for m in self._fetch_raw(label, n)]

    def fetch_and_pickle(self, label, n=1):
        """Retrieve the N most recent emails under label and write each to a pickle file."""
        os.makedirs(STORAGE_PATH, exist_ok=True)
        raw_messages = self._fetch_raw(label, n)
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


if __name__ == '__main__':
    main()
