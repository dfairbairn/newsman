"""Shared fixtures/builders for the test suite."""
import base64
import glob
import os
from email.message import EmailMessage

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GROUND_TRUTH_DIR = os.path.join(REPO_ROOT, 'misc', 'ground_truth')


def _b64url(text: str) -> str:
    """Encode text the way the Gmail API returns part bodies (urlsafe base64)."""
    return base64.urlsafe_b64encode(text.encode('utf-8')).decode('ascii')


def make_gmail_message(subject='S', sender='a@b.com', message_id='<mid@x>',
                       html='<p>hi</p>', text='hi', internal_date_ms=1700000000000,
                       nested=False):
    """Build a dict shaped like a Gmail API messages.get(format='full') response."""
    headers = [
        {'name': 'Subject', 'value': subject},
        {'name': 'From', 'value': sender},
        {'name': 'Message-ID', 'value': message_id},
    ]
    parts = []
    if text is not None:
        parts.append({'mimeType': 'text/plain', 'body': {'data': _b64url(text)}})
    if html is not None:
        parts.append({'mimeType': 'text/html', 'body': {'data': _b64url(html)}})

    if nested:
        payload = {
            'mimeType': 'multipart/mixed',
            'headers': headers,
            'parts': [{'mimeType': 'multipart/alternative', 'body': {}, 'parts': parts}],
        }
    else:
        payload = {'mimeType': 'multipart/alternative', 'headers': headers, 'parts': parts}
    return {'internalDate': str(internal_date_ms), 'payload': payload}


def make_eml_message(subject='S', sender='a@b.com', message_id='<mid@x>',
                     html='<p>hi</p>', text='hi', date='Thu, 02 Jul 2026 10:02:25 +0000'):
    """Build an email.message.Message (multipart/alternative) like IMAP returns."""
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['Message-ID'] = message_id
    msg['Date'] = date
    if text is not None:
        msg.set_content(text)
    if html is not None:
        if text is not None:
            msg.add_alternative(html, subtype='html')
        else:
            msg.set_content(html, subtype='html')
    return msg


def ground_truth_files():
    return sorted(glob.glob(os.path.join(GROUND_TRUTH_DIR, '*.eml')))


# Expected header values for the two ground-truth newsletters (from their headers).
GROUND_TRUTH_EXPECTED = {
    '20260702-AI just got reallllll expensive.eml': {
        'subject': 'AI just got reallllll expensive',
        'sender': 'Joseph from 404 Media <404-media@ghost.io>',
        'message_id': '<20260702100225.346ecf1e42f52bff@m.ghost.io>',
        'date_prefix': '2026-07-02',
        'phrases': ['throttling', 'driver'],
    },
    '20260917-Unsupervised Learning NO. 543.eml': {
        'subject': 'Unsupervised Learning NO. 543',
        'sender': 'Daniel Miessler <unsupervised-learning@mail.beehiiv.com>',
        'message_id': '<2h4zj_89T4qkdanXnFQikQ@geopod-ismtpd-9>',
        'date_prefix': '2026-09-17',
        'phrases': ['anthropic', 'flock', 'miessler'],
    },
}
