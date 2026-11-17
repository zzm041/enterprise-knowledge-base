@echo off
chcp 65001 >nul
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo [EKB] .venv not found. Run:  uv sync --frozen
  pause
  exit /b 1
)

netstat -ano | findstr /C:":8809 " | findstr /C:"LISTENING" >nul
if not errorlevel 1 (
  echo [EKB] port 8809 is already in use; stop the other public window first.
  pause
  exit /b 1
)

echo [EKB] ollama: 4 slots (OLLAMA_NUM_PARALLEL=4); restarted only if it was running default
".venv\Scripts\python.exe" scripts\ensure_ollama.py
if errorlevel 1 echo [EKB] ollama is not ready; the window will still open, answers may fail

echo [EKB] PUBLIC read-only demo on http://127.0.0.1:8809/  pass phrase: EKBDEMO
echo [EKB] visitors cannot write, cannot switch identity, 30 requests/minute per IP.
echo [EKB] expose it only through a tunnel or reverse proxy you control.
echo.

".venv\Scripts\python.exe" scripts\console.py --port 8809 --public --public-token EKBDEMO --data-dir runtime\console-public --demo-password ekb-demo-2026 %*
pause
