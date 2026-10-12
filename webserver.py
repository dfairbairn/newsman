"""Local web UI over the newsletter data.

Presents stories (summaries.db) and raw emails (emails.db), supports tag/keyword/
full-text/date filtering, and an optional LLM-assisted natural-language query.

Run:   python webserver.py          # http://127.0.0.1:5000  (--help for options;
       python webserver.py -a       #  -a/--all-interfaces binds 0.0.0.0)
Tests inject temp DB paths via create_app(...) and monkeypatch the NL query.
"""
import argparse
import datetime
import ipaddress
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


def _epoch_end(s):
    """Last second of the given calendar day (inclusive 'until' semantics; DST-safe)."""
    if not s:
        return None
    try:
        d = datetime.datetime.strptime(s, '%Y-%m-%d')
    except ValueError:
        return None
    return int((d + datetime.timedelta(days=1)).timestamp()) - 1


def _clamp_limit(raw, default, maximum):
    """Parse a ?limit= value; garbage -> default, otherwise clamp to 1..maximum."""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, maximum))


def _safe_urls(urls) -> list:
    """Keep only http(s) URLs -- LLM-extracted strings must never become javascript:/data: links."""
    out = []
    for u in urls or []:
        if isinstance(u, str) and u.strip().lower().startswith(('http://', 'https://')):
            out.append(u)
    return out


def _is_loopback(host: str) -> bool:
    """True only for hosts that bind to this machine alone (127.0.0.0/8, ::1, localhost)."""
    if (host or '').lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False        # wildcard shorthand ('0'), hostnames, etc.: assume reachable


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
        'urls': _safe_urls(r.get('urls', [])),
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
                until=_epoch_end(request.args.get('until')),
                limit=_clamp_limit(request.args.get('limit'), 50, 500),
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
                limit=_clamp_limit(request.args.get('limit'), 100, 1000),
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


def build_parser():
    p = argparse.ArgumentParser(
        description="Local web UI over the newsletter archive: browse/search stories "
                    "(summaries.db), browse raw emails (emails.db), and ask natural-language "
                    "questions (Ask tab; uses the Anthropic API).",
        epilog="examples:\n"
               "  python webserver.py                     # http://127.0.0.1:5000 (this machine only)\n"
               "  python webserver.py --port 8080\n"
               "  python webserver.py --all-interfaces    # listen on 0.0.0.0 (reachable from the LAN)\n"
               "  python webserver.py --host 192.168.1.20 # bind one specific interface\n\n"
               "The UI has no authentication and exposes email bodies plus a paid LLM query\n"
               "endpoint -- only bind beyond localhost on a network you trust.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    host = p.add_mutually_exclusive_group()
    host.add_argument('--host', default='127.0.0.1',
                      help="interface address to bind (default: 127.0.0.1, local only)")
    host.add_argument('-a', '--all-interfaces', action='store_const', dest='host', const='0.0.0.0',
                      help="bind every interface (same as --host 0.0.0.0)")
    p.add_argument('-p', '--port', type=int, default=None,
                   help="port to listen on (default: $PORT or 5000)")
    p.add_argument('--summaries-db', default=SUMMARIES_DB_PATH,
                   help=f"stories database (default: {os.path.relpath(SUMMARIES_DB_PATH)})")
    p.add_argument('--emails-db', default=EMAILS_DB_PATH,
                   help=f"emails database (default: {os.path.relpath(EMAILS_DB_PATH)})")
    p.add_argument('--debug', action='store_true',
                   help="Flask debug mode (auto-reload + interactive debugger; never with --all-interfaces)")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.port is None:                      # resolve $PORT lazily so --help/--port never choke on it
        env = os.environ.get('PORT', '5000')
        try:
            args.port = int(env)
        except ValueError:
            parser.error(f"invalid $PORT value {env!r} (expected an integer; or pass --port)")
    exposed = not _is_loopback(args.host)
    if exposed and args.debug:
        raise SystemExit("Refusing --debug on a non-loopback host (the debugger allows code execution).")
    cfg = load_config()
    setup_logging(cfg)
    app = create_app(summaries_db=args.summaries_db, emails_db=args.emails_db, config=cfg)
    if exposed:
        logger.warning("Listening beyond localhost (%s) -- no authentication; anyone who can reach "
                       "port %d can read emails and spend API credit via Ask.", args.host, args.port)
        logger.info("Newsman web UI → http://%s:%d", '127.0.0.1' if args.host in ('0.0.0.0', '::', '0', '::0') else args.host, args.port)
    else:
        logger.info("Newsman web UI → http://%s:%d", args.host, args.port)
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()
