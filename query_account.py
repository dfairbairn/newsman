"""
Facade for email account operations.
Supports multiple backends (currently: oauth). Extend by adding a new backend
module (e.g. access_imap.py) and a matching branch in AccountQuery.__init__.
"""
import argparse
import os
import pickle
import re
import time

import access_oauth
from storage.models import Email

STORAGE_PATH = 'output'


def _parse_email(raw_message, label) -> Email:
    headers = {h['name']: h['value'] for h in raw_message['payload'].get('headers', [])}
    body_parts = []
    for part in raw_message['payload'].get('parts', []):
        data = part['body'].get('data', '')
        if data:
            body_parts.append(access_oauth.decode_b64url(data))
    return Email(
        subject=headers.get('Subject', ''),
        body='\n\n\n\nXXXXX\n\n\n\n'.join(body_parts),
        timestamp=int(raw_message.get('internalDate', 0)) // 1000,
        sender=headers.get('From', ''),
        label=label,
    )


class AccountQuery:
    def __init__(self, backend='oauth', token_path='token.json'):
        if backend == 'oauth':
            service = access_oauth.connect(token_path)
            self._fetch_labels = lambda: access_oauth.fetch_labels(service)
            self._fetch_raw = lambda label, n: access_oauth.fetch_messages(service, label, n)
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
        return [_parse_email(m, label) for m in self._fetch_raw(label, n)]

    def fetch_and_pickle(self, label, n=1):
        """Retrieve the N most recent emails under label and write each to a pickle file."""
        os.makedirs(STORAGE_PATH, exist_ok=True)
        raw_messages = self._fetch_raw(label, n)
        for msg in raw_messages:
            headers = {h['name']: h['value'] for h in msg['payload'].get('headers', [])}
            subject = re.sub(r'[^\w\-]', '_', headers.get('Subject', 'no_subject'))[:30]
            email_ts = int(msg.get('internalDate', 0)) // 1000
            label_slug = label.replace(' ', '_').replace('/', '.')
            fname = f"{label_slug}_{subject}_{email_ts}_{round(time.time())}.pkl"
            fpath = os.path.join(STORAGE_PATH, fname)
            with open(fpath, 'wb') as f:
                pickle.dump(msg, f)
            print(f"Wrote {fpath}")


def main():
    parser = argparse.ArgumentParser(description="Query an email account.")
    parser.add_argument('--backend', default='oauth', choices=['oauth'],
                        help="Account access method (default: oauth)")
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
