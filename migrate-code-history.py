#!/usr/bin/env python3
"""Import missing local Code metadata; keep original chats and existing target files.

Transcripts already live in the user's shared ~/.claude/projects. No cookies,
credentials, scheduled tasks, deleted markers, binaries or browser data are copied.
"""
import datetime
import json
import os
from pathlib import Path
import tempfile

ROOT=Path(__file__).resolve().parent
SOURCE=Path.home()/'.config/Claude'
TARGET=ROOT/'runtime/workspace/claude-profile'


def atomic_write(target, data):
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix='.history-import-',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as file:file.write(data)
        os.chmod(name,0o600)
        os.replace(name,target)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    lock=TARGET/'SingletonLock'
    if lock.is_symlink():
        try:
            pid=int(os.readlink(lock).rsplit('-',1)[1])
            os.kill(pid,0)
        except (ValueError,ProcessLookupError,FileNotFoundError):pass
        else:raise RuntimeError('Сначала останови отдельную сессию Claude; обычный профиль можно оставить открытым')
    source=SOURCE/'claude-code-sessions'
    if not source.is_dir():raise RuntimeError('Не найдена локальная история Code')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=ROOT/'runtime/workspace/history-import-backups'/stamp
    backup.mkdir(parents=True,mode=0o700)
    copied=skipped=0
    for file in sorted(source.rglob('local_*.json')):
        data=file.read_bytes()
        parsed=json.loads(data)
        if not isinstance(parsed,dict) or 'sessionId' not in parsed:
            raise RuntimeError('Некорректная запись истории: '+file.name)
        relative=file.relative_to(source)
        atomic_write(backup/'source'/'claude-code-sessions'/relative,data)
        target=TARGET/'claude-code-sessions'/relative
        if target.exists():skipped+=1;continue
        atomic_write(target,data);copied+=1
    extras=0
    for file in [*source.rglob('archived-sessions.idx'),SOURCE/'ccd-ids.json']:
        if not file.is_file():continue
        relative=file.relative_to(SOURCE)
        atomic_write(backup/'source'/relative,file.read_bytes())
        target=TARGET/relative
        if not target.exists():atomic_write(target,file.read_bytes());extras+=1
    report={'importedChats':copied,'keptExistingChats':skipped,'auxiliaryFiles':extras,
            'sourceUnchanged':True,'credentialsCopied':False,'scheduledTasksCopied':False}
    (backup/'manifest.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
