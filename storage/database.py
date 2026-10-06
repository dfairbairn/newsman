import sqlite3
import os

from storage.models import Email

"""
Email data spec:
- message_id (RFC822 Message-ID header; stable dedup key, may be empty)
- Subject (parsed out ... somehow)
- Body (sanitized content of email parts stuck together)
- Timestamp
- Sender?
- Label
"""

CREATE_EMAILS_TABLE = """
CREATE TABLE IF NOT EXISTS emails (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      TEXT,
    subject         TEXT,
    body            TEXT,
    timestamp       INTEGER,
    sender          TEXT,
    label           TEXT NOT NULL,
    injection_flags TEXT DEFAULT ''
);
"""

# Dedup on (message_id, label): the same message can legitimately appear under
# multiple labels (stored once per label). Restricted to non-empty message_id so
# that messages without a Message-ID header don't all collide onto one row.
CREATE_MESSAGE_ID_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_emails_message_id_label
    ON emails(message_id, label) WHERE message_id <> '';
"""


class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def init_db(self):
        self.conn.execute(CREATE_EMAILS_TABLE)
        self._migrate()
        # Drop the old single-column index (superseded by the (message_id, label) one).
        self.conn.execute("DROP INDEX IF EXISTS idx_emails_message_id")
        self.conn.execute(CREATE_MESSAGE_ID_INDEX)
        self.conn.commit()

    def _migrate(self):
        """Bring a pre-existing emails table up to the current schema."""
        cols = {row['name'] for row in self.conn.execute("PRAGMA table_info(emails)")}
        if 'message_id' not in cols:
            self.conn.execute("ALTER TABLE emails ADD COLUMN message_id TEXT DEFAULT ''")
        if 'injection_flags' not in cols:
            self.conn.execute("ALTER TABLE emails ADD COLUMN injection_flags TEXT DEFAULT ''")

    def insert_email(self, subject: str, body: str, timestamp: int, sender: str,
                     label: str, message_id: str = '', injection_flags: str = '') -> int | None:
        """Insert one email. Returns the new row id, or None if it was a duplicate
        (same non-empty (message_id, label) already stored)."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO emails "
            "(message_id, subject, body, timestamp, sender, label, injection_flags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message_id, subject, body, timestamp, sender, label, injection_flags),
        )
        self.conn.commit()
        return cur.lastrowid if cur.rowcount else None

    def insert_email_obj(self, email: Email) -> int | None:
        """Insert an Email dataclass. Returns new row id, or None if duplicate."""
        return self.insert_email(
            subject=email.subject,
            body=email.body,
            timestamp=email.timestamp,
            sender=email.sender,
            label=email.label,
            message_id=email.message_id,
            injection_flags=email.injection_flags,
        )

    def get_emails_by_label(self, label: str) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM emails WHERE label = ? ORDER BY timestamp DESC", (label,)
        )
        return cur.fetchall()

    def count_emails(self) -> int:
        return self.conn.execute("SELECT count(*) FROM emails").fetchone()[0]

    def close(self):
        self.conn.close()


DB_PATH = os.path.join(os.path.dirname(__file__), 'emails.db')


def ensure_db(db_path: str = DB_PATH) -> DatabaseManager:
    """Open (or create) the DB and ensure the emails table/index exist."""
    db = DatabaseManager(db_path)
    db.init_db()
    return db


if __name__ == "__main__":
    db = ensure_db()
    print(f"DB ready at {DB_PATH} ({db.count_emails()} emails)")
    db.close()
