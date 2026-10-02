@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY="
for %%C in ("py -3" "python" "python3") do (
  if not defined PY (
    %%~C -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
    if !errorlevel! equ 0 set "PY=%%~C"
  )
)
if not defined PY (
  echo.
  echo   Python 3.10 or newer was not found on this machine.
  echo   Install it from the offline installer supplied with this machine,
  echo   or from python.org on a machine that has internet, then run this again.
  echo.
  pause
  exit /b 1
)

if "%~1"=="--doctor"   ( %PY% -m ethel --doctor & pause & exit /b 0 )
if "%~1"=="--validate" ( %PY% -m ethel --validate & pause & exit /b 0 )
if "%~1"=="--selftest" ( %PY% tools\selftest.py & pause & exit /b 0 )
if "%~1"=="--modelcheck" ( %PY% tools\modelcheck.py & pause & exit /b 0 )
if "%~1"=="--listencheck" ( %PY% tools\listencheck.py %2 & pause & exit /b 0 )
if "%~1"=="--package" ( %PY% tools\package.py %2 %3 & pause & exit /b 0 )

start "" http://127.0.0.1:8770/
%PY% -m ethel %*
pause
