"""Locations shared by the runtime, setup tools, and tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / 'runtime'
ELECTRON = ROOT.parent / 'meetingly-review/app/node_modules/electron/dist/electron'
KWIN_SCRIPT = ROOT / 'interview_helper/desktop/kwin.js'
ELECTRON_BRIDGE = ROOT / 'interview_helper/speech/bridge.cjs'
FIXTURE = ROOT / 'tests/fixtures/fixture.cjs'
