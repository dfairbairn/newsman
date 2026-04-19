import base64
import re

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

DEFAULT_LABELS = [
    "CHAT", "SENT", "INBOX", "IMPORTANT", "TRASH", "DRAFT", "SPAM",
    "CATEGORY_FORUMS", "CATEGORY_UPDATES", "CATEGORY_PERSONAL",
    "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "STARRED", "UNREAD",
]


def connect(token_path='token.json'):
    creds = Credentials.from_authorized_user_file(
        token_path, scopes=['https://www.googleapis.com/auth/gmail.readonly']
    )
    return build('gmail', 'v1', credentials=creds)


def fetch_labels(service, nondefault_only=True):
    results = service.users().labels().list(userId='me').execute()
    labels = results.get('labels', [])
    if nondefault_only:
        return [l for l in labels if l['name'] not in DEFAULT_LABELS]
    return labels


def fetch_messages(service, label, max_results=1, user_id='me'):
    """Return a list of full message dicts for the N most recent emails under label."""
    query = f'label:{label}'
    results = service.users().messages().list(
        userId=user_id, q=query, maxResults=max_results, includeSpamTrash=False
    ).execute()

    stubs = results.get('messages', [])
    if not stubs:
        return []

    return [
        service.users().messages().get(userId=user_id, id=s['id'], format='full').execute()
        for s in stubs
    ]


def decode_b64url(b64_data, charset='utf-8'):
    if isinstance(b64_data, str):
        b = b64_data.encode('ascii')
    else:
        b = b64_data
    b = re.sub(rb'\s+', b'', b)
    padding = (-len(b)) % 4
    if padding:
        b += b'=' * padding
    return base64.urlsafe_b64decode(b).decode(charset, errors='replace')
