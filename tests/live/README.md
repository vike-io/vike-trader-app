# tests/live — manual LIVE-display verification drivers

These are **not pytest tests** (deliberately not named `test_*.py`, so nothing here is ever
collected by `pytest`, locally or in CI). They drive the **real app on the real display** —
launching an actual `MainWindow`, clicking through shipped features, reading back objective
state (geometry, Win32 style bits like `WS_EX_TOPMOST`), and saving staged screenshots.

Use them before claiming a UI feature works: offscreen GUI tests prove logic, these prove the
pixels (per the project rule: live-verify implemented features before reporting done).

## Running

From the repo root, on a machine with a display (NOT offscreen):

```powershell
.venv\Scripts\python.exe tests\live\verify_titlebar.py
```

All output (screenshots, logs) goes to the OS tmp dir (`%TEMP%\vike-shots\`) — never into the
repo. Scripts set `VIKE_DISABLE_SESSION=1` / `VIKE_DISABLE_LIVE=1` where needed so they don't
touch your saved session or hit live feeds. **Rule for new scripts: never write persistent app
state (QSettings, storage/) — sandbox anything you toggle.**

| Script | Verifies |
|---|---|
| `verify_s7_floating_windows.py` | S7: floating chart windows (title bars, arrange grid/cascade, roll-up, maximize, detach + topmost pin, link dots in toolbar, command routing) |
| `verify_titlebar.py` | S6: frameless merged title bar (one caption row, working min/max/close) |
| `verify_shell.py` | Shell: top command bar, hamburger menu, launchers |
| `verify_shell_recheck.py` | Shell regression sweep (menus, title bar) |
| `verify_style_icons.py` | Chart-type icon dropdown + favorites star |
| `verify_arrange.py` | Arrange grid + keep-on-top pin (reads `WS_EX_TOPMOST`) |
| `verify_linkmenu.py` | Link-dot dropdown (symbol/interval color channels) |
| `verify_tiles.py` | News tiles layout |
