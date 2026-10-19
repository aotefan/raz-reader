"""Fail closed on private/runtime assets in the Git index. Complements a secret scanner."""
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
BLOCKED_DIRS={'data','library','fixtures/generated','.secrets','secrets','backups','releases',
              '.venv','node_modules','test-results','playwright-report','evidence','upstream'}
BLOCKED_SUFFIXES={'.pdf','.mp3','.wav','.ogg','.webm','.m4a','.mp4','.aiff','.sqlite','.sqlite3',
                  '.db','.key','.pem','.p12','.pfx','.env','.pt','.pth','.safetensors','.bin','.zip','.gz','.log'}
TEXT_SUFFIXES={'.py','.js','.cjs','.mjs','.json','.md','.txt','.html','.css','.sh','.yaml','.yml','.toml','.svg'}


def audit():
    files=subprocess.check_output(['git','ls-files','--stage','-z'],cwd=ROOT).decode().split('\0')
    errors=[];count=0;size=0
    for entry in files:
        if not entry: continue
        metadata,name=entry.split('\t',1);mode,oid,stage=metadata.split()
        path=PurePosixPath(name);parts=path.parts
        if mode not in ('100644','100755'):
            errors.append((name,'symlink, submodule or unsupported file mode'));continue
        if any(name==prefix or name.startswith(prefix+'/') for prefix in BLOCKED_DIRS):
            errors.append((name,'runtime/private directory'))
        if path.suffix.lower() in BLOCKED_SUFFIXES or (path.name.startswith('.env') and path.name!='.env.example'):
            errors.append((name,'private/media/model extension'))
        blob=subprocess.check_output(['git','cat-file','blob',oid],cwd=ROOT)
        count+=1;size+=len(blob)
        if blob.startswith((b'%PDF-',b'SQLite format 3\x00',b'RIFF',b'ID3')):
            errors.append((name,'book/database/audio file signature'))
        if path.suffix.lower() in {'.png','.jpg','.jpeg'} and not name.startswith('docs/screenshots/'):
            errors.append((name,'image outside reviewed screenshot directory'))
        if len(blob)>15*1024*1024:
            errors.append((name,'unexpected large file'))
        if path.suffix in TEXT_SUFFIXES or path.name in {'LICENSE','.gitignore','.dockerignore','Caddyfile'}:
            text=blob.decode('utf-8',errors='replace')
            # Match actual values, not code that reads a secret or names an env var.
            patterns=[r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                      r'gh[pousr]_[A-Za-z0-9]{30,}',r'github_pat_[A-Za-z0-9_]{40,}',
                      r'AKIA[0-9A-Z]{16}',r'LTAI[0-9A-Za-z]{16,}',
                      r'/Users/[A-Za-z0-9_.-]+/',r'/(?:home|root)/[A-Za-z0-9_.-]+/\.ssh/']
            if any(re.search(pattern,text) for pattern in patterns):
                errors.append((name,'credential or personal filesystem path pattern'))
    print(json.dumps({'tracked_files':count,'bytes':size,'findings':errors},indent=2))
    return 1 if errors else 0


if __name__=='__main__': sys.exit(audit())
