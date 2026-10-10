"""Separate SQLite store for extracted newsletter stories (storage/summaries.db).

Kept deliberately apart from emails.db — this is the queryable knowledge base.
Stories are immutable once extracted, so the FTS5 index is populated inline at
insert (no triggers). Mirrors the patterns in storage/database.py.
"""
import json
import os
import sqlite3
import time

from summaries.models import Story

CREATE_STORIES = """
CREATE TABLE IF NOT EXISTS stories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id      TEXT NOT NULL,
    label           TEXT NOT NULL,
    sender          TEXT,
    email_subject   TEXT,
    published_ts    INTEGER,
    title           TEXT,
    summary         TEXT,
    category        TEXT,
    urls            TEXT,            -- JSON array
    injection_flags TEXT DEFAULT '',
    model           TEXT,
    extracted_ts    INTEGER
);
"""

CREATE_KEYWORDS = """
CREATE TABLE IF NOT EXISTS story_keywords (
    story_id INTEGER NOT NULL,
    keyword  TEXT NOT NULL
);
"""

CREATE_LEDGER = """
CREATE TABLE IF NOT EXISTS summarized_emails (
    message_id   TEXT NOT NULL,
    label        TEXT NOT NULL,
    n_stories    INTEGER,
    status       TEXT,
    extracted_ts INTEGER,
    PRIMARY KEY (message_id, label)
);
"""

CREATE_QUARANTINE = """
CREATE TABLE IF NOT EXISTS quarantine (
    message_id      TEXT NOT NULL,
    label           TEXT NOT NULL,
    subject         TEXT,
    sender          TEXT,
    published_ts    INTEGER,
    injection_flags TEXT,
    quarantined_ts  INTEGER,
    PRIMARY KEY (message_id, label)
);
"""

CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS stories_fts
    USING fts5(title, summary, keywords, story_id UNINDEXED);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_story_keywords ON story_keywords(keyword)",
    "CREATE INDEX IF NOT EXISTS idx_stories_label ON stories(label)",
    "CREATE INDEX IF NOT EXISTS idx_stories_ts ON stories(published_ts)",
    "CREATE INDEX IF NOT EXISTS idx_stories_message_id ON stories(message_id)",
]


class SummaryDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def init_db(self):
        self.conn.execute(CREATE_STORIES)
        self.conn.execute(CREATE_KEYWORDS)
        self.conn.execute(CREATE_LEDGER)
        self.conn.execute(CREATE_QUARANTINE)
        self.conn.execute(CREATE_FTS)
        for stmt in INDEXES:
            self.conn.execute(stmt)
        self.conn.commit()

    # ---- writes -------------------------------------------------------------
    def add_story(self, story: Story, *, message_id: str, label: str, sender: str = '',
                  email_subject: str = '', published_ts: int = 0,
                  injection_flags: str = '', model: str = '') -> int:
        """Insert one story plus its keywords and FTS row. Returns the story id."""
        cur = self.conn.execute(
            "INSERT INTO stories (message_id, label, sender, email_subject, published_ts, "
            "title, summary, category, urls, injection_flags, model, extracted_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (message_id, label, sender, email_subject, published_ts, story.title,
             story.summary, story.category, json.dumps(story.urls), injection_flags,
             model, int(time.time())),
        )
        story_id = cur.lastrowid
        for kw in story.keywords:
            self.conn.execute(
                "INSERT INTO story_keywords (story_id, keyword) VALUES (?, ?)",
                (story_id, kw),
            )
        self.conn.execute(
            "INSERT INTO stories_fts (title, summary, keywords, story_id) VALUES (?, ?, ?, ?)",
            (story.title, story.summary, ' '.join(story.keywords), story_id),
        )
        self.conn.commit()
        return story_id

    def record_email(self, message_id: str, label: str, n_stories: int, status: str = 'ok'):
        """Mark an (email, label) as processed in the idempotency ledger."""
        self.conn.execute(
            "INSERT OR REPLACE INTO summarized_emails "
            "(message_id, label, n_stories, status, extracted_ts) VALUES (?, ?, ?, ?, ?)",
            (message_id, label, n_stories, status, int(time.time())),
        )
        self.conn.commit()

    def quarantine_email(self, message_id: str, label: str, subject: str = '', sender: str = '',
                         published_ts: int = 0, injection_flags: str = ''):
        """Divert a flagged email to the quarantine bin AND mark it processed in the
        ledger so future extraction runs skip it (it is never summarized)."""
        self.conn.execute(
            "INSERT OR REPLACE INTO quarantine "
            "(message_id, label, subject, sender, published_ts, injection_flags, quarantined_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message_id, label, subject, sender, published_ts, injection_flags, int(time.time())),
        )
        self.record_email(message_id, label, 0, status='quarantined')

    def list_quarantine(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM quarantine ORDER BY quarantined_ts DESC")]

    def quarantine_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]

    # ---- idempotency --------------------------------------------------------
    def is_summarized(self, message_id: str, label: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM summarized_emails WHERE message_id = ? AND label = ?",
            (message_id, label),
        ).fetchone()
        return row is not None

    def summarized_keys(self) -> set:
        """Set of (message_id, label) pairs already processed."""
        return {(r[0], r[1]) for r in
                self.conn.execute("SELECT message_id, label FROM summarized_emails")}

    # ---- reads (used by the query tools) ------------------------------------
    def _where(self, text=None, label=None, category=None, keyword=None, since=None, until=None):
        clauses, params = [], []
        if text:
            # Quote each token so FTS5 treats operator-like characters as literals
            # (implicit AND between tokens); robust to arbitrary model/user input.
            tokens = [t for t in text.replace('"', ' ').split() if t]
            fts = ' '.join(f'"{t}"' for t in tokens)
            if fts:
                clauses.append(
                    "s.id IN (SELECT story_id FROM stories_fts WHERE stories_fts MATCH ?)")
                params.append(fts)
        if label:
            clauses.append("s.label LIKE ?")
            params.append(f"%{label}%")
        if category:
            clauses.append("s.category = ?")
            params.append(category)
        if keyword:
            clauses.append("s.id IN (SELECT story_id FROM story_keywords WHERE keyword LIKE ?)")
            params.append(f"%{keyword}%")
        if since is not None:
            clauses.append("s.published_ts >= ?")
            params.append(int(since))
        if until is not None:
            clauses.append("s.published_ts <= ?")
            params.append(int(until))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def search_stories(self, text=None, label=None, category=None, keyword=None,
                       since=None, until=None, limit=50) -> list[dict]:
        where, params = self._where(text, label, category, keyword, since, until)
        rows = self.conn.execute(
            f"SELECT s.* FROM stories s{where} ORDER BY s.published_ts DESC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count_stories(self, text=None, label=None, category=None, keyword=None,
                      since=None, until=None) -> int:
        where, params = self._where(text, label, category, keyword, since, until)
        return self.conn.execute(
            f"SELECT COUNT(*) FROM stories s{where}", params
        ).fetchone()[0]

    def list_labels(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT label, COUNT(*) AS n FROM stories GROUP BY label ORDER BY n DESC"
        ).fetchall()
        return [{"label": r["label"], "stories": r["n"]} for r in rows]

    def total_stories(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]

    @staticmethod
    def _row_to_dict(r: sqlite3.Row) -> dict:
        d = dict(r)
        try:
            d["urls"] = json.loads(d.get("urls") or "[]")
        except (TypeError, ValueError):
            d["urls"] = []
        return d

    def close(self):
        self.conn.close()


DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'storage', 'summaries.db')


def ensure_summary_db(db_path: str = DB_PATH) -> SummaryDB:
    """Open (or create) the summaries DB and ensure its schema exists."""
    db = SummaryDB(db_path)
    db.init_db()
    return db


if __name__ == "__main__":
    db = ensure_summary_db()
    print(f"summaries DB ready at {DB_PATH} ({db.total_stories()} stories)")
    db.close()
