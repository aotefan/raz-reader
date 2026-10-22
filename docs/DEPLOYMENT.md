# HTTPS deployment and optional Mac scoring

[中文](DEPLOYMENT.zh-CN.md) · [README](../README.md)

Use a domain you control and a server you manage. All domains below are placeholders. This guide does not connect to or alter the original author's service.

## 1. Prepare the server

On Linux, install the prerequisites in the README and place a clean clone at `/srv/raz-reader`. Run `./scripts/bootstrap.sh` as the dedicated service user. Install the optional language/scoring environments if needed. The scoring environment requires Python 3.11 and enough disk/RAM for PyTorch and the model; it is not included in the basic demo.

To use your books, import them **on that server** with `scripts/import_library.py --root ...`. Manifests contain absolute local paths; copying a manifest from another machine without rebuilding it will not relocate its sources. Books and any generated media must be readable by the service user.

Create a separate private state directory owned by that user:

```bash
mkdir -p data/cloud-state
chmod 700 data/cloud-state
python3 -m engine.server \
  --data data/cloud-state \
  --manifest data/manifest.json \
  --port 8765 --listen 127.0.0.1 \
  --cloud-origin https://reader.example.com
```

Replace `reader.example.com` with your domain. Run the command under your process manager for persistent operation; the foreground example stops when its terminal exits. Model and dictionary locations remain in the project's `data/`; `--data` selects private account/recording state, not the model directory.

## 2. Put Caddy in front

Point your domain's DNS to the server and make ports 80/443 reachable. Adapt [deploy/Caddyfile.example](../deploy/Caddyfile.example) to your domain and clone location. The web root must be `/srv/raz-reader/web`, **not** `/srv/raz-reader` or its `data` directory.

Caddy serves the static app, forwards `/api/*` to loopback, preserves the public Host/Origin and passes the real client address. Valid HTTPS is needed for secure account cookies and microphone access. Do not expose port 8765 directly to the internet. `https://reader.example.com/api/health` should report `cloud_full`; then test registration, recording and a second browser's account isolation.

This is a generic configuration, not an automatic production deployment or a completed security audit. Back up private state and media separately; a source-code archive does not back them up. Only make books available if you have permission to serve them.

## 3. Optional Mac acceleration

First install and verify the **server scorer**. Fallback cannot work if the server model is missing. On the Mac, clone this repository and run `./scripts/setup_assessment.sh`; FFmpeg and eSpeak NG are required. The Mac must reach the same HTTPS domain on port 443.

Generate the dedicated shared credential on the server without printing it:

```bash
python3 - <<'PY'
from pathlib import Path
import os, secrets
p = Path('data/cloud-state/assessment-worker.key')
with p.open('x', opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
    f.write(secrets.token_hex(32) + '\n')
PY
```

The exclusive create fails if a key already exists; do not overwrite a working credential unintentionally. Restart the server after creating it. Transfer this one file to the Mac's ignored `data/mac-scorer/assessment-worker.key` through your existing secure administration channel. Never paste it into an issue, commit, screenshot or public URL.

On the Mac, create the private worker config from that file:

```bash
python3 - <<'PY'
from pathlib import Path
import json, os
d = Path('data/mac-scorer')
d.mkdir(parents=True, exist_ok=True)
d.chmod(0o700)
key = d / 'assessment-worker.key'
key.chmod(0o600)
payload = {'origin': 'https://reader.example.com', 'token': key.read_text().strip()}
with (d / 'config.json').open('x', opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
    json.dump(payload, f)
PY
python3 scripts/install_mac_scorer.py
```

The launchd label is `org.razreader.scorer`. It starts at user login, restarts on failure, and permits normal sleep. It does not open a Mac port or require a tunnel. Check private local logs under `data/mac-scorer/`; do not publish those files.

Stop acceleration while leaving cloud scoring available:

```bash
launchctl bootout gui/$(id -u)/org.razreader.scorer
```

Restore it:

```bash
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/org.razreader.scorer.plist"
```

An absent/busy Mac goes directly to the server; an interrupted assigned job falls back after its heartbeat lease expires. A total deadline also bounds a connected-but-stuck worker. Test both paths with your own short recording before relying on this setup. The server remains necessary even while the Mac is online.

## 4. Low-memory tuning

The source includes an optional compact-model exporter and parity checker. They preserve the existing model's scoring method, but need to be validated on your installed model/runtime and recordings. Inspect their `--help` and [architecture notes](ARCHITECTURE.md); do not assume the private deployment's memory limits are appropriate for your host. No production Compose file, IP address, resource path or machine credential is shipped in this release.

## 5. Read on localhost and synchronize to your own cloud

Update both installations to the same source version. Import the same original PDF editions separately on the local machine and server; copying manifests between machines is not supported. Install the local scoring model using `./scripts/setup_assessment.sh`. Then start the local reader with your own HTTPS origin:

```bash
RAZ_SYNC_ORIGIN=https://reader.example.com ./scripts/start.sh
```

Open `http://127.0.0.1:8765` and sign in to the account used on your deployment. PDFs and scoring stay local; account progress, completion days, recordings and saved results synchronize with that deployment. The service remains bound to loopback. Initial sign-in requires connectivity; pending writes are retained for retries after a network interruption. Do not clear the private `data/local-sync/` state while uploads are pending. The personal center displays synchronization status.

On a phone, open your HTTPS deployment and use the same account. Recordings/results synchronize for matching PDF editions, each page keeping the latest take. Synchronization transfers private audio and results; it does not upload your book library. The local bridge is independent of the Mac task worker and does not need its worker key. Remove `RAZ_SYNC_ORIGIN` to return to the separate local-only profile. Nothing is connected by default.

To precompute references for imported pages before practice, optionally run:

```bash
python3 scripts/prepare_reading_text.py --data data
```

The default range is AA–H; use `--levels` for other grades or `--manifest` if the manifest is elsewhere. Cache and review output stay under the private data directory. Uncertain pages remain uncertain; this command does not guess text or modify source PDFs.
