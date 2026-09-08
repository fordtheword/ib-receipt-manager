@echo off
rem Starts the Receipt Manager server fully hidden (no console window).
rem A visible console can be put into QuickEdit "select" mode by a stray
rem click, which freezes uvicorn's output and hangs the whole app.
rem Output goes to uvicorn.out.log / uvicorn.err.log in this folder instead.
cd /d "%~dp0"
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%~dp0venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','app:app','--port','8000' -WorkingDirectory '%~dp0' -WindowStyle Hidden -RedirectStandardOutput '%~dp0uvicorn.out.log' -RedirectStandardError '%~dp0uvicorn.err.log'"
