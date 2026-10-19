# Security and private data

Use GitHub's **Security → Report a vulnerability** for sensitive reports. Do not post tokens, real recordings, account databases, private PDFs, or production logs in public issues. For an ordinary UI bug, reproduce it with the original demo and remove personal information from screenshots.

## Data boundaries

- Source is public; runtime state and your books are not part of the source package.
- Keep `data/`, `library/`, generated fixtures, model weights and secret files out of Git and outside the web root. The publication audit checks tracked files; `.gitignore` alone is not a secret scanner and does not remove existing Git history.
- Local mode is loopback-only. Cloud mode needs a trusted same-origin HTTPS proxy and private per-reader state. Never expose the backend directly or trust client-supplied proxy headers through an untrusted route.
- Account passwords use salted PBKDF2-HMAC-SHA256. Login tokens are checked by the backend; cookies use Secure, HttpOnly and SameSite. These measures are not a claim that recordings or SQLite databases are encrypted at rest.
- Accounts have separate progress, latest recordings and assessment results. Version and reader-scope checks reject stale operations after a new take or account switch.
- Recordings are validated and bounded; the API resolves book IDs through a manifest rather than accepting arbitrary user filesystem paths. Rate limits are in-process and are not a distributed abuse-prevention service.
- The optional Mac worker receives task audio and reference text over HTTPS with a separate credential. It opens no listening port. Temporary audio is removed after processing; private state remains on the server.

No independent penetration test, server disaster recovery, automatic encrypted backup or unlimited-concurrency guarantee is implied. Restrict server administration, manage backups outside the public document root and test restoration. Serving a book to unauthenticated readers makes that book publicly accessible even if progress and recordings are private.

## Before contributing or publishing

Run `python3 scripts/audit_public.py` on the staged/tracked snapshot, and use a dedicated secret scanner such as Gitleaks against the full history. Review image content separately: automated text scanners do not prove a screenshot is safe. If a credential is accidentally published, revoke/rotate it and clean history; deleting the current file alone is insufficient.
