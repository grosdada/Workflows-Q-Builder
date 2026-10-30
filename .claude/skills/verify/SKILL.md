---
name: verify
description: Observe Q-Builder network CLI behavior without starting a GPU render.
---

# Q-Builder network verification

For H3 queue changes, run the real `queue_h3_multishot.py --queue` CLI against
a temporary loopback HTTP server that implements `/queue`, `/object_info`,
`/upload/image`, `/prompt`, and `/history/<prompt_id>`. Capture the received
multipart filenames and the posted API workflow, especially the Director
`timeline_data` paths. Keep `--job-ledger` in a temporary directory and omit
`--copy-results` unless copying is the behavior under review. Do not point a
verification run at the production workers: `--queue` submits real GPU work.

The focused fixture is `test_h3_media_upload.py`, which exercises the CLI and
checks video/audio references, upload cache, and a second worker. For a manual
probe, use the same loopback endpoints and inspect the CLI stdout plus the
captured `/prompt` JSON before reporting success.
