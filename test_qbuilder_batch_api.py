"""Offline checks for the browser-facing H3 Director batch API."""

import base64
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

import qbuilder_batch_api as api
from workflows_q_builder_server import Handler


PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nsmall-test-image").decode("ascii")


def packet(seconds=15, asset_id="person"):
    return {
        "schema": "qbuilder-html/v3", "model": "h3-director",
        "aspect_ratio": "4:3", "duration": seconds,
        "dialogue_language": "none", "style_line": "Archive film.",
        "assets": {asset_id: {"filename": "person.png", "data_url": "data:image/png;base64," + PNG,
                               "description": "a person"}},
        "scenes": [{"name": "01_test", "seed": 1001, "character_refs": [asset_id],
                    "cuts": [{"seconds": seconds, "text": "<Subject 1> walks.", "speakers": []}]}],
    }


class BatchApiTests(unittest.TestCase):
    def setUp(self):
        self.settings = {"api_allowed_roots": [str(api.ROOT)],
                         "comfy_nodes": [{"name": "FORD", "url": "http://127.0.0.1:8188", "auto": True}]}

    def test_valid_packet(self):
        report, _ = api.validate({"prompts": packet()}, self.settings)
        self.assertTrue(report["ok"])
        self.assertEqual(report["scenes"][0]["duration"], 15)
        self.assertEqual(len(report["assets"]), 1)

    def test_oversize_duration_and_missing_asset(self):
        invalid = packet(16)
        invalid["scenes"][0]["background_ref"] = "missing"
        report, _ = api.validate({"prompts": invalid}, self.settings)
        self.assertFalse(report["ok"])
        self.assertIn("16s > 15s", " ".join(report["errors"]))
        self.assertIn("asset inconnu missing", " ".join(report["errors"]))

    def test_dry_run_does_not_stage_or_submit(self):
        with patch.object(api, "_workers", return_value=(self.settings["comfy_nodes"],
                                                     {"http://127.0.0.1:8188": 0})), \
             patch.object(api, "_stage_assets", side_effect=AssertionError("staging")), \
             patch.object(api, "_submit", side_effect=AssertionError("submit")):
            result = api.start({"prompts": packet(), "dry_run": True, "scenes": "01",
                                "settings_override": {"megapixels": 0.8}}, self.settings)
        self.assertEqual(result["jobs"][0]["settings"]["aspect_ratio"], "4:3 (Standard)")
        self.assertEqual(result["jobs"][0]["worker"], "FORD")

    def test_path_allowlist_rejects_sibling(self):
        with tempfile.TemporaryDirectory(dir=api.ROOT) as temp:
            root = Path(temp) / "allowed"
            root.mkdir()
            outside = Path(temp) / "allowed-other" / "page.html"
            with self.assertRaises(api.BatchError):
                api.allowed_path(str(outside), {"api_allowed_roots": [str(root)]})

    def test_http_validate_and_origin_guard(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/api/batch/validate"
        body = json.dumps({"prompts": packet()}).encode()
        try:
            request = urllib.request.Request(url, body, {"Content-Type": "application/json",
                                                        "Origin": f"http://127.0.0.1:{server.server_port}"})
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertTrue(json.load(response)["ok"])
            hostile = urllib.request.Request(url, body, {"Content-Type": "application/json",
                                                        "Origin": "https://example.org"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(hostile, timeout=5)
            self.assertEqual(caught.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_fake_worker_submit_copy_and_persistence(self):
        workflow = json.loads(api.TEMPLATE.read_text(encoding="utf-8-sig"))
        types = {item.get("class_type"): {} for item in workflow.values() if isinstance(item, dict)}

        class Worker(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, data):
                body = data if isinstance(data, bytes) else json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/queue":
                    self.reply({"queue_running": [], "queue_pending": []})
                elif self.path == "/object_info":
                    self.reply(types)
                elif self.path.startswith("/history/"):
                    self.reply({"test-prompt": {"status": {"completed": True, "status_str": "success"},
                                                "outputs": {"1": {"videos": [{"filename": "test.mp4",
                                                                                "subfolder": "", "type": "output"}]}}}})
                elif self.path.startswith("/view?"):
                    self.reply(b"fake-video")
                else:
                    self.send_error(404)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                if self.path == "/upload/image":
                    self.reply({"name": "ref.png", "subfolder": "musedirector/qbuilder"})
                elif self.path == "/prompt":
                    self.reply({"prompt_id": "test-prompt"})
                else:
                    self.send_error(404)

        worker = ThreadingHTTPServer(("127.0.0.1", 0), Worker)
        thread = threading.Thread(target=worker.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(dir=api.ROOT) as temp:
                root = Path(temp)
                url = f"http://127.0.0.1:{worker.server_port}"
                settings = {"api_allowed_roots": [str(root)],
                            "comfy_nodes": [{"name": "FAKE", "url": url, "auto": True}]}
                with patch.object(api, "STORE", root / "batches.json"), \
                     patch.object(api, "STAGING", root / "staging"), \
                     patch.object(api, "default_path", return_value=root / "ledger.json"):
                    result = api.start({"prompts": packet(), "scenes": "01",
                                        "copy_results": str(root / "results")}, settings)
                    batch_id = result["batch_id"]
                    deadline = time.time() + 10
                    while time.time() < deadline:
                        current = api.detail(batch_id)
                        if current["jobs"][0]["status"] in {"done", "failed"}:
                            break
                        time.sleep(.1)
                    self.assertEqual(current["jobs"][0]["status"], "done", current)
                    self.assertEqual((root / "results" / "test.mp4").read_bytes(), b"fake-video")
                    self.assertEqual(api.detail(batch_id)["jobs"][0]["prompt_id"], "test-prompt")
                    self.assertEqual(json.loads((root / "ledger.json").read_text())["jobs"][0]["state"], "finished")
        finally:
            worker.shutdown()
            worker.server_close()
            thread.join(timeout=5)

    def test_cancel_deletes_only_selected_pending_prompt(self):
        deleted = []

        class Worker(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                body = json.dumps({"queue_running": [[1, "other-prompt"]],
                                   "queue_pending": [[2, "ours"], [3, "another-prompt"]]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                deleted.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()

        worker = ThreadingHTTPServer(("127.0.0.1", 0), Worker)
        thread = threading.Thread(target=worker.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(dir=api.ROOT) as temp:
                root = Path(temp)
                batch_id = "00000000-0000-0000-0000-000000000001"
                data = {batch_id: {"batch_id": batch_id, "created_at": 1, "worker_urls": [],
                                   "jobs": [{"scene": "01_test", "job_id": "job1", "state": "queued",
                                             "server": f"http://127.0.0.1:{worker.server_port}",
                                             "worker": "FAKE", "prompt_id": "ours"}]}}
                store = root / "batches.json"
                store.write_text(json.dumps(data), encoding="utf-8")
                with patch.object(api, "STORE", store):
                    result = api.cancel(batch_id, {"scenes": "all"})
                self.assertEqual(deleted, [{"delete": ["ours"]}])
                self.assertEqual(result["jobs"][0]["status"], "cancelled")
        finally:
            worker.shutdown()
            worker.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
