@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
start /min python -m uvicorn app:app --port 8000
