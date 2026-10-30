"""Regression tests for duplicate ComfyUI output references and repeated copies."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from queue_ltx_multishot import copy_prompt_outputs


class CopyPromptOutputsTests(unittest.TestCase):
    def test_same_output_from_two_nodes_and_repeat_copy(self):
        item = {"filename": "shot.mp4", "subfolder": "Muse/Video", "type": "output"}
        history = {"prompt-1": {"status": {"completed": True},
                                "outputs": {"save": {"videos": [item]},
                                            "preview": {"videos": [item]}}}}
        requests = []

        def open_url(url, timeout):
            requests.append(url)
            if "/history/" in url:
                return io.BytesIO(json.dumps(history).encode())
            return io.BytesIO(b"same video")

        with tempfile.TemporaryDirectory() as folder, patch(
            "queue_ltx_multishot.urllib.request.urlopen", side_effect=open_url
        ):
            first = copy_prompt_outputs("http://worker:8188", "prompt-1", folder)
            second = copy_prompt_outputs("http://worker:8188", "prompt-1", folder)
            self.assertEqual(first, second)
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["shot.mp4"])
            self.assertEqual(sum("/view?" in url for url in requests), 2)

    def test_different_content_keeps_both_versions(self):
        item = {"filename": "shot.mp4", "type": "output"}
        history = {"prompt-1": {"status": {"completed": True},
                                "outputs": {"save": {"videos": [item]}}}}
        content = [b"first", b"second"]

        def open_url(url, timeout):
            if "/history/" in url:
                return io.BytesIO(json.dumps(history).encode())
            return io.BytesIO(content.pop(0))

        with tempfile.TemporaryDirectory() as folder, patch(
            "queue_ltx_multishot.urllib.request.urlopen", side_effect=open_url
        ):
            copy_prompt_outputs("http://worker:8188", "prompt-1", folder)
            copy_prompt_outputs("http://worker:8188", "prompt-1", folder)
            self.assertEqual((Path(folder) / "shot.mp4").read_bytes(), b"first")
            self.assertEqual((Path(folder) / "shot_2.mp4").read_bytes(), b"second")


if __name__ == "__main__":
    unittest.main()
