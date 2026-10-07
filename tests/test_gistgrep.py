"""Black-box tests for the gistgrep CLI.

Each test builds a small cache in a temp dir and points gistgrep at it with
GISTGREP_CACHE, so nothing touches ~/.cache or the network. The JSON shapes
tested here are a contract: launchers and other UIs parse them.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

BIN = str(Path(__file__).resolve().parent.parent / "bin" / "gistgrep")


def iso(days_ago):
    t = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class CacheCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name) / "cache"
        (self.cache / "gists").mkdir(parents=True)
        # An empty PATH proves a command never shells out to gh.
        self.empty_path = Path(self._tmp.name) / "empty-bin"
        self.empty_path.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def add_gist(self, gid, description, files, days_ago=10, public=False, summary=None):
        d = self.cache / "gists" / gid
        d.mkdir()
        meta = {
            "id": gid,
            "description": description,
            "updated_at": iso(days_ago),
            "html_url": f"https://gist.github.com/someone/{gid}",
            "public": public,
            "files": list(files),
        }
        (d / "_meta.json").write_text(json.dumps(meta))
        for name, content in files.items():
            (d / name).write_text(content)
        if summary is not None:
            summary.setdefault("updated_at", meta["updated_at"])
            (d / "_summary.json").write_text(json.dumps(summary))
        return d

    def write_state(self, **state):
        (self.cache / "state.json").write_text(json.dumps(state))

    def read_state(self):
        return json.loads((self.cache / "state.json").read_text())

    def run_cli(self, *args, path=None, env=None):
        full_env = dict(os.environ)
        full_env["GISTGREP_CACHE"] = str(self.cache)
        full_env["PATH"] = str(path or self.empty_path)
        full_env.update(env or {})
        return subprocess.run([sys.executable, BIN, *args], capture_output=True,
                              text=True, env=full_env, timeout=60)

    def search_json(self, *query, limit=None):
        args = ["--no-sync", "--json"]
        if limit:
            args += ["--limit", str(limit)]
        r = self.run_cli(*args, *query)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def fake_model(self, script):
        """Install a stand-in for the compiled Apple Intelligence helper."""
        p = self.cache / "apple-llm"
        p.write_text("#!/bin/sh\n" + script)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)


class SearchTests(CacheCase):
    def test_every_word_must_match(self):
        self.add_gist("both", "Ably notes", {"a.md": "chat sdk notes"})
        self.add_gist("one", "Ably notes", {"a.md": "pubsub only"})
        ids = [r["id"] for r in self.search_json("ably", "chat")]
        self.assertEqual(ids, ["both"])

    def test_title_match_outranks_a_word_repeated_in_a_long_body(self):
        # Ids chosen so directory or id order would put the body match first.
        self.add_gist("z-title", "Pricing proposal", {"a.md": "short"})
        self.add_gist("a-body", "Unrelated notes", {"a.md": "pricing " * 500})
        results = self.search_json("pricing")
        self.assertEqual([r["id"] for r in results], ["z-title", "a-body"])
        self.assertGreater(results[0]["score"], results[1]["score"])

    def test_ties_go_to_the_newest_then_the_id(self):
        self.add_gist("b-old", "Notes", {"a": "needle"}, days_ago=400)
        self.add_gist("c-new", "Notes", {"a": "needle"}, days_ago=300)
        self.add_gist("a-old", "Notes", {"a": "needle"}, days_ago=400)
        ids = [r["id"] for r in self.search_json("needle")]
        self.assertEqual(ids, ["c-new", "a-old", "b-old"])

    def test_phrase_bonus_scores_but_hits_stay_raw(self):
        self.add_gist("phrase", "Ably chat notes", {"a": "x"})
        self.add_gist("apart", "Chat about Ably", {"a": "x"})
        results = self.search_json("ably", "chat")
        self.assertEqual([r["id"] for r in results], ["phrase", "apart"])
        self.assertEqual(results[0]["hits"], ["title×2"])

    def test_hits_report_raw_counts(self):
        self.add_gist("body", "Unrelated notes", {"a.md": "pricing " * 50})
        [r] = self.search_json("pricing")
        self.assertEqual(r["hits"], ["body×50"])

    def test_json_fields_summary_and_snippet(self):
        self.add_gist(
            "g1", "Deploy script", {"deploy.sh": "#!/bin/sh\necho start\nfly deploy --app web\n"},
            public=True, summary={"summary": "Deploys the web app", "files": {}})
        [r] = self.search_json("fly", "deploy")
        self.assertEqual(r["id"], "g1")
        self.assertEqual(r["description"], "Deploy script")
        self.assertTrue(r["public"])
        self.assertEqual(r["files"], ["deploy.sh"])
        self.assertEqual(r["html_url"], "https://gist.github.com/someone/g1")
        self.assertEqual(r["summary"], "Deploys the web app")
        self.assertEqual(r["snippet"], "fly deploy --app web")
        self.assertGreater(r["score"], 0)

    def test_long_snippet_is_trimmed_around_the_match(self):
        line = "x" * 300 + " needle " + "y" * 300
        self.add_gist("g1", "Long line", {"a.txt": line})
        [r] = self.search_json("needle")
        self.assertIn("needle", r["snippet"])
        self.assertTrue(r["snippet"].startswith("…"))
        self.assertTrue(r["snippet"].endswith("…"))
        self.assertLess(len(r["snippet"]), 160)

    def test_missing_summary_is_null(self):
        self.add_gist("g1", "Notes", {"a.md": "hello"})
        [r] = self.search_json("hello")
        self.assertIsNone(r["summary"])

    def test_empty_query_lists_most_recent_first_and_honours_limit(self):
        self.add_gist("old", "Old", {"a": "x"}, days_ago=30)
        self.add_gist("new", "New", {"a": "x"}, days_ago=1)
        self.add_gist("mid", "Mid", {"a": "x"}, days_ago=10)
        ids = [r["id"] for r in self.search_json(limit=2)]
        self.assertEqual(ids, ["new", "mid"])

    def test_no_sync_search_never_needs_gh(self):
        self.add_gist("g1", "Notes", {"a.md": "hello"})
        r = self.run_cli("--no-sync", "--json", "hello")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_plain_output_still_lists_matches(self):
        self.add_gist("g1", "Deploy script", {"deploy.sh": "fly deploy"})
        r = self.run_cli("--no-sync", "--plain", "deploy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Deploy script", r.stdout)
        self.assertIn("https://gist.github.com/someone/g1", r.stdout)


FAKE_GH = """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1" in
  auth) exit 0 ;;
  api)
    if [ -n "$GH_FAIL" ]; then echo "gh: HTTP 502: Bad Gateway" >&2; exit 1; fi
    cat "$GH_FIXTURE" ;;
  *) exit 2 ;;
esac
"""


class SyncTests(CacheCase):
    """Sync paths, against a fake `gh` that serves one gist."""

    def setUp(self):
        super().setUp()
        self.bindir = Path(self._tmp.name) / "fake-bin"
        self.bindir.mkdir()
        gh = self.bindir / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.gh_log = Path(self._tmp.name) / "gh.log"
        fixture = Path(self._tmp.name) / "gists.json"
        fixture.write_text(json.dumps([{
            "id": "g1", "description": "Synced gist", "updated_at": iso(1),
            "html_url": "https://gist.github.com/someone/g1", "public": False,
            "files": {"a.md": {"content": "hello from sync", "raw_url": ""}},
        }]))
        self.env = {"GH_LOG": str(self.gh_log), "GH_FIXTURE": str(fixture),
                    "MARKER": str(Path(self._tmp.name) / "model-ran")}
        self.path = f"{self.bindir}:/usr/bin:/bin"
        # No background work by default: model known-off, reconcile not due.
        self.write_state(apple_llm_status="unavailable", apple_llm_reason="off",
                         apple_llm_checked_at=int(time.time()), last_reconcile=int(time.time()))

    def gh_calls(self):
        return self.gh_log.read_text().splitlines() if self.gh_log.exists() else []

    def test_sync_only_reports_json_and_fills_the_cache(self):
        r = self.run_cli("--sync-only", "--json", path=self.path, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["message"], "initial sync: 1 gists")
        [hit] = self.search_json("hello")
        self.assertEqual(hit["id"], "g1")
        # Nothing new upstream: one freshness call, nothing fetched, message null.
        r = self.run_cli("--sync-only", "--json", path=self.path, env=self.env)
        self.assertIsNone(json.loads(r.stdout)["message"])

    def test_sync_only_api_failure_exits_non_zero_with_the_reason(self):
        r = self.run_cli("--sync-only", "--json", path=self.path, env={**self.env, "GH_FAIL": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("HTTP 502", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_without_sync_only_a_failed_sync_still_searches_the_cache(self):
        self.add_gist("local", "Cached deploy notes", {"a": "x"})
        r = self.run_cli("--plain", "deploy", path=self.path, env={**self.env, "GH_FAIL": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sync failed", r.stderr)
        self.assertIn("Cached deploy notes", r.stdout)

    def test_no_sync_search_starts_no_background_worker_but_a_sync_does(self):
        self.add_gist("a", "A", {"a": "x"})
        self.write_state(apple_llm_status="available", apple_llm_checked_at=int(time.time()),
                         last_reconcile=int(time.time()))
        self.fake_model('echo ran >> "$MARKER"\nwhile read -r _; do :; done\n'
                        "echo '{\"summary\":\"S\",\"files\":{}}'\n")
        marker = Path(self.env["MARKER"])

        r = self.run_cli("--no-sync", "--json", "a", path=self.path, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        time.sleep(1.0)
        self.assertFalse(marker.exists(), "a --no-sync search started the summarizer")
        self.assertEqual(self.gh_calls(), [])

        r = self.run_cli("--sync-only", "--json", path=self.path, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        for _ in range(100):                      # the worker is detached; wait for it
            if marker.exists() and not (self.cache / "summarize.lock").exists():
                break
            time.sleep(0.05)
        self.assertTrue(marker.exists(), "the sync did not start the summarizer")

    def test_sync_only_without_gh_fails_loudly(self):
        r = self.run_cli("--sync-only", "--json")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("gh not found", r.stderr)
        self.assertEqual(r.stdout, "")


class PreviewTests(CacheCase):
    def test_preview_json_lists_files_with_summaries_and_teasers(self):
        body = "\n".join(f"line {i}" for i in range(25))
        d = self.add_gist("g1", "Two files", {"a.md": body, "b.sh": "echo hi"},
                          summary={"summary": "Two small files",
                                   "files": {"a.md": "Numbered lines"}})
        r = self.run_cli("--preview", "g1", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        p = json.loads(r.stdout)
        self.assertEqual(p["summary"], "Two small files")
        self.assertEqual(p["summary_status"], "ready")
        a, b = p["files"]
        self.assertEqual(a["name"], "a.md")
        self.assertEqual(a["summary"], "Numbered lines")
        self.assertEqual(a["path"], str(d / "a.md"))
        self.assertEqual(a["teaser"].splitlines()[0], "line 0")
        self.assertEqual(len(a["teaser"].splitlines()), 20)
        self.assertEqual(a["more_lines"], 5)
        self.assertIsNone(b["summary"])
        self.assertEqual(b["more_lines"], 0)

    def test_preview_json_unknown_id_fails(self):
        r = self.run_cli("--preview", "nope", "--json")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown id", r.stderr)

    def test_summary_status_distinguishes_pending_failed_and_unavailable(self):
        self.add_gist("pending", "P", {"a": "x"})
        self.add_gist("failed", "F", {"a": "x"},
                      summary={"summary": "", "files": {}, "_unparsed": True,
                               "generated_at": datetime.now(timezone.utc).isoformat()})

        def status(gid):
            return json.loads(self.run_cli("--preview", gid, "--json").stdout)["summary_status"]

        self.assertEqual(status("pending"), "pending")
        self.assertEqual(status("failed"), "failed")
        self.write_state(apple_llm_status="unavailable", apple_llm_reason="switched off")
        p = json.loads(self.run_cli("--preview", "failed", "--json").stdout)
        self.assertEqual(p["summary_status"], "unavailable")
        self.assertEqual(p["summary_unavailable_reason"], "switched off")

    def test_text_preview_still_renders(self):
        self.add_gist("g1", "Deploy script", {"deploy.sh": "fly deploy"},
                      summary={"summary": "Deploys the app", "files": {}})
        r = self.run_cli("--preview", "g1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Deploy script", r.stdout)
        self.assertIn("Deploys the app", r.stdout)
        self.assertIn("deploy.sh", r.stdout)


class SummaryWorkerTests(CacheCase):
    def setUp(self):
        super().setUp()
        # A recent "available" check, so the worker trusts it and skips the smoke test.
        self.write_state(apple_llm_status="available", apple_llm_checked_at=int(time.time()))

    def test_worker_stops_and_records_when_the_model_is_off(self):
        self.add_gist("a", "A", {"a": "x"})
        self.add_gist("b", "B", {"b": "y"})
        self.fake_model('echo "unavailable: appleIntelligenceNotEnabled" >&2\nexit 3\n')
        r = self.run_cli("--summarize-queue")
        self.assertEqual(r.returncode, 0, r.stderr)
        state = self.read_state()
        self.assertEqual(state["apple_llm_status"], "unavailable")
        self.assertIn("appleIntelligenceNotEnabled", state["apple_llm_reason"])
        # No per-gist "failed" summaries written for a model that is simply off.
        self.assertEqual(list(self.cache.glob("gists/*/_summary.json")), [])

    def test_worker_summarizes_and_retries_failures_weekly(self):
        self.add_gist("new", "New", {"a": "x"})
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        stale = datetime.now(timezone.utc) - timedelta(days=8)
        self.add_gist("recent-fail", "R", {"a": "x"},
                      summary={"summary": "", "files": {}, "_unparsed": True,
                               "generated_at": recent.isoformat()})
        self.add_gist("stale-fail", "S", {"a": "x"},
                      summary={"summary": "", "files": {}, "_unparsed": True,
                               "generated_at": stale.isoformat()})
        self.fake_model("while read -r _; do :; done\necho '{\"summary\":\"Fake summary\",\"files\":{}}'\n")
        r = self.run_cli("--summarize-queue")
        self.assertEqual(r.returncode, 0, r.stderr)

        def summary(gid):
            return json.loads((self.cache / "gists" / gid / "_summary.json").read_text())

        self.assertEqual(summary("new")["summary"], "Fake summary")
        self.assertEqual(summary("stale-fail")["summary"], "Fake summary")
        self.assertEqual(summary("recent-fail")["summary"], "")

    def test_an_empty_summary_from_the_model_counts_as_failed(self):
        self.add_gist("a", "A", {"a": "x"})
        self.fake_model("while read -r _; do :; done\necho '{\"summary\":\"\",\"files\":{}}'\n")
        r = self.run_cli("--summarize-queue")
        self.assertEqual(r.returncode, 0, r.stderr)
        p = json.loads(self.run_cli("--preview", "a", "--json").stdout)
        self.assertEqual(p["summary_status"], "failed")

    def test_odd_generated_at_values_do_not_stop_the_worker(self):
        for gid, generated_at in [("naive", "2026-01-01T00:00:00"), ("null", None), ("junk", "soon")]:
            self.add_gist(gid, gid, {"a": "x"},
                          summary={"summary": "", "files": {}, "_unparsed": True,
                                   "generated_at": generated_at})
        self.fake_model("while read -r _; do :; done\necho '{\"summary\":\"Retried\",\"files\":{}}'\n")
        r = self.run_cli("--summarize-queue")
        self.assertEqual(r.returncode, 0, r.stderr)
        for gid in ("naive", "null", "junk"):
            summary = json.loads((self.cache / "gists" / gid / "_summary.json").read_text())
            self.assertEqual(summary["summary"], "Retried", gid)

    def test_failures_from_before_the_model_came_back_are_retried_at_once(self):
        self.add_gist("a", "A", {"a": "x"},
                      summary={"summary": "", "files": {}, "_unparsed": True,
                               "generated_at": (datetime.now(timezone.utc)
                                                - timedelta(hours=1)).isoformat()})
        self.write_state(apple_llm_status="unavailable", apple_llm_reason="off",
                         apple_llm_checked_at=int(time.time()) - 2 * 86400)
        self.fake_model("while read -r _; do :; done\necho '{\"summary\":\"Back on\",\"files\":{}}'\n")
        r = self.run_cli("--summarize-queue")
        self.assertEqual(r.returncode, 0, r.stderr)
        summary = json.loads((self.cache / "gists" / "a" / "_summary.json").read_text())
        if sys.platform != "darwin":
            self.assertEqual(summary["summary"], "")      # the model can't run off macOS
            return
        self.assertEqual(summary["summary"], "Back on")

    def test_a_recent_failure_with_the_model_on_all_along_waits_a_week(self):
        self.add_gist("a", "A", {"a": "x"},
                      summary={"summary": "", "files": {}, "_unparsed": True,
                               "generated_at": (datetime.now(timezone.utc)
                                                - timedelta(hours=1)).isoformat()})
        self.write_state(apple_llm_status="available", apple_llm_checked_at=int(time.time()),
                         apple_llm_available_since=int(time.time()) - 30 * 86400)
        self.fake_model("while read -r _; do :; done\necho '{\"summary\":\"Too soon\",\"files\":{}}'\n")
        self.run_cli("--summarize-queue")
        summary = json.loads((self.cache / "gists" / "a" / "_summary.json").read_text())
        self.assertEqual(summary["summary"], "")

    def test_unavailable_model_is_rechecked_after_a_day(self):
        self.add_gist("a", "A", {"a": "x"})
        self.write_state(apple_llm_status="unavailable", apple_llm_reason="off",
                         apple_llm_checked_at=int(time.time()) - 2 * 86400)
        self.fake_model("while read -r _; do :; done\necho '{\"summary\":\"Back on\",\"files\":{}}'\n")
        r = self.run_cli("--summarize-queue")
        self.assertEqual(r.returncode, 0, r.stderr)
        if sys.platform != "darwin":
            self.assertEqual(self.read_state()["apple_llm_status"], "unavailable")
            return
        self.assertEqual(self.read_state()["apple_llm_status"], "available")
        summary = json.loads((self.cache / "gists" / "a" / "_summary.json").read_text())
        self.assertEqual(summary["summary"], "Back on")


if __name__ == "__main__":
    unittest.main()
