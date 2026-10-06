"""LLM-assisted natural-language querying of the story archive.

Claude drives read-only, parameterized tools over summaries.db (via the SDK Tool
Runner), then synthesizes an answer that cites each story and links back to the
original ingested email (Gmail rfc822msgid deep link).
"""
import datetime
import json
import logging
import urllib.parse

import anthropic
from anthropic import beta_tool

from summaries.db import ensure_summary_db
from summaries.extract import llm_api_key
from query_account import load_config

logger = logging.getLogger('newsletter.summarize')

DEFAULT_MODEL = 'claude-opus-4-8'

# Set by run_query() before the tool runner starts; the @beta_tool functions
# below read it. (Single-process CLI use — not for concurrent callers.)
_db = None

QUERY_SYSTEM = (
    "You answer questions about a personal newsletter archive by querying a story database.\n"
    "Today's date is {today}.\n"
    "Always use the tools to retrieve data before answering — never answer world-knowledge "
    "from memory; rely only on what the tools return. Use count_stories for 'how many' "
    "questions. Call list_labels when you need to map a topic to a newsletter series "
    "(e.g. 'Chinese social media' -> a 'Chinese-Doom-Scroll' label). For date ranges like "
    "'the last year', compute YYYY-MM-DD bounds from today's date.\n"
    "Answer concisely. For every story you cite, give its title, the newsletter and date, any "
    "URLs, and the source_email_link so the user can open the original email. If a story has "
    "injection_flags set, flag that its content may have been manipulated. If nothing relevant "
    "is in the archive, say so plainly."
)


def _epoch(d: str):
    if not d:
        return None
    try:
        return int(datetime.datetime.strptime(d, '%Y-%m-%d').timestamp())
    except ValueError:
        return None


def _fmt(r: dict) -> dict:
    ts = r.get('published_ts') or 0
    date = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d') if ts else ''
    mid = r.get('message_id', '')
    link = (f"https://mail.google.com/mail/#search/rfc822msgid:{urllib.parse.quote(mid)}"
            if mid else '')
    return {
        "title": r.get('title'),
        "summary": r.get('summary'),
        "newsletter": r.get('label'),
        "date": date,
        "category": r.get('category'),
        "urls": r.get('urls', []),
        "email_subject": r.get('email_subject'),
        "source_email_link": link,
        "injection_flags": r.get('injection_flags', ''),
    }


@beta_tool
def search_stories(text: str = "", label: str = "", category: str = "", keyword: str = "",
                   since: str = "", until: str = "", limit: int = 25) -> str:
    """Search the newsletter story archive. Returns matching stories as a JSON list.

    Args:
        text: Full-text query over title/summary/keywords. Empty string to skip.
        label: Newsletter-series substring filter (e.g. 'Chinese-Doom-Scroll'). Empty to skip.
        category: One of cyber-incident, geopolitics, policy, research, product, trend,
            business, other. Empty to skip.
        keyword: A single entity/keyword substring filter. Empty to skip.
        since: Only stories on/after this date (YYYY-MM-DD). Empty to skip.
        until: Only stories on/before this date (YYYY-MM-DD). Empty to skip.
        limit: Maximum number of stories to return (default 25).
    """
    rows = _db.search_stories(
        text=text or None, label=label or None, category=category or None,
        keyword=keyword or None, since=_epoch(since), until=_epoch(until), limit=limit)
    return json.dumps([_fmt(r) for r in rows], ensure_ascii=False)


@beta_tool
def count_stories(text: str = "", label: str = "", category: str = "", keyword: str = "",
                  since: str = "", until: str = "") -> str:
    """Count stories matching the filters (use for 'how many' questions). Returns JSON {"count": N}.

    Args:
        text: Full-text query over title/summary/keywords. Empty string to skip.
        label: Newsletter-series substring filter. Empty to skip.
        category: Category filter. Empty to skip.
        keyword: Single entity/keyword substring filter. Empty to skip.
        since: Only stories on/after this date (YYYY-MM-DD). Empty to skip.
        until: Only stories on/before this date (YYYY-MM-DD). Empty to skip.
    """
    n = _db.count_stories(
        text=text or None, label=label or None, category=category or None,
        keyword=keyword or None, since=_epoch(since), until=_epoch(until))
    return json.dumps({"count": n})


@beta_tool
def list_labels() -> str:
    """List the available newsletter series (labels) and how many stories each has."""
    return json.dumps(_db.list_labels(), ensure_ascii=False)


def run_query(question: str, config=None) -> str:
    """Answer a natural-language question over the story archive. Returns the answer text."""
    global _db
    cfg = config if config is not None else load_config()
    llm = cfg.get('llm', {})
    key = llm_api_key(llm)
    if not key or key.startswith('sk-ant-...'):
        raise SystemExit("Set [llm].api_key in newsletter.toml to use the summarizer.")
    model = llm.get('query_model', DEFAULT_MODEL)
    client = anthropic.Anthropic(api_key=key)

    _db = ensure_summary_db()
    try:
        system = QUERY_SYSTEM.format(today=datetime.date.today().isoformat())
        runner = client.beta.messages.tool_runner(
            model=model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=system,
            tools=[search_stories, count_stories, list_labels],
            messages=[{"role": "user", "content": question}],
        )
        final = None
        for message in runner:
            final = message
        if final is None:
            return ""
        return "".join(b.text for b in final.content if getattr(b, "type", None) == "text")
    finally:
        _db.close()
        _db = None
