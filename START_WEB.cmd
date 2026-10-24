@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>nul
  if errorlevel 1 (python -m venv .venv) else (py -3.12 -m venv .venv)
  if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
echo.
echo Open http://127.0.0.1:8000 in your browser.
echo Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -X utf8 web_app.py
if errorlevel 1 goto :failed
exit /b 0
:failed
echo Setup failed. Install Python 3.12 and check your internet connection.
pause
exit /b 1
