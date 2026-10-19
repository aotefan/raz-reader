# Initial public source release checks

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
