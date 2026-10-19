"""Offline contract tests for the Q-Builder H3-Orbital integration."""

import json
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import h3_orbital_build as orbital
import queue_h3_orbital as queue


class OrbitalQBuilderTests(unittest.TestCase):
    def test_start_only_removes_optional_loaders(self):
        workflow = orbital.build_workflow({"id": "one", "start_image": "C:/start.png",
                                           "mode": "orbit", "length": 73})
        self.assertEqual(workflow["8"]["inputs"]["mode"], "orbit")
        self.assertEqual(workflow["8"]["inputs"]["length"], 73)
        self.assertNotIn("21", workflow)
        self.assertNotIn("31", workflow)
        self.assertNotIn("40", workflow)

    def test_sheets_map_to_selected_nodes_only(self):
        scene = {"id": "two", "start_image": "C:/start.png",
                 "character_refs": ["C:/face.png"], "set_refs": ["C:/set.png"],
                 "mode": "path", "path_preset": "half-right", "length": 124}
        workflow = orbital.build_workflow(scene)
        self.assertEqual(workflow["8"]["inputs"]["character_1"], ["21", 0])
        self.assertEqual(workflow["8"]["inputs"]["set_1"], ["31", 0])
        self.assertNotIn("22", workflow)
        self.assertNotIn("32", workflow)
        self.assertEqual(workflow["5"]["inputs"]["strength_model"], 0.0)
        self.assertIn("half circle", workflow["8"]["inputs"]["prompt"])

    def test_prepare_uploads_only_scene_images_and_keeps_recovery_names(self):
        with tempfile.TemporaryDirectory() as folder:
            start = Path(folder) / "start.png"
            face = Path(folder) / "face.png"
            start.write_bytes(b"start")
            face.write_bytes(b"face")
            scene = {"id": "three", "start_image": str(start),
                     "character_refs": [str(face)], "set_refs": [], "length": 73}
            def fake_upload(server, source, subfolder, overwrite, filename):
                self.assertEqual(server, "http://worker:8188")
                self.assertEqual(subfolder, "h3orbital/qbuilder")
                return f"{subfolder}/{filename}"
            with patch.object(queue, "upload_image_path", side_effect=fake_upload) as upload:
                cache = {}
                workflow, references = queue.prepare(scene, "http://worker:8188", cache)
                queue.prepare(scene, "http://worker:8188", cache)
            self.assertEqual(upload.call_count, 2)
            self.assertEqual(len(references), 2)
            self.assertTrue(workflow["1"]["inputs"]["image"].startswith("h3orbital/qbuilder/"))
            self.assertEqual(workflow["21"]["inputs"]["image"],
                             f"{references[1]['subfolder']}/{references[1]['filename']}")
            json.dumps(workflow)

    def test_cli_dry_run_never_contacts_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            start = Path(folder) / "start.png"
            start.write_bytes(b"not used by dry run")
            scenes = Path(folder) / "scenes.json"
            scenes.write_text(json.dumps([{"id": "dry_run", "start_image": str(start),
                                           "mode": "orbit", "length": 73}]), encoding="utf-8")
            output = io.StringIO()
            with patch.object(sys, "argv", ["queue_h3_orbital.py", "--scenes", str(scenes), "--dry-run"]), \
                 patch.object(queue, "initial_loads", side_effect=AssertionError("network access")), \
                 contextlib.redirect_stdout(output):
                queue.main()
            self.assertIn("dry_run: orbit, 73 images", output.getvalue())


if __name__ == "__main__":
    unittest.main()
