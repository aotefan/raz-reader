# Architecture

[中文](ARCHITECTURE.zh-CN.md) · [README](../README.md)

Little Reader is a browser application with a Python API and optional local model workers. Books and user state are supplied by the operator. The repository is a clean source snapshot; it contains neither the private deployment's history nor its data.

## Reading and language tools

`web/app.js` owns the bookshelf and reading state. `web/reader-boot.js` adapts the official PDF.js viewer in an iframe. PDFs stay unchanged. The frontend reads native text first; image-only pages fall back to Poppler rendering and Tesseract OCR through `engine/page_text.py`. Position-aware text filtering removes page furniture before translation or assessment.

`engine/language.py` invokes bounded subprocesses for translation and generated single-word audio. Translation uses the Argos English-to-Chinese model through CTranslate2 and SentencePiece. ECDICT is a local read-only SQLite dictionary. These optional downloads are installed separately and are never fetched by the browser.

Narration is original embedded media or content-verified external media. Sentence clips require verified timing. Visual rewards can run without spoken encouragement; the public release contains an empty voice manifest.

## Accounts and persistence

Local mode binds to loopback and uses one private local state directory. HTTPS/cloud mode adds `engine/accounts.py` and `engine/cloud_sessions.py`: salted PBKDF2 password verification, server-side session records, secure cookies, independent account directories, nicknames and remembered-account switching. Guests can read without registering. Registration can explicitly claim a guest's records; ordinary login does not merge them.

SQLite stores account metadata and each reader's progress/recordings. Recordings are audio BLOBs with assessment JSON, keyed by PDF edition and page. An edition is bound to the PDF's SHA-256. A new recording reserves a monotonically increasing version; late uploads/results cannot overwrite its successor. Only the latest take is retained by the application.

Browser localStorage holds a scoped outbox of unsynchronized progress. Writer IDs and sequence numbers reject stale writes from the same writer. This is not real-time collaborative editing: simultaneous devices may compete for a last-read position.

## Pronunciation pipeline

1. Capture the current page reference and record with MediaRecorder.
2. Validate audio and save the versioned take before assessment.
3. Reuse a cached report only for the same take, normalized reference and method.
4. Prefer an available Mac worker; otherwise invoke the server's persistent scoring subprocess.
5. Decode at 16 kHz mono, rebuild duration from samples, check silence/clipping, trim only edge silence.
6. Use OpenPronounce phoneme recognition and comparison APIs, including its pronunciation tolerance.
7. Compute `round(100 * max(0, 1 - weighted_phone_edits / expected_phone_count))`.
8. Save only if the original take is still current; render actual word/phoneme differences.

The result is a practice reference, not the upstream project's full composite score. The UI's pass line is 80. Score presentation does not change the calculation. There is no paid speech API in the normal scoring path.

`engine/compact_assessment.py` supports an optional memory-conscious export of the same model. The exporter verifies exact restoration of eligible weights; computation remains FP32. It is an advanced deployment option, not a different default model or a blanket guarantee that all small servers can score quickly.

## Optional Mac worker

The worker warms its model before issuing an outbound HTTPS long poll. The broker considers it ready only while a valid poll is waiting. There is one active remote task slot; no worker or a busy worker falls straight back to the server.

Jobs contain an opaque ID, reference text and audio, with no caller-supplied filesystem path or command. A dedicated token authenticates the worker. A three-second heartbeat lease detects interruption, while an absolute deadline prevents heartbeats from extending a task forever. Failed, invalid or expired remote work falls back; real input-quality errors are returned as errors. Late reports are rejected.

Mac tasks use private temporary files and remove them after use. The Mac does process the submitted audio; it is not a zero-data-transfer arrangement. Accounts and final state stay on the server. launchd starts the worker at user login and does not prevent sleep. No tunnel, inbound Mac service, shared LAN or VPN change is required.

## Key files

| Area | Files |
| --- | --- |
| Routes and bounds | `engine/server.py` |
| Account/session isolation | `engine/accounts.py`, `engine/cloud_sessions.py`, `web/identity.js` |
| Book metadata/progress | `engine/pipeline.py`, `web/progress.js`, `web/reading.js` |
| Page text | `engine/page_text.py`, `web/page-translation.js` |
| Recording storage/UI | `engine/recordings.py`, `web/recording-assessment.js` |
| Assessment coordinator | `engine/assessment.py`, `engine/assessment_process.py` |
| Model execution | `scripts/assessment_worker.py`, `engine/compact_assessment.py` |
| Remote broker/worker | `engine/assessment_remote.py`, `scripts/mac_assessment_worker.py` |

Cloud mode requires a same-origin HTTPS proxy. The browser talks only to that origin. The proxy serves `web/` and forwards `/api/*`; private directories are never its document root. The Python API resolves book IDs through an explicit manifest, not arbitrary client paths.

The server is still the central dependency. Mac fallback does not provide server disaster recovery, distributed task queues or horizontal scaling. The default deployment also has no automated off-site backup system; operators must protect and test restoration of their private state separately.
