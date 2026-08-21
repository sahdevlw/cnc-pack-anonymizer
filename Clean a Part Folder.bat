@echo off
rem CNC Pack Anonymizer launcher (Windows).
rem Double-click  -> opens the simple 3-step window.
rem Or DRAG a part folder onto this file -> asks for a name and cleans it.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)

if "%~1"=="" (
    %PY% anonymize_pack.py
    goto :eof
)
set /p PNAME="Neutral part name for this folder (e.g. housing-01): "
%PY% anonymize_pack.py "%~1" --name "%PNAME%"
echo.
pause
