@echo off
chcp 65001 >nul
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo [EKB] .venv not found. Run:  uv sync --frozen
  pause
  exit /b 1
)

netstat -ano | findstr /C:":8810 " | findstr /C:"LISTENING" >nul
if not errorlevel 1 (
  echo [EKB] port 8810 is already in use.
  echo [EKB] close the other demo window first, or run:  scripts\console.py --port 8811
  pause
  exit /b 1
)

echo [EKB] ollama: 4 slots (OLLAMA_NUM_PARALLEL=4); restarted only if it was running default
".venv\Scripts\python.exe" scripts\ensure_ollama.py
if errorlevel 1 echo [EKB] ollama is not ready; the window will still open, answers may fail

echo [EKB] http://127.0.0.1:8810/   account: editor / reader / staff / other-reader
echo [EKB] demo password: ekb-demo-2026
echo [EKB] data dir: runtime\console-live  (documents survive restarts)
echo [EKB] close this window to stop all four processes.
echo.

".venv\Scripts\python.exe" scripts\console.py --port 8810 --data-dir runtime\console-live --demo-password ekb-demo-2026 %*
pause
