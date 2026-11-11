#!/usr/bin/env python3
"""Generate the Plasma launcher for the current checkout location."""
from pathlib import Path

root = Path(__file__).resolve().parent
target = Path.home() / '.local/share/applications/org.interviewhelper.ClaudeInterview.desktop'
target.parent.mkdir(parents=True, exist_ok=True)
# Desktop Entry quoting differs from shell quoting; quoted paths support spaces.
script = str(root / 'controller.py').replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
target.write_text(f'''[Desktop Entry]
Type=Application
Name=Claude — собеседование
Comment=Голос, скриншоты и горячие клавиши для Claude Desktop в Plasma
Exec=/usr/bin/python3 "{script}" launch
Icon=/usr/lib/claude-desktop/resources/icon.png
Terminal=false
Categories=Utility;
StartupNotify=true
Actions=Stop;

[Desktop Action Stop]
Name=Выключить режим собеседования
Exec=/usr/bin/python3 "{script}" stop
''')
print(target)
