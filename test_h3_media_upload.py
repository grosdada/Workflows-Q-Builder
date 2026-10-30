"""Director media references must be present in the queued API workflow."""

import copy
import json
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import h3_director_build as director
import queue_h3_multishot as queue


class H3MediaUploadTest(unittest.TestCase):
    def test_queue_cli_sends_video_to_selected_worker(self):
        uploads = []
        prompts = []

        class Worker(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def respond(self, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/queue":
                    self.respond({"queue_running": [], "queue_pending": []})
                elif self.path == "/object_info":
                    self.respond({})
                elif self.path == "/history/queued-media":
                    self.respond({"queued-media": {"status": {"completed": True}, "outputs": {}}})
                else:
                    self.send_error(404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                if self.path == "/upload/image":
                    match = re.search(rb'filename="([^"]+)"', body)
                    filename = match.group(1).decode("utf-8") if match else ""
                    uploads.append(filename)
                    self.respond({"name": filename, "subfolder": "musedirector/qbuilder_qbuilder"})
                elif self.path == "/prompt":
                    prompts.append(json.loads(body)["prompt"])
                    self.respond({"prompt_id": "queued-media"})
                else:
                    self.send_error(404)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Worker)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                video = root / "source video.mp4"
                video.write_bytes(b"small video fixture")
                brief = {"settings": {"mode": "reference"},
                         "ref_videos": [{"file": str(video), "role": "reference"}],
                         "cuts": [{"seconds": 3, "text": "A slow pan.", "speakers": []}]}
                briefs = root / "briefs.json"
                briefs.write_text(json.dumps([brief]), encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).parent / "queue_h3_multishot.py"),
                     "--briefs", str(briefs), "--template",
                     str(Path(__file__).parent / "h3_director_api.json"),
                     "--queue", "--server", f"http://127.0.0.1:{server.server_port}",
                     "--job-ledger", str(root / "jobs.json")],
                    capture_output=True, text=True, timeout=20, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(len(uploads), 1)
                self.assertRegex(uploads[0], r"^ref_[0-9a-f]{16}\.mp4$")
                self.assertEqual(len(prompts), 1)
                node = next(item for item in prompts[0].values()
                            if isinstance(item, dict)
                            and item.get("class_type") == queue.DIRECTOR_CLASS)
                timeline = json.loads(node["inputs"]["timeline_data"])
                video_ref = timeline["refVideos"][0]
                self.assertEqual(video_ref["file"],
                                 f"musedirector/qbuilder_qbuilder/{uploads[0]}")
                self.assertEqual(video_ref["fileName"], uploads[0])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_video_and_audio_references_reach_api_workflow_and_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = {
                "character.png": root / "character.png",
                "room.jpg": root / "room.jpg",
                "source-long-video-name.mp4": root / "source-long-video-name.mp4",
                "voice.wav": root / "voice.wav",
            }
            for source in sources.values():
                source.write_bytes(b"test media")
            brief = {
                "settings": {"mode": "reference", "output_name": "Muse/Video/media_test"},
                "characters": [{"file": "character.png"}],
                "background": {"file": "room.jpg"},
                "ref_videos": [{"file": "source-long-video-name.mp4", "role": "reference"}],
                "ref_audios": [{"file": "voice.wav", "retention": "reference"}],
                "cuts": [{"seconds": 3, "text": "<Subject 1> looks around.", "speakers": []}],
            }
            settings, source_timeline, _ = director.normalise(brief)
            template = json.loads(
                (Path(__file__).parent / "h3_director_api.json").read_text(encoding="utf-8-sig")
            )
            uploads = []

            def resolve(entry, _settings):
                return sources[Path(entry["file"]).name]

            def upload(server, source, subfolder, overwrite, filename):
                uploads.append((server, source.name, filename, overwrite))
                return f"{subfolder}/{filename}"

            cache = {}
            with patch.object(queue, "resolve_local_reference", side_effect=resolve), patch.object(
                queue, "upload_image_path", side_effect=upload
            ):
                for server in ("http://first:8188", "http://second:8188"):
                    timeline = copy.deepcopy(source_timeline)
                    registry = []
                    count = queue.upload_h3_images(server, timeline, settings, cache, registry)
                    workflow = copy.deepcopy(template)
                    queue.patch_api_workflow(workflow, settings, timeline, "Muse/Video/media_test")
                    node = next(
                        item for item in workflow.values()
                        if isinstance(item, dict) and item.get("class_type") == queue.DIRECTOR_CLASS
                    )
                    final = json.loads(node["inputs"]["timeline_data"])
                    self.assertEqual(count, 4)
                    self.assertEqual(len(registry), 4)
                    for key, index, filename in (
                        ("characters", 0, "character.png"),
                        ("refVideos", 0, "source-long-video-name.mp4"),
                        ("refAudios", 0, "voice.wav"),
                    ):
                        entry = final[key][index]
                        self.assertTrue(entry["file"].startswith("musedirector/qbuilder_"))
                        self.assertEqual(entry["fileName"], Path(entry["file"]).name)
                        self.assertEqual(Path(entry["fileName"]).suffix, Path(filename).suffix)
                    self.assertEqual(final["background"]["fileName"],
                                     Path(final["background"]["file"]).name)
                    queue.upload_h3_images(server, copy.deepcopy(source_timeline), settings, cache)
            self.assertEqual(len(uploads), 8)
            self.assertEqual({item[0] for item in uploads},
                             {"http://first:8188", "http://second:8188"})
            self.assertEqual(
                {item[2] for item in uploads if item[0] == "http://first:8188"},
                {item[2] for item in uploads if item[0] == "http://second:8188"},
            )
            self.assertTrue(all(item[2].startswith("ref_") and item[3] == "true"
                                for item in uploads))


if __name__ == "__main__":
    unittest.main()
