@echo off
REM Prop Desk Arena launcher for Windows. Double-click it, or let Task Scheduler start it.
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. Follow prop_desk\SETUP.md step 3 first.
  pause
  exit /b 1
)
echo Starting Prop Desk Arena - open http://localhost:8000 in your browser.
echo Keep this window open. Close it to stop the Arena.
:loop
".venv\Scripts\python.exe" -m prop_desk.server >> "prop_desk\arena.log" 2>&1
echo The Arena stopped unexpectedly. Restarting in 30 seconds... (see prop_desk\arena.log)
timeout /t 30 /nobreak > nul
goto loop
