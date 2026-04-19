import sqlite3
import os

"""
Email data spec:
- Subject (parsed out ... somehow)
- Body (sanitized content of email parts stuck together)
- Timestamp
- Sender?
- Label
"""

CREATE_EMAILS_TABLE = """
CREATE TABLE IF NOT EXISTS emails (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    subject   TEXT,
    body      TEXT,
    timestamp INTEGER,
    sender    TEXT,
    label     TEXT NOT NULL
);
"""


class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def init_db(self):
        self.conn.execute(CREATE_EMAILS_TABLE)
        self.conn.commit()

    def insert_email(self, subject: str, body: str, timestamp: int, sender: str, label: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO emails (subject, body, timestamp, sender, label) VALUES (?, ?, ?, ?, ?)",
            (subject, body, timestamp, sender, label),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_emails_by_label(self, label: str) -> list[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM emails WHERE label = ?", (label,))
        return cur.fetchall()

    def close(self):
        self.conn.close()


if __name__=="__main__":
    import pickle

    DB_PATH = os.path.join(os.path.dirname(__file__), 'emails.db')

    # 0. Initialize DB
    db = DatabaseManager(DB_PATH)
    db.init_db()
    print(f"DB initialized at {DB_PATH}")

    # 1. Load a sample raw email and insert it
    EXAMPLE_MSG_PATH = os.path.join('output', 'Risky-Biz_1776561469.pkl')
    with open(EXAMPLE_MSG_PATH, 'rb') as f:
        raw_message = pickle.load(f)

    headers = {h['name']: h['value'] for h in raw_message['payload']['headers']}
    row_id = db.insert_email(
        subject=headers.get('Subject', ''),
        body='',  # populate after sanitization
        timestamp=int(raw_message.get('internalDate', 0)) // 1000,
        sender=headers.get('From', ''),
        label='Risky-Biz',
    )
    print(f"Inserted email row id={row_id}")

    # 2. Query by label
    rows = db.get_emails_by_label('Risky-Biz')
    print(f"Found {len(rows)} email(s) for label 'Risky-Biz'")

    db.close()