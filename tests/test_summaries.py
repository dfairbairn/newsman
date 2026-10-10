"""Tests for the summarization engine (no network / no API calls)."""
import unittest
from types import SimpleNamespace
from typing import get_args

from storage.database import DatabaseManager
from summaries import extract as ex
from summaries import query as q
from summaries.db import SummaryDB
from summaries.models import Category, NewsletterExtraction, Story


def _story(**kw):
    base = dict(title="T", summary="S", category="other", keywords=[], urls=[])
    base.update(kw)
    return Story(**base)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class TestModels(unittest.TestCase):
    def test_story_defaults(self):
        s = Story(title="t", summary="s", category="trend")
        self.assertEqual(s.keywords, [])
        self.assertEqual(s.urls, [])

    def test_extraction_from_dict(self):
        ext = NewsletterExtraction.model_validate(
            {"stories": [{"title": "t", "summary": "s", "category": "other",
                          "keywords": ["a"], "urls": ["http://x"]}]})
        self.assertEqual(len(ext.stories), 1)
        self.assertEqual(ext.stories[0].urls, ["http://x"])

    def test_invalid_category_rejected(self):
        with self.assertRaises(Exception):
            Story(title="t", summary="s", category="not-a-category")


# --------------------------------------------------------------------------- #
# SummaryDB
# --------------------------------------------------------------------------- #
class TestSummaryDB(unittest.TestCase):
    def setUp(self):
        self.db = SummaryDB(':memory:')
        self.db.init_db()

    def tearDown(self):
        self.db.close()

    def _add(self, **kw):
        prov = dict(message_id='<m@x>', label='Risky-Biz', sender='r@x',
                    email_subject='Sub', published_ts=1000, model='m')
        story_kw = {k: kw.pop(k) for k in list(kw) if k in
                    ('title', 'summary', 'category', 'keywords', 'urls')}
        prov.update(kw)
        return self.db.add_story(_story(**story_kw), **prov)

    def test_insert_and_total(self):
        self._add()
        self.assertEqual(self.db.total_stories(), 1)

    def test_fts_match(self):
        self._add(title="KillSec dismantled", summary="ransomware arrests in Europe")
        self.assertEqual(len(self.db.search_stories(text="ransomware")), 1)
        self.assertEqual(len(self.db.search_stories(text="nonexistentword")), 0)

    def test_fts_handles_special_chars(self):
        self._add(summary="an update about foo:bar (baz) AND qux")
        # must not raise despite FTS operator-like characters
        self.assertIsInstance(self.db.search_stories(text="foo:bar (baz)"), list)

    def test_keyword_filter(self):
        self._add(keywords=["Europol", "KillSec"])
        self.assertEqual(len(self.db.search_stories(keyword="Europol")), 1)
        self.assertEqual(len(self.db.search_stories(keyword="Nope")), 0)

    def test_label_filter_substring(self):
        self._add(label="China Watching/Chinese-Doom-Scroll")
        self.assertEqual(self.db.count_stories(label="Doom-Scroll"), 1)

    def test_category_filter(self):
        self._add(category="cyber-incident")
        self.assertEqual(self.db.count_stories(category="cyber-incident"), 1)
        self.assertEqual(self.db.count_stories(category="trend"), 0)

    def test_date_range(self):
        self._add(published_ts=1000)
        self._add(published_ts=5000)
        self.assertEqual(self.db.count_stories(since=2000), 1)
        self.assertEqual(self.db.count_stories(until=2000), 1)

    def test_urls_json_roundtrip(self):
        self._add(urls=["https://a", "https://b"])
        self.assertEqual(self.db.search_stories()[0]["urls"], ["https://a", "https://b"])

    def test_list_labels(self):
        self._add(label="A")
        self._add(label="A")
        self._add(label="B")
        labels = {r["label"]: r["stories"] for r in self.db.list_labels()}
        self.assertEqual(labels, {"A": 2, "B": 1})

    def test_limit(self):
        for i in range(5):
            self._add(published_ts=1000 + i)
        self.assertEqual(len(self.db.search_stories(limit=3)), 3)

    def test_search_orders_desc(self):
        self._add(title="old", published_ts=100)
        self._add(title="new", published_ts=200)
        self.assertEqual(self.db.search_stories()[0]["title"], "new")


# --------------------------------------------------------------------------- #
# Idempotency ledger (composite key)
# --------------------------------------------------------------------------- #
class TestLedger(unittest.TestCase):
    def setUp(self):
        self.db = SummaryDB(':memory:')
        self.db.init_db()

    def tearDown(self):
        self.db.close()

    def test_record_and_check(self):
        self.db.record_email('<m@x>', 'L', 3)
        self.assertTrue(self.db.is_summarized('<m@x>', 'L'))
        self.assertFalse(self.db.is_summarized('<m@x>', 'OTHER'))

    def test_composite_key_distinct_labels(self):
        self.db.record_email('<m@x>', 'A', 1)
        self.db.record_email('<m@x>', 'B', 1)
        self.assertEqual(self.db.summarized_keys(), {('<m@x>', 'A'), ('<m@x>', 'B')})


# --------------------------------------------------------------------------- #
# Extraction plumbing (no API)
# --------------------------------------------------------------------------- #
class TestExtractPlumbing(unittest.TestCase):
    def test_record_schema_enum_matches_categories(self):
        item = ex.RECORD_SCHEMA["properties"]["stories"]["items"]
        self.assertEqual(item["properties"]["category"]["enum"], list(get_args(Category)))
        self.assertFalse(item["additionalProperties"])
        self.assertIn("title", item["required"])

    def test_parse_extraction_from_json_text(self):
        import json as _json
        blk = SimpleNamespace(type="text", text=_json.dumps(
            {"stories": [{"title": "T", "summary": "S", "category": "other",
                          "keywords": ["k"], "urls": []}]}))
        ext = ex._parse_extraction([blk])
        self.assertEqual(len(ext.stories), 1)

    def test_parse_extraction_empty_when_absent(self):
        blk = SimpleNamespace(type="tool_use", name="x", input={})
        self.assertEqual(ex._parse_extraction([blk]).stories, [])

    def test_candidate_emails_skips_summarized_and_empty(self):
        edb = DatabaseManager(':memory:')
        edb.init_db()
        edb.insert_email("s1", "real body", 100, "a", "L", message_id="<m1@x>")
        edb.insert_email("s2", "", 90, "a", "L", message_id="<m2@x>")      # empty body
        edb.insert_email("s3", "body3", 80, "a", "L", message_id="<m3@x>")
        cands = ex._candidate_emails(edb, summarized={('<m3@x>', 'L')})
        ids = {r["message_id"] for r in cands}
        self.assertEqual(ids, {"<m1@x>"})  # m2 empty, m3 already summarized
        edb.close()

    def test_candidate_emails_label_and_limit(self):
        edb = DatabaseManager(':memory:')
        edb.init_db()
        edb.insert_email("a", "b", 100, "x", "Econ", message_id="<a@x>")
        edb.insert_email("c", "d", 90, "x", "Tech", message_id="<c@x>")
        self.assertEqual(len(ex._candidate_emails(edb, set(), label="Econ")), 1)
        self.assertEqual(len(ex._candidate_emails(edb, set(), limit=1)), 1)
        edb.close()

    def test_run_extract_requires_key(self):
        with self.assertRaises(SystemExit):
            ex.run_extract(config={"llm": {"api_key": "sk-ant-..."}})

    def test_llm_api_key_accepts_alt_name(self):
        self.assertEqual(ex.llm_api_key({"anthropic_api_key": "k"}), "k")
        self.assertEqual(ex.llm_api_key({"api_key": "k2"}), "k2")


# --------------------------------------------------------------------------- #
# Query helpers (no API)
# --------------------------------------------------------------------------- #
class TestQueryHelpers(unittest.TestCase):
    def test_epoch_valid(self):
        self.assertIsInstance(q._epoch("2026-01-01"), int)

    def test_epoch_invalid_and_empty(self):
        self.assertIsNone(q._epoch("not-a-date"))
        self.assertIsNone(q._epoch(""))

    def test_fmt_builds_gmail_link_and_date(self):
        out = q._fmt({"title": "t", "summary": "s", "label": "L", "category": "other",
                      "urls": ["http://a"], "email_subject": "sub",
                      "published_ts": 1790910579, "message_id": "<abc@x>"})
        self.assertIn("rfc822msgid:", out["source_email_link"])
        self.assertIn("%3Cabc%40x%3E", out["source_email_link"])  # url-encoded <abc@x>
        self.assertRegex(out["date"], r"\d{4}-\d{2}-\d{2}")

    def test_fmt_no_message_id(self):
        out = q._fmt({"title": "t", "summary": "s", "label": "L", "published_ts": 0,
                      "message_id": ""})
        self.assertEqual(out["source_email_link"], "")
        self.assertEqual(out["date"], "")


# --------------------------------------------------------------------------- #
# Structure-preserving sanitization (for the story counter)
# --------------------------------------------------------------------------- #
class TestStructuredText(unittest.TestCase):
    def test_preserves_paragraph_breaks(self):
        from storage.sanitizer import html_to_structured_text
        out = html_to_structured_text("<p>First story.</p><p>Second story.</p>")
        self.assertIn("First story.", out)
        self.assertIn("Second story.", out)
        self.assertIn("\n", out)  # not collapsed to one line

    def test_hr_becomes_divider(self):
        from storage.sanitizer import html_to_structured_text
        out = html_to_structured_text("<p>A</p><hr><p>B</p>")
        self.assertIn("---", out)

    def test_sanitize_to_text_is_structured_and_clean(self):
        from storage.sanitizer import sanitize_to_text
        out = sanitize_to_text("<p>Hello <script>x()</script></p><hr><p>world</p>")
        self.assertIn("Hello", out)
        self.assertIn("world", out)
        self.assertNotIn("<", out)
        self.assertIn("\n", out)
        self.assertIn("---", out)  # <hr> survives sanitization as a section divider

    def test_html_to_text_still_single_line(self):
        # flat extractor unchanged (backward compat)
        from storage.sanitizer import html_to_text
        self.assertEqual(html_to_text("<p>a</p><p>b</p>"), "a b")


# --------------------------------------------------------------------------- #
# Quarantine
# --------------------------------------------------------------------------- #
class TestQuarantine(unittest.TestCase):
    def setUp(self):
        self.db = SummaryDB(':memory:')
        self.db.init_db()

    def tearDown(self):
        self.db.close()

    def test_quarantine_records_and_lists(self):
        self.db.quarantine_email('<m@x>', 'L', 'Sub', 'snd', 123, 'instruction_override')
        self.assertEqual(self.db.quarantine_count(), 1)
        row = self.db.list_quarantine()[0]
        self.assertEqual(row['subject'], 'Sub')
        self.assertEqual(row['injection_flags'], 'instruction_override')

    def test_quarantine_marks_ledger_so_future_runs_skip(self):
        self.db.quarantine_email('<m@x>', 'L', 'Sub', 'snd', 123, 'reveal_prompt')
        # recorded in the ledger -> excluded from future candidate selection
        self.assertTrue(self.db.is_summarized('<m@x>', 'L'))
        self.assertIn(('<m@x>', 'L'), self.db.summarized_keys())


class TestQuarantineSplit(unittest.TestCase):
    def setUp(self):
        self.db = SummaryDB(':memory:')
        self.db.init_db()

    def tearDown(self):
        self.db.close()

    def _row(self, mid, flags=''):
        return {'message_id': mid, 'label': 'L', 'subject': 's', 'sender': 'a',
                'timestamp': 1, 'body': 'b', 'injection_flags': flags}

    def test_disabled_passes_all_through(self):
        rows = [self._row('<a@x>', 'reveal_prompt'), self._row('<b@x>')]
        to_proc, n = ex._quarantine_split(self.db, rows, quarantine_sketchy=False)
        self.assertEqual(len(to_proc), 2)
        self.assertEqual(n, 0)
        self.assertEqual(self.db.quarantine_count(), 0)

    def test_enabled_diverts_flagged_only(self):
        rows = [self._row('<a@x>', 'reveal_prompt'), self._row('<b@x>')]
        to_proc, n = ex._quarantine_split(self.db, rows, quarantine_sketchy=True)
        self.assertEqual([r['message_id'] for r in to_proc], ['<b@x>'])
        self.assertEqual(n, 1)
        self.assertTrue(self.db.is_summarized('<a@x>', 'L'))      # quarantined -> skipped later
        self.assertFalse(self.db.is_summarized('<b@x>', 'L'))


# --------------------------------------------------------------------------- #
# Two-pass extraction plumbing (no API)
# --------------------------------------------------------------------------- #
class TestCounterPlumbing(unittest.TestCase):
    def test_count_schema(self):
        sch = ex.COUNT_SCHEMA
        self.assertEqual(set(sch["required"]), {"count", "is_list_of_items"})
        self.assertFalse(sch["additionalProperties"])
        self.assertEqual(sch["properties"]["count"]["type"], "integer")

    def test_extract_hint_mentions_count_and_no_merge_for_lists(self):
        row = {'label': 'L', 'subject': 's', 'sender': 'a', 'body': 'b'}
        hint = ex._extract_user_content(row, 7, True)
        self.assertIn("7", hint)
        self.assertIn("per item", hint.lower())

    def test_params_use_models_and_low_effort(self):
        row = {'label': 'L', 'subject': 's', 'sender': 'a', 'body': 'b'}
        cp = ex._count_params("claude-haiku-5-5", row)
        ep = ex._extract_params("claude-sonnet-5-5", row, 3, False)
        self.assertEqual(cp["model"], "claude-haiku-5-5")
        self.assertEqual(ep["model"], "claude-sonnet-5-5")
        self.assertEqual(ep["output_config"]["effort"], "low")
        # structured outputs (not forced tool_choice, unsupported on 5.5 models)
        self.assertEqual(ep["output_config"]["format"]["schema"], ex.RECORD_SCHEMA)
        self.assertNotIn("tool_choice", ep)
        # thinking omitted (its valid "off" value differs per model)
        self.assertNotIn("thinking", cp)
        self.assertNotIn("thinking", ep)

    def test_default_models_are_cheaper_tier(self):
        self.assertEqual(ex.DEFAULT_EXTRACT_MODEL, "claude-sonnet-5-5")
        self.assertEqual(ex.DEFAULT_COUNT_MODEL, "claude-haiku-5-5")



if __name__ == "__main__":
    unittest.main(verbosity=2)
