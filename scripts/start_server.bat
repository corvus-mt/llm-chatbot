@echo off
setlocal

set "ROOT=%~dp0.."
set "UVICORN=%ROOT%\.venv\Scripts\uvicorn.exe"

if not exist "%UVICORN%" (
  echo uvicorn not found at %UVICORN%. Create the venv and install requirements first.
  exit /b 1
)

cd /d "%ROOT%"
call "%UVICORN%" app.main:app --reload --host 0.0.0.0 --port 8000
