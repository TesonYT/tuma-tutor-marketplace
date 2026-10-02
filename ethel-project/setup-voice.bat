@echo off
REM Ethel - optional voice setup.
REM
REM Ethel's core needs no packages at all: enrolment, placement, teaching,
REM retrieval and the four grounding gates are standard library only, and that
REM is deliberate - a machine with no network is the worst place to discover a
REM wheel is missing.
REM
REM Voice is the one exception. Piper is a neural TTS that cannot reasonably be
REM reimplemented, so it gets its own virtualenv here, inside Ethel, owned by
REM Ethel. Nothing outside this folder is touched, and if you delete .venv the
REM rest of the app carries on exactly as before - silently.
REM
REM This step needs the internet ONCE. After it, voice runs fully offline.

setlocal
cd /d "%~dp0"

echo Ethel - voice setup
echo.

set "PY=python"
where python >nul 2>&1 || set "PY=py -3"

if exist ".venv\Scripts\piper.exe" (
  echo Voice environment already present at .venv
  echo Re-run with --force to rebuild it.
  if /i not "%~1"=="--force" goto verify
  echo Rebuilding...
  rmdir /s /q .venv
)

echo Creating .venv ...
%PY% -m venv .venv || goto fail

echo Installing piper-tts ^(this downloads about 60 MB, once^) ...
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements-voice.txt || goto fail

:verify
echo.
echo Checking voice files in models\piper ...
if not exist "models\piper\en_GB-jenny_dioco-medium.onnx" (
  echo   female voice MISSING - downloading ...
  .venv\Scripts\python.exe -m piper.download_voices en_GB-jenny_dioco-medium --download-dir models\piper || goto fail
)
if not exist "models\piper\en_GB-alan-medium.onnx" (
  echo   male voice MISSING - downloading ...
  .venv\Scripts\python.exe -m piper.download_voices en_GB-alan-medium --download-dir models\piper || goto fail
)

echo.
echo Verifying ...
%PY% -c "import sys; sys.path.insert(0,'.'); from ethel import config, speech; s=speech.status(config.load()); print('  engine :', s['engine_path'] or 'NOT FOUND'); print('  english: female', s['voices']['en']['female'], '| male', s['voices']['en']['male']); sys.exit(0 if s['engine_found'] and s['voices']['en']['female'] and s['voices']['en']['male'] else 1)" || goto fail

echo.
echo Voice is ready. Ethel finds .venv on her own - no config needed.
echo Bemba and Lozi still have no voice; see README.
pause
exit /b 0

:fail
echo.
echo Voice setup FAILED. Ethel still runs - she just stays silent.
pause
exit /b 1
