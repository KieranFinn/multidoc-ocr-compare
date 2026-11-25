@echo off
rem Windows entry point for the desktop workbench (gui.py).
rem Keep this file pure ASCII: a .bat that mixes non-ASCII text with "chcp"
rem makes cmd mis-seek and silently skip later lines.
cd /d "%~dp0"
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw gui.py
  exit /b 0
)
echo [i] pythonw not found - falling back to python, console stays open.
python gui.py
if errorlevel 1 (
  echo.
  echo [X] gui.py failed to start. See the traceback above.
  pause
)
