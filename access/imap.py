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


def _quote_mailbox(label):
    """Quote a mailbox name for SELECT (handles spaces, e.g. '[Gmail]/All Mail')."""
    return '"%s"' % label.replace('"', '\\"')


def fetch_messages(conn, label, max_results=None, unseen_only=False, since=None):
    """Return messages from label as email.message.Message objects.

    - Reads with BODY.PEEK[] and selects readonly, so fetching does NOT mark
      messages seen.
    - unseen_only=True restricts to unread messages (IMAP UNSEEN).
    - since: a datetime/date; restricts to messages on/after that day (IMAP SINCE).
    - max_results: cap on the number of (most recent) messages; None = no cap.
    """
    status, _ = conn.select(_quote_mailbox(label), readonly=True)
    if status != 'OK':
        raise RuntimeError(f"Could not select mailbox '{label}'")

    criteria = []
    if unseen_only:
        criteria.append('UNSEEN')
    if since is not None:
        criteria.append('SINCE')
        criteria.append(since.strftime('%d-%b-%Y'))
    if not criteria:
        criteria = ['ALL']

    status, data = conn.search(None, *criteria)
    msg_ids = data[0].split()

    # IMAP ids are oldest-first; keep the N most recent when capped.
    if max_results is not None:
        msg_ids = msg_ids[-max_results:]

    messages = []
    for msg_id in msg_ids:
        status, raw = conn.fetch(msg_id, '(BODY.PEEK[])')
        msg = email_lib.message_from_bytes(raw[0][1])
        messages.append(msg)
    return messages
