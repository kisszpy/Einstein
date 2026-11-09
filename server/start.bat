@echo off
rem Start Einstein API server (backend) on http://127.0.0.1:8000
rem Double-click this file, or run it from a terminal. The window stays open with live logs.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Create it first:
  echo   python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)

echo [einstein] starting API on 127.0.0.1:8000 ...
start "einstein-api" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
