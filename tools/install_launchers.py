#!/usr/bin/env python3
"""Install distinct launchers for classic and virtual-display helper modes."""
from pathlib import Path
import sys

from interview_helper.paths import ROOT

applications=Path.home()/'.local/share/applications'
applications.mkdir(parents=True,exist_ok=True)


def install(filename,entry,title,description,backend='claude'):
    script=str(ROOT/entry).replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$').replace('%','%%')
    target=applications/filename
    codex=backend=='codex'
    flags=' --codex' if codex else ''
    icon='/usr/lib/chatgpt/resources/icon-chatgpt.png' if codex else '/usr/lib/claude-desktop/resources/icon.png'
    target.write_text(f'''[Desktop Entry]
Type=Application
Name={title}
Comment={description}
Exec=/usr/bin/python3 "{script}" launch{flags}
Icon={icon}
Terminal=false
Categories=X-PlasmaTools;
StartupNotify=true
Actions=Stop;

[Desktop Action Stop]
Name=Выключить этот режим
Exec=/usr/bin/python3 "{script}" stop{flags}
''')
    print(target)


if '--codex-only' not in sys.argv:
    install('org.interviewhelper.ClaudeInterview.desktop','controller.py',
            'Claude — собес (обычный)','Окно Claude прямо на рабочем столе Plasma')
    install('org.interviewhelper.ClaudeInterviewWorkspace.desktop','workspace.py',
            'Claude — собес (TigerVNC)','Claude на отдельном виртуальном экране с локальным окном просмотра')
install('org.interviewhelper.CodexInterview.desktop','controller.py',
        'Codex — собес (обычный)','Чат Codex для подготовки к собеседованиям',backend='codex')
install('org.interviewhelper.CodexInterviewWorkspace.desktop','workspace.py',
        'Codex — собес (TigerVNC)','Чат Codex на отдельном виртуальном экране',backend='codex')
