import copy
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from recall import core, source, sync
from recall.cli import main
from recall.jdy import Client, LOG_FIELDS, RECORD_FIELDS, load_config, date_string
from recall.privacy import clean_text


def candidate(n=1, date=None, title=None):
    return {"date": date or core.today(), "title": title or "虚构测试企业召回测试车型%d" % n,
            "url": "https://www.samr.gov.cn/zw/zh/art/2026/art_fixture_%d.html" % n}


def row(c):
    return {"日期str": c["date"], "公司": "虚构测试企业", "车型": "测试车型",
            "数量": 2, "分类": "螺栓", "原因": "测试螺栓断裂",
            "来源URL": c["url"], "备注": ""}


def review(c, include=False):
    return {**c, "verdict": "测试螺栓缺陷" if include else "测试排除结论",
            "in_table": include, "crosscheck": "测试双路核对一致",
            "crosscheck_agreed": True, "rows": [row(c)] if include else []}


def config():
    return {"app_id": "fixture_app", "recall_entry_id": "fixture_recall",
            "log_entry_id": "fixture_log",
            "record_fields": {key: "field_record_%d" % n for n, key in enumerate(RECORD_FIELDS)},
            "log_fields": {key: "field_log_%d" % n for n, key in enumerate(LOG_FIELDS)},
            "private_terms": []}


class MemoryClient:
    def __init__(self):
        self.records = {"fixture_recall": [], "fixture_log": []}
        self.list_calls = []
        self.create_calls = []
        self.fail_at = None
        self.accept_then_fail = False

    def list(self, entry):
        self.list_calls.append(entry)
        return copy.deepcopy(self.records[entry])

    def create(self, entry, fields):
        self.create_calls.append((entry, copy.deepcopy(fields)))
        fail = len(self.create_calls) == self.fail_at
        did = "fixture_%d" % (sum(map(len, self.records.values())) + 1)
        if not fail or self.accept_then_fail:
            self.records[entry].append({"_id": did, **fields})
        if fail:
            raise RuntimeError("simulated failure")
        return did


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        core.initialize(self.base)
        self.path = self.base / "batches" / "current.json"
        self.cfg = config()
        self.client = MemoryClient()
        self.network = patch("urllib.request.OpenerDirector.open",
                             side_effect=AssertionError("Tests must not use the network"))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.tmp.cleanup()

    def prepare_reviewed(self, include=False, n=1):
        candidates = [candidate(i) for i in range(1, n + 1)]
        batch = core.prepare(candidates, self.base)
        core.review(batch, [review(c, include) for c in candidates])
        core.save(self.path, batch)
        return batch

    def test_no_new_writes_only_log(self):
        core.save(self.base / "ledger.json", {"entries": [review(candidate())]})
        core.prepare([candidate()], self.base)
        result = sync.synchronize(self.base, self.cfg, client=self.client)
        self.assertEqual(self.client.list_calls, [])
        self.assertEqual(len(self.client.create_calls), 1)
        entry, fields = self.client.create_calls[0]
        self.assertEqual(entry, "fixture_log")
        self.assertEqual(fields[self.cfg["log_fields"]["逐条明细"]], "当日新增公告 0 条，无逐条判定")
        self.assertEqual(result["recall_rows"], 0)

    def test_only_new_rows_uploaded(self):
        core.save(self.base / "ledger.json", {"entries": [review(candidate(99))]})
        batch = core.prepare([candidate(99), candidate()], self.base)
        core.review(batch, [review(candidate(), True)])
        core.save(self.path, batch)
        sync.synchronize(self.base, self.cfg, client=self.client)
        self.assertEqual(len(self.client.records["fixture_recall"]), 1)
        record = self.client.records["fixture_recall"][0]
        self.assertEqual(record[self.cfg["record_fields"]["来源URL"]], candidate()["url"])

    def test_completed_retry_is_noop(self):
        self.prepare_reviewed(True)
        sync.synchronize(self.base, self.cfg, client=self.client)
        calls = len(self.client.create_calls)
        sync.synchronize(self.base, self.cfg, client=self.client)
        self.assertEqual(len(self.client.create_calls), calls)

    def test_partial_failure_resumes_without_reanalysis(self):
        self.prepare_reviewed(True, 2)
        self.client.fail_at = 2
        with self.assertRaises(RuntimeError):
            sync.synchronize(self.base, self.cfg, client=self.client)
        saved = core.read(self.path)
        self.assertEqual(len(saved["uploaded"]), 1)
        self.assertEqual(core.read(self.base / "ledger.json")["entries"], [])
        restored = core.prepare([candidate(9)], self.base)
        self.assertEqual(restored["reviews"], saved["reviews"])
        self.client.fail_at = None
        sync.synchronize(self.base, self.cfg, client=self.client)
        self.assertEqual(len(self.client.records["fixture_recall"]), 2)
        self.assertEqual(len(core.read(self.base / "ledger.json")["entries"]), 2)

    def test_uncertain_record_is_reconciled(self):
        self.prepare_reviewed(True)
        self.client.fail_at = 1
        self.client.accept_then_fail = True
        with self.assertRaises(RuntimeError):
            sync.synchronize(self.base, self.cfg, client=self.client)
        self.client.fail_at = None
        sync.synchronize(self.base, self.cfg, client=self.client)
        self.assertEqual(len(self.client.records["fixture_recall"]), 1)
        self.assertEqual(len(core.read(self.path)["created_ids"]), 1)

    def test_uncertain_log_is_reconciled(self):
        self.prepare_reviewed()
        self.client.fail_at = 1
        self.client.accept_then_fail = True
        with self.assertRaises(RuntimeError):
            sync.synchronize(self.base, self.cfg, client=self.client)
        self.client.fail_at = None
        sync.synchronize(self.base, self.cfg, client=self.client)
        self.assertEqual(len(self.client.records["fixture_log"]), 1)

    def test_remote_existing_not_counted_as_created(self):
        self.prepare_reviewed(True)
        fields = self.cfg["record_fields"]
        r = row(candidate())
        self.client.records["fixture_recall"].append({
            "_id": "fixture_preexisting",
            fields["来源URL"]: r["来源URL"], fields["日期"]: r["日期str"],
            fields["公司"]: r["公司"], fields["数量"]: r["数量"],
        })
        sync.synchronize(self.base, self.cfg, client=self.client)
        log = self.client.records["fixture_log"][0]
        self.assertEqual(log[self.cfg["log_fields"]["本次推送简道云"]], 0)

    def test_dry_run_no_credentials_no_network_no_state_change(self):
        self.prepare_reviewed(True)
        before = self.path.read_bytes()
        result = sync.synchronize(self.base, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(before, self.path.read_bytes())

    def test_reject_unreviewed_upload(self):
        core.prepare([candidate()], self.base)
        with self.assertRaises(ValueError):
            sync.synchronize(self.base, self.cfg, client=self.client)
        self.assertEqual(self.client.create_calls, [])

    def test_reject_historical_review(self):
        batch = core.prepare([candidate()], self.base)
        with self.assertRaises(ValueError):
            core.review(batch, [review(candidate(2))])

    def test_reject_unconfirmed_crosscheck(self):
        batch = core.prepare([candidate()], self.base)
        r = review(candidate(), True)
        r["crosscheck_agreed"] = False
        with self.assertRaises(ValueError):
            core.review(batch, [r])

    def test_reject_duplicate_rows(self):
        batch = self.prepare_reviewed(True)
        batch["reviews"][0]["rows"] *= 2
        core.save(self.path, batch)
        with self.assertRaises(ValueError):
            sync.synchronize(self.base, self.cfg, client=self.client)

    def test_empty_list_is_error(self):
        with self.assertRaises(ValueError):
            core.prepare([], self.base)

    def test_metadata_deduplicates(self):
        batch = core.prepare([candidate(), candidate()], self.base)
        self.assertEqual(len(batch["new"]), 1)

    def test_full_title_not_prefix(self):
        a = candidate(title="虚构测试公司召回部分甲车型")
        b = candidate(title="虚构测试公司召回部分乙车型")
        a.pop("url")
        b.pop("url")
        self.assertIsNone(core.find([a], b))

    def test_url_variants(self):
        a = candidate()
        b = {**a, "url": a["url"].replace("https:", "http:") + "?tracking=1#part"}
        self.assertIsNotNone(core.find([a], b))

    def test_distinct_urls_not_collapsed(self):
        a = candidate(1, title="相同完整标题")
        b = candidate(2, title="相同完整标题")
        self.assertIsNone(core.find([a], b))

    def test_backdated_unknown_not_skipped(self):
        core.save(self.base / "baseline.json", {"entries": [candidate(1, date="2020-01-01")]})
        batch = core.prepare([candidate(1, date="2020-01-01"), candidate(2, date="2020-01-01")], self.base)
        self.assertEqual(len(batch["new"]), 1)

    def test_plain_text_details(self):
        batch = self.prepare_reviewed(True, 2)
        detail = core.log_text(batch)["detail"]
        self.assertIn("第 1 条\n首次核对：", detail)
        self.assertIn("\n\n第 2 条\n", detail)
        self.assertNotIn("|", detail)
        self.assertNotIn("---", detail)

    def test_formatter_preserves_zero(self):
        self.assertIn("数量：0", core.format_details([{"数量": 0}]))

    def test_privacy_filter(self):
        value = clean_text(r"C:\Example\private\report.xlsx tenant-label", ["tenant-label"])
        self.assertNotIn("Example", value)
        self.assertNotIn("tenant-label", value)
        self.assertNotIn(".xlsx", value)

    def test_lock_overlap(self):
        with core.lock(self.base):
            with self.assertRaises(RuntimeError):
                with core.lock(self.base):
                    pass

    def test_initialize_never_overwrites(self):
        with self.assertRaises(ValueError):
            core.initialize(self.base)

    def test_placeholder_config_is_rejected(self):
        path = self.base / "config.json"
        core.save(path, {"app_id": "REPLACE_WITH_YOUR_APP_ID"})
        with self.assertRaises(ValueError):
            load_config(path)

    def test_api_malformed_page_is_error(self):
        client = Client(self.cfg, key="fixture_key")
        with patch.object(client, "post", return_value={"code": 500}):
            with self.assertRaises(RuntimeError):
                client.list("fixture_recall")

    def test_api_stalled_cursor_is_error(self):
        client = Client(self.cfg, key="fixture_key")
        with patch.object(client, "post", return_value={"data": [{"_id": "same"}] * 100}):
            with self.assertRaises(RuntimeError):
                client.list("fixture_recall")

    def test_timezone(self):
        self.assertEqual(date_string("2020-01-01T16:00:00Z"), "2020-01-02")

    def test_snapshot_checks_coverage(self):
        with self.assertRaises(ValueError):
            source.make_snapshot([candidate()], ["https://www.samr.gov.cn/zw/zh/"], "1900-01-01")

    def test_snapshot_rejects_stale(self):
        snapshot = source.make_snapshot([candidate()], ["https://www.samr.gov.cn/zw/zh/"], core.today())
        snapshot["checked_at"] = "2020-01-01T00:00:00+08:00"
        path = self.base / "input.json"
        core.save(path, snapshot)
        with self.assertRaises(ValueError):
            source.load_snapshot(path)

    def test_listing_parses_json_and_html(self):
        c = candidate()
        self.assertEqual(source.parse_listing(json.dumps([c]), "https://www.samr.gov.cn/zw/zh/")[0]["title"], c["title"])
        html = '<ul><li><a href="%s">%s</a><span>%s</span></li></ul>' % (c["url"], c["title"], c["date"])
        self.assertEqual(source.parse_listing(html, "https://www.samr.gov.cn/zw/zh/")[0]["url"], c["url"])

    def test_empty_js_listing_is_error(self):
        with self.assertRaises(ValueError):
            source.parse_listing("<html><script>loadList()</script></html>", "https://www.samr.gov.cn/zw/zh/")

    def test_listing_rejects_undated_rows(self):
        c = candidate()
        html = '<li><a href="%s">%s</a></li>' % (c["url"], c["title"])
        with self.assertRaises(ValueError):
            source.parse_listing(html, "https://www.samr.gov.cn/zw/zh/")

    def test_official_sources_only(self):
        with self.assertRaises(ValueError):
            source.official_url("https://example.invalid/list")
        with self.assertRaises(ValueError):
            source.official_url(candidate()["url"])

    def test_cli_pending_and_dry_run(self):
        snapshot = source.make_snapshot([candidate()], ["https://www.samr.gov.cn/zw/zh/"], core.today())
        path = self.base / "snapshot.json"
        reviews = self.base / "review.json"
        core.save(path, snapshot)
        core.save(reviews, [review(candidate(), True)])
        prefix = ["--home", str(self.base)]
        with patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(main(prefix + ["prepare", "--snapshot", str(path)]), 3)
            self.assertEqual(main(prefix + ["review", "--input", str(reviews)]), 0)
            self.assertEqual(main(prefix + ["sync", "--dry-run"]), 0)

    def test_cli_run_without_new_uses_logger(self):
        core.save(self.base / "ledger.json", {"entries": [review(candidate())]})
        snapshot = source.make_snapshot([candidate()], ["https://www.samr.gov.cn/zw/zh/"], core.today())
        path = self.base / "snapshot.json"
        core.save(path, snapshot)
        with patch("sys.stdout", new=io.StringIO()), patch("recall.cli.load_config", return_value=self.cfg), patch("recall.sync.Client", return_value=self.client):
            self.assertEqual(main(["--home", str(self.base), "run", "--snapshot", str(path)]), 0)
        self.assertEqual(self.client.list_calls, [])
        self.assertEqual(len(self.client.records["fixture_log"]), 1)

    def test_partial_json_listing_is_rejected(self):
        partial = {"title": "测试企业召回汽车", "url": candidate(2)["url"]}
        with self.assertRaises(ValueError):
            source.parse_listing(json.dumps([candidate(), partial]), "https://www.samr.gov.cn/zw/zh/")

    def test_truncated_metadata_is_rejected(self):
        with self.assertRaises(ValueError):
            core.validate_candidates([candidate(title="测试企业召回部分...")])

    def test_credentials_not_echoed_in_http_error(self):
        from urllib.error import HTTPError
        client = Client(self.cfg, key="fixture_secret_not_for_output")
        failure = HTTPError("https://api.jiandaoyun.com", 403, "fixture_secret_not_for_output", {}, None)
        with patch.object(client._opener, "open", side_effect=failure):
            with self.assertRaises(RuntimeError) as caught:
                client.list("fixture_recall")
        self.assertNotIn("fixture_secret_not_for_output", str(caught.exception))
        self.assertIn("HTTP 403", str(caught.exception))

    def test_secret_scanner_detects_identifiers_and_ignored_data(self):
        from tools.check_public import inspect
        self.assertTrue(inspect("bad.py", "a" * 24))
        self.assertTrue(inspect("bad.py", "_widget_" + "12345678"))
        self.assertTrue(inspect("private/config.json", "{}"))
        self.assertFalse(inspect("config.example.json", '{"app_id":"REPLACE_WITH_YOUR_APP_ID"}'))


if __name__ == "__main__":
    unittest.main()
