@echo off
REM Daily run for Windows Task Scheduler. Uses the project's virtualenv if present.
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python main.py %* >> outputs\daily.log 2>&1
