#!/usr/bin/env python3
"""Install the current user's scorer agent after a private config has been provisioned."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys

APP = Path(__file__).resolve().parents[1]
LABEL = 'org.littlereader.scorer'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=APP / 'data/mac-scorer/config.json')
    args = parser.parse_args()
    config = args.config.resolve()
    if sys.platform != 'darwin' or not config.is_file() or config.stat().st_mode & 0o077:
        raise SystemExit('Requires macOS and an existing private (0600) worker config.')
    agent = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    agent.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'Label': LABEL,
        'ProgramArguments': [str(APP / '.venv-assessment/bin/python'),
                             str(APP / 'scripts/mac_assessment_worker.py'), '--config', str(config)],
        'WorkingDirectory': str(APP),
        'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin',
                                 'PYTHONUNBUFFERED': '1', 'RAZ_MODEL_THREADS': '4'},
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 15,
        'ProcessType': 'Standard', 'Nice': 5,
        'StandardOutPath': '/dev/null', 'StandardErrorPath': str(config.parent / 'launchd.log'),
        # No caffeinate: normal sleep is allowed and triggers cloud failover.
    }
    agent.write_bytes(plistlib.dumps(payload))
    agent.chmod(0o600)
    domain = f'gui/{os.getuid()}'
    subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], capture_output=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(agent)], check=True)
    print('Mac scorer installed for the current login session and future logins.')


if __name__ == '__main__':
    main()
