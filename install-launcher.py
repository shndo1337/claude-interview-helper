#!/usr/bin/env python3
"""Install distinct launchers for classic and virtual-display helper modes."""
from pathlib import Path

root=Path(__file__).resolve().parent
applications=Path.home()/'.local/share/applications'
applications.mkdir(parents=True,exist_ok=True)


def install(filename,entry,title,description):
    script=str(root/entry).replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$').replace('%','%%')
    target=applications/filename
    target.write_text(f'''[Desktop Entry]
Type=Application
Name={title}
Comment={description}
Exec=/usr/bin/python3 "{script}" launch
Icon=/usr/lib/claude-desktop/resources/icon.png
Terminal=false
Categories=X-PlasmaTools;
StartupNotify=true
Actions=Stop;

[Desktop Action Stop]
Name=Выключить этот режим
Exec=/usr/bin/python3 "{script}" stop
''')
    print(target)


install('org.interviewhelper.ClaudeInterview.desktop','controller.py',
        'Claude — собес (обычный)','Окно Claude прямо на рабочем столе Plasma')
install('org.interviewhelper.ClaudeInterviewWorkspace.desktop','workspace.py',
        'Claude — собес (TigerVNC)','Claude на отдельном виртуальном экране с локальным окном просмотра')
