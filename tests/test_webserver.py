"""Tests for the local web UI (Flask test client; temp DBs; mocked LLM query)."""
import os
import tempfile
import unittest

import webserver
from storage.database import DatabaseManager
from summaries.db import SummaryDB
from summaries.models import Story


class WebBase(unittest.TestCase):
    def setUp(self):
        fd, self.spath = tempfile.mkstemp(suffix='.db'); os.close(fd); os.remove(self.spath)
        fd, self.epath = tempfile.mkstemp(suffix='.db'); os.close(fd); os.remove(self.epath)

        s = SummaryDB(self.spath); s.init_db()
        s.add_story(Story(title="KillSec dismantled",
                          summary="Europol arrested KillSec ransomware operators.",
                          category="cyber-incident", keywords=["KillSec", "Europol"], urls=["https://x/1"]),
                    message_id="<m1@x>", label="Risky-Biz", published_ts=1790000000)
        s.add_story(Story(title="Weibo gift drama", summary="A viral thread about gifts.",
                          category="trend", keywords=["Weibo"], urls=[]),
                    message_id="<m2@x>", label="China Watching/Chinese-Doom-Scroll",
                    published_ts=1789000000)
        s.quarantine_email("<evil@x>", "Suspicious", "bad", "a@b", 1, "instruction_override")
        s.close()

        e = DatabaseManager(self.epath); e.init_db()
        e.insert_email("Risky Bulletin", "full body one", 1790000000, "r@ghost.io",
                       "Risky-Biz", message_id="<m1@x>")
        e.insert_email("Weekly roundup", "flagged body", 1789000000, "a@evil.com",
                       "Suspicious", message_id="<evil@x>", injection_flags="instruction_override")
        e.close()

        self.app = webserver.create_app(summaries_db=self.spath, emails_db=self.epath,
                                        config={'llm': {'api_key': 'test'}})
        self.c = self.app.test_client()

    def tearDown(self):
        for p in (self.spath, self.epath):
            if os.path.exists(p):
                os.remove(p)


class TestPages(WebBase):
    def test_index_loads(self):
        r = self.c.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Newsman', r.data)

    def test_stats(self):
        d = self.c.get('/api/stats').get_json()
        self.assertEqual(d['stories'], 2)
        self.assertEqual(d['emails'], 2)
        self.assertEqual(d['quarantined'], 1)

    def test_labels(self):
        labels = {l['label'] for l in self.c.get('/api/labels').get_json()}
        self.assertEqual(labels, {"Risky-Biz", "China Watching/Chinese-Doom-Scroll"})


class TestStoriesApi(WebBase):
    def test_all(self):
        self.assertEqual(len(self.c.get('/api/stories').get_json()), 2)

    def test_fulltext(self):
        rows = self.c.get('/api/stories?text=ransomware').get_json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], "KillSec dismantled")

    def test_keyword_tag(self):
        rows = self.c.get('/api/stories?keyword=Weibo').get_json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['category'], "trend")

    def test_category_filter(self):
        rows = self.c.get('/api/stories?category=cyber-incident').get_json()
        self.assertEqual(len(rows), 1)

    def test_label_filter(self):
        rows = self.c.get('/api/stories?label=Doom-Scroll').get_json()
        self.assertEqual(len(rows), 1)

    def test_limit(self):
        self.assertEqual(len(self.c.get('/api/stories?limit=1').get_json()), 1)

    def test_story_shape_has_citation_link(self):
        r = self.c.get('/api/stories?text=ransomware').get_json()[0]
        self.assertIn('rfc822msgid', r['source_email_link'])
        self.assertEqual(r['urls'], ["https://x/1"])
        self.assertRegex(r['date'], r'\d{4}-\d{2}-\d{2}')


class TestEmailsApi(WebBase):
    def test_list(self):
        self.assertEqual(len(self.c.get('/api/emails').get_json()), 2)

    def test_flagged_only(self):
        rows = self.c.get('/api/emails?flagged=1').get_json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['injection_flags'], "instruction_override")

    def test_list_excludes_body(self):
        self.assertNotIn('body', self.c.get('/api/emails').get_json()[0])

    def test_detail_has_body(self):
        eid = self.c.get('/api/emails').get_json()[0]['id']
        d = self.c.get(f'/api/emails/{eid}').get_json()
        self.assertIn('body', d)
        self.assertTrue(d['body'])

    def test_detail_404(self):
        self.assertEqual(self.c.get('/api/emails/99999').status_code, 404)

    def test_email_labels(self):
        labels = {l['label'] for l in self.c.get('/api/email_labels').get_json()}
        self.assertEqual(labels, {"Risky-Biz", "Suspicious"})


class TestQueryApi(WebBase):
    def test_query_mocked(self):
        orig = webserver._run_nl_query
        webserver._run_nl_query = lambda q, config: f"ANSWER to: {q}"
        try:
            d = self.c.post('/api/query', json={'question': 'how many?'}).get_json()
            self.assertEqual(d['answer'], "ANSWER to: how many?")
        finally:
            webserver._run_nl_query = orig

    def test_query_empty_400(self):
        self.assertEqual(self.c.post('/api/query', json={'question': '  '}).status_code, 400)

    def test_query_error_500(self):
        orig = webserver._run_nl_query

        def boom(q, config):
            raise RuntimeError("nope")
        webserver._run_nl_query = boom
        try:
            r = self.c.post('/api/query', json={'question': 'x'})
            self.assertEqual(r.status_code, 500)
            self.assertIn('RuntimeError', r.get_json()['error'])
        finally:
            webserver._run_nl_query = orig

    def test_query_missing_key_400(self):
        orig = webserver._run_nl_query

        def needs_key(q, config):
            raise SystemExit("Set [llm].api_key in newsletter.toml to use the summarizer.")
        webserver._run_nl_query = needs_key
        try:
            r = self.c.post('/api/query', json={'question': 'x'})
            self.assertEqual(r.status_code, 400)
        finally:
            webserver._run_nl_query = orig


class TestHelpers(unittest.TestCase):
    def test_epoch(self):
        self.assertIsInstance(webserver._epoch("2026-01-01"), int)
        self.assertIsNone(webserver._epoch(""))
        self.assertIsNone(webserver._epoch("bad"))

    def test_story_out_no_message_id(self):
        out = webserver._story_out({"title": "t", "summary": "s", "label": "L",
                                    "category": "other", "published_ts": 0, "message_id": ""})
        self.assertEqual(out['source_email_link'], '')
        self.assertEqual(out['date'], '')


class TestCli(unittest.TestCase):
    def parse(self, *argv):
        return webserver.build_parser().parse_args(list(argv))

    def test_defaults_localhost_only(self):
        a = self.parse()
        self.assertEqual(a.host, '127.0.0.1')
        self.assertFalse(a.debug)

    def test_all_interfaces(self):
        self.assertEqual(self.parse('--all-interfaces').host, '0.0.0.0')
        self.assertEqual(self.parse('-a').host, '0.0.0.0')

    def test_explicit_host_and_port(self):
        a = self.parse('--host', '192.168.1.20', '-p', '8080')
        self.assertEqual((a.host, a.port), ('192.168.1.20', 8080))

    def test_host_and_all_interfaces_conflict(self):
        with self.assertRaises(SystemExit):
            self.parse('--host', '10.0.0.1', '-a')

    def test_debug_refused_on_all_interfaces(self):
        orig_run = webserver.Flask.run
        webserver.Flask.run = lambda *a, **k: self.fail("server should not start")
        try:
            with self.assertRaises(SystemExit):
                webserver.main(['-a', '--debug'])
        finally:
            webserver.Flask.run = orig_run


if __name__ == "__main__":
    unittest.main(verbosity=2)
