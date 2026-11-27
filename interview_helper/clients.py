"""Launch and discover the installed desktop clients."""
import json
import os
from pathlib import Path
import uuid

from .paths import RUNTIME


CODEX_EXECUTABLE='/usr/lib/chatgpt/ChatGPT'
CLAUDE_EXECUTABLE='/usr/lib/claude-desktop/claude-desktop'


def codex_window_command(profile=None):
    config=RUNTIME/'codex-interview/thread.json'
    thread_id=uuid.UUID(json.loads(config.read_text())['threadId'])
    command=['/usr/bin/chatgpt','--force-renderer-accessibility']
    if profile:
        # Chromium checks its profile lock before the application handles the
        # deep link. The application also needs its own matching profile path.
        command.extend(['--ozone-platform=x11',f'--user-data-dir={profile}'])
    return [*command,f'codex://threads/{thread_id}']


def codex_environment(profile=None):
    env=os.environ.copy()
    env.pop('ELECTRON_RUN_AS_NODE',None)
    env.pop('CODEX_ELECTRON_USER_DATA_PATH',None)
    if profile:
        env['CODEX_ELECTRON_USER_DATA_PATH']=str(profile)
    return env


def find_native_codex():
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            if str((proc/'exe').resolve())!=CODEX_EXECUTABLE:continue
            if any(arg.startswith(b'--type=') for arg in (proc/'cmdline').read_bytes().split(b'\0')):continue
            if any(entry.startswith(b'CODEX_ELECTRON_USER_DATA_PATH=')
                   for entry in (proc/'environ').read_bytes().split(b'\0')):continue
            return int(proc.name)
        except (FileNotFoundError,PermissionError,ProcessLookupError):
            continue
    return None


class ExistingClaude:
    def __init__(self,pid,executable=CLAUDE_EXECUTABLE):
        self.pid=pid
        self.executable=executable
    def poll(self):
        try:return None if str(Path(f'/proc/{self.pid}/exe').resolve())==self.executable else 0
        except (OSError,ProcessLookupError):return 0
