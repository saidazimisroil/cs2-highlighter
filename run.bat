@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Setting up Python environment...
  python -m venv .venv || goto :fail
  .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :fail
)
start "" http://127.0.0.1:8765
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
goto :eof

:fail
echo Setup failed. Is Python 3.12+ installed and on PATH?
pause
