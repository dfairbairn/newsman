import email as email_lib
import imaplib


def connect(host, user, password):
    conn = imaplib.IMAP4_SSL(host)
    conn.login(user, password)
    return conn


def fetch_labels(conn):
    """Return mailboxes as label-like dicts matching the oauth interface: [{'name': ...}]."""
    status, mailboxes = conn.list()
    labels = []
    for mb in mailboxes:
        # each entry is e.g. b'(\\HasNoChildren) "/" "INBOX"'
        parts = mb.decode().split('"')
        name = parts[-2] if len(parts) >= 2 else mb.decode()
        labels.append({'name': name})
    return labels


def fetch_messages(conn, label, max_results=1, unseen_only=False):
    """Return up to max_results recent messages from label as email.message.Message objects.

    Reads with BODY.PEEK[] so fetching does NOT mark messages as seen. Set
    unseen_only=True to restrict the search to unread messages.
    """
    conn.select(label, readonly=True)
    criteria = 'UNSEEN' if unseen_only else 'ALL'
    status, data = conn.search(None, criteria)
    msg_ids = data[0].split()

    # take the N most recent (IMAP IDs are oldest-first)
    recent_ids = msg_ids[-max_results:]

    messages = []
    for msg_id in recent_ids:
        status, raw = conn.fetch(msg_id, '(BODY.PEEK[])')
        msg = email_lib.message_from_bytes(raw[0][1])
        messages.append(msg)
    return messages
