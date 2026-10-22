# Source publication checks

## 2026-10-22 update

Synced recent application fixes into the separate public repository while retaining its local-only defaults, explicit library import and original examples. Production data/configuration and private repository history were not copied.

| Check | Result |
| --- | --- |
| Backend regression suite | 103 passed; 1 optional real-translation test skipped (model not installed) |
| Browser suite | 6 passed using installed Chrome and Playwright 1.58.2 |
| Main demo flow | `http://127.0.0.1:8876` → shelf/search → original PDF → save/resume → mobile picker |
| Reading interaction | PDF-focused Space starts/stops recording; held-key repeats ignored; feedback narration plays/pauses; arrow keys and next action close feedback and turn the page |
| Time-zone regression | New York, Shanghai and UTC devices retain the same morning count through device midnight and refresh, then reset at Beijing midnight |
| Page/console health | Correct RAZ Reader title, visible book content, no blank screen/framework overlay or application console errors |
| Responsive checks | Demo screenshots at 1365×900 and 390×844; feedback actions visible at 1280×720, 390×600 and 320×568 |
| Screenshot review | All five captures reviewed; original generated text/art only; no image metadata |
| Source/index audit | 545 indexed files; no prohibited materials, private paths, databases or credentials detected |
| Gitleaks 8.30.1 | No findings in the staged source snapshot or existing Git history |
| Documentation | Local links resolve; English and Chinese instructions updated |
| Test runtime | Actual local Node.js v24.22.1; CI pinned to the same security-patched LTS version |

Browser plugin not available; the existing Playwright workflow was used. Commands: `python3 -m unittest discover -s tests -p 'test_*.py'`, `PW_CHANNEL=chrome npm run test:e2e`, `python3 scripts/audit_public.py`, and redacted Gitleaks scans. Screenshots were first captured outside the repository, then reviewed and copied to `docs/screenshots/`.

The new browser interaction test uses a generated microphone payload and controlled assessment responses. Actual original-demo narration and PDF rendering run in the browser; backend tests exercise API boundaries and sync behavior with temporary data. These checks do not validate pronunciation accuracy, physical Android/WeChat behavior, or a new production deployment. No optional model was downloaded for this update; existing scoring model/formula are unchanged. Refer to GitHub Actions for the Linux result of the published commit.

## Initial release

Date: 2026-10-19. This is a source publication check, not a penetration audit or a claim of model accuracy.

## Scope

The public package was assembled into a separate directory with a fresh Git history. Private deployment history, production settings, commercial books/audio, generated book files, accounts, recordings, models, logs and credentials were not copied. Original third-party notices were retained; no upstream PDF test corpus was included.

## Validation

| Check | Result |
| --- | --- |
| Fresh basic bootstrap on macOS | Passed; generated six original example books |
| Core backend regression suite | 67 passed; 1 optional real-translation test skipped because its model was absent |
| Explicit import regression suite | 2 passed; source integrity/progress preservation and source-directory protection |
| Browser flow | 2 passed: desktop 1365×900 and mobile 390×844 |
| Browser checks | Correct title/content, real PDF rendering, search, saved page restoration, picker interaction; no application console errors |
| Screenshot review | Only original example art/text; all five PNGs have no metadata entries |
| Documentation links | Local targets resolve |
| Tracked-file publication audit | No prohibited runtime/media/model/credential files or personal filesystem paths detected |
| Gitleaks 8.30.1 source snapshot scan | No findings |

Browser plugin not available; regular Playwright with installed Chrome was used. Screenshots are in `docs/screenshots/`. Source-only unit tests use controlled substitutes where appropriate and do not imply that optional model installation, real microphone recognition or long-recording accuracy was revalidated for every platform.

The generic HTTPS deployment template and Mac installation instructions require operator configuration. This release preparation did not change the private production deployment, install another scoring model or reopen a Mac network port. Physical Android/WeChat devices, full English UI localization and Windows setup are outside this release's validation.

GitHub Actions also runs the backend and original-demo browser suites on Linux. Its workflow results are the authority for that environment; consult the repository's Actions tab for the current commit.
