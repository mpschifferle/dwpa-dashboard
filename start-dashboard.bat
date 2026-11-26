@echo off
rem ---------------------------------------------------------------------------
rem  help_crack dashboard launcher (Windows)
rem
rem  Double-click to start it, or from a terminal:
rem      start-dashboard.bat                    (opens the browser)
rem      start-dashboard.bat --port 9000        (extra options go to run_dashboard.py)
rem      start-dashboard.bat --workdir D:\crack
rem
rem  Set HC_DASHBOARD_NO_BROWSER=1 to skip opening the browser.
rem ---------------------------------------------------------------------------
setlocal EnableExtensions
title help_crack dashboard

cd /d "%~dp0"
if errorlevel 1 (
    echo Could not enter "%~dp0".
    pause
    exit /b 1
)

rem ---------------------------------------------------------------- find Python
set "PY="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)" >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=python"
    )
)
if not defined PY goto :no_python

rem ------------------------------------------------------------------- Flask
%PY% -c "import flask" >nul 2>nul
if errorlevel 1 (
    echo Flask is not installed yet, installing dashboard dependencies...
    %PY% -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :no_flask
    echo.
)

rem --------------------------------------------------------------- launch it
set "BROWSER_ARG="
if not defined HC_DASHBOARD_NO_BROWSER set "BROWSER_ARG=--open"

echo Starting the help_crack dashboard from "%~dp0"
echo Close this window or press Ctrl-C to stop it (a running help_crack is stopped too).
echo.
%PY% run_dashboard.py %BROWSER_ARG% %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo The dashboard exited with code %RC%.
    echo If the port is already in use, close the other dashboard or pass --port 9000
    pause
)

endlocal & exit /b %RC%

:no_python
echo.
echo Python 3.8 or newer was not found.
echo Install it from https://www.python.org/downloads/ and tick
echo "Add python.exe to PATH" during setup, then run this file again.
echo.
pause
exit /b 1

:no_flask
echo.
echo Could not install the dashboard requirements.
echo Try manually, from this folder:
echo     %PY% -m pip install -r requirements.txt
echo.
pause
exit /b 1
