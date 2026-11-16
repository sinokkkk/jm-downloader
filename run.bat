@echo off
setlocal
cd /d "%~dp0"

rem Prefer local .venv, fallback to parent directory
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%~dp0..\.venv\Scripts\python.exe"

if not exist "%PY%" goto no_venv

"%PY%" "%~dp0main.py"
if errorlevel 1 pause
exit /b 0

:no_venv
echo [ERROR] Python venv not found.
echo.
echo Create one first:
echo   python -m venv .venv
echo   .venv\Scripts\activate
echo   pip install -r requirements.txt
echo.
pause
exit /b 1
