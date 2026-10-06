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
    def test_record_tool_schema_enum_matches_categories(self):
        enum = RECORD = ex.RECORD_TOOL["input_schema"]["properties"]["stories"]["items"]
        self.assertEqual(enum["properties"]["category"]["enum"], list(get_args(Category)))
        self.assertFalse(enum["additionalProperties"])
        self.assertIn("title", enum["required"])

    def test_parse_tool_result(self):
        blk = SimpleNamespace(type="tool_use", name="record_stories", input={
            "stories": [{"title": "T", "summary": "S", "category": "other",
                         "keywords": ["k"], "urls": []}]})
        ext = ex._parse_tool_result([blk])
        self.assertEqual(len(ext.stories), 1)

    def test_parse_tool_result_empty_when_absent(self):
        blk = SimpleNamespace(type="text", text="hi")
        self.assertEqual(ex._parse_tool_result([blk]).stories, [])

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
