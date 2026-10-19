# Contributing

Use the original demo to reproduce changes. Keep PRs focused and include the behavior changed, why it matters, and relevant validation.

```bash
./scripts/bootstrap.sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
npm ci --ignore-scripts
npx playwright install chromium
npm run test:e2e
python3 scripts/audit_public.py
```

Optional real-model tests may skip when their models are not installed. Do not replace those checks with claims of real inference. No commercial library is required for the default test suite.

## Publication rules

- Do not contribute RAZ or other proprietary PDFs, covers, page images, transcripts, or audio.
- Do not contribute real user recordings, progress, account/session databases, model downloads, cookies, logs, tokens, production configuration, or personal filesystem paths.
- Use generated original examples for screenshots. Review every image and its metadata; adding screenshots under `docs/screenshots/` is an explicit publication decision.
- Keep original upstream notices intact. Dependencies and optional datasets retain their own licenses.
- Changes to scoring must preserve real evidence and version/account isolation. Do not inflate scores to hide OCR, recording or recognition failures.
- Avoid introducing new runtime network calls or paid APIs without documenting them.

To deliberately refresh the README images using only the original demo:

```bash
PW_CHANNEL=chrome SCREENSHOT_DIR=docs/screenshots npm run test:e2e
```

Omit `PW_CHANNEL=chrome` to use Playwright's installed Chromium. Screenshot generation is opt-in; ordinary test runs do not overwrite documentation images.
