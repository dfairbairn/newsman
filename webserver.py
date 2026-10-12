"""Local web UI over the newsletter data.

Presents stories (summaries.db) and raw emails (emails.db), supports tag/keyword/
full-text/date filtering, and an optional LLM-assisted natural-language query.

Run:   python webserver.py          # http://127.0.0.1:5000
Tests inject temp DB paths via create_app(...) and monkeypatch the NL query.
"""
import datetime
import logging
import os
import urllib.parse

from flask import Flask, abort, jsonify, render_template, request

from storage.database import DatabaseManager, DB_PATH as EMAILS_DB_PATH
from summaries.db import SummaryDB, DB_PATH as SUMMARIES_DB_PATH
from summaries.models import Category
from typing import get_args

from query_account import load_config, setup_logging

logger = logging.getLogger('newsletter.web')

CATEGORIES = list(get_args(Category))


def _run_nl_query(question: str, config):
    """Thin wrapper around the LLM query so tests can monkeypatch it without the SDK."""
    from summaries.query import run_query
    return run_query(question, config=config)


def _epoch(s):
    if not s:
        return None
    try:
        return int(datetime.datetime.strptime(s, '%Y-%m-%d').timestamp())
    except ValueError:
        return None


def _story_out(r: dict) -> dict:
    ts = r.get('published_ts') or 0
    mid = r.get('message_id', '') or ''
    link = (f"https://mail.google.com/mail/#search/rfc822msgid:{urllib.parse.quote(mid)}"
            if mid else '')
    return {
        'id': r.get('id'),
        'title': r.get('title'),
        'summary': r.get('summary'),
        'category': r.get('category'),
        'newsletter': r.get('label'),
        'date': datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d') if ts else '',
        'urls': r.get('urls', []),
        'message_id': mid,
        'injection_flags': r.get('injection_flags', ''),
        'source_email_link': link,
    }


def create_app(summaries_db=None, emails_db=None, config=None) -> Flask:
    app = Flask(__name__)
    app.config['SUMMARIES_DB'] = summaries_db or SUMMARIES_DB_PATH
    app.config['EMAILS_DB'] = emails_db or EMAILS_DB_PATH
    app.config['NL_CONFIG'] = config

    def sdb() -> SummaryDB:
        db = SummaryDB(app.config['SUMMARIES_DB'])
        db.init_db()
        return db

    def edb() -> DatabaseManager:
        db = DatabaseManager(app.config['EMAILS_DB'])
        db.init_db()
        return db

    @app.route('/')
    def index():
        return render_template('index.html', categories=CATEGORIES)

    @app.route('/api/stats')
    def stats():
        s, e = sdb(), edb()
        try:
            return jsonify({
                'stories': s.total_stories(),
                'emails': e.count_emails(),
                'quarantined': s.quarantine_count(),
                'story_labels': s.list_labels(),
            })
        finally:
            s.close()
            e.close()

    @app.route('/api/labels')
    def labels():
        s = sdb()
        try:
            return jsonify(s.list_labels())
        finally:
            s.close()

    @app.route('/api/stories')
    def stories():
        s = sdb()
        try:
            rows = s.search_stories(
                text=request.args.get('text') or None,
                label=request.args.get('label') or None,
                category=request.args.get('category') or None,
                keyword=request.args.get('keyword') or None,
                since=_epoch(request.args.get('since')),
                until=_epoch(request.args.get('until')),
                limit=int(request.args.get('limit', 50)),
            )
            return jsonify([_story_out(r) for r in rows])
        finally:
            s.close()

    @app.route('/api/query', methods=['POST'])
    def query():
        data = request.get_json(silent=True) or {}
        q = (data.get('question') or '').strip()
        if not q:
            return jsonify({'error': 'empty question'}), 400
        cfg = app.config['NL_CONFIG'] or load_config()
        try:
            answer = _run_nl_query(q, cfg)
        except SystemExit as ex:                       # e.g. missing API key
            return jsonify({'error': str(ex)}), 400
        except Exception as ex:                         # noqa: BLE001 - surface to UI
            logger.exception('NL query failed')
            return jsonify({'error': f'{type(ex).__name__}: {ex}'}), 500
        return jsonify({'question': q, 'answer': answer})

    @app.route('/api/emails')
    def emails():
        e = edb()
        try:
            rows = e.search_emails(
                label=request.args.get('label') or None,
                flagged_only=request.args.get('flagged') == '1',
                limit=int(request.args.get('limit', 100)),
            )
            return jsonify([dict(r) for r in rows])
        finally:
            e.close()

    @app.route('/api/emails/<int:email_id>')
    def email_detail(email_id):
        e = edb()
        try:
            row = e.get_email(email_id)
            if not row:
                abort(404)
            return jsonify(dict(row))
        finally:
            e.close()

    @app.route('/api/email_labels')
    def email_labels():
        e = edb()
        try:
            return jsonify([dict(r) for r in e.email_labels()])
        finally:
            e.close()

    return app


if __name__ == '__main__':
    cfg = load_config()
    setup_logging(cfg)
    app = create_app(config=cfg)
    port = int(os.environ.get('PORT', '5000'))
    logger.info("Newsman web UI → http://127.0.0.1:%d", port)
    app.run(host='127.0.0.1', port=port, debug=False)
