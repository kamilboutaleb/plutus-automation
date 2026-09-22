@echo off
setlocal
cd /d "%~dp0"
title VC Workbook Enrichment

echo VC Workbook Enrichment
echo ======================

where py >nul 2>&1
if not errorlevel 1 goto use_py

where python >nul 2>&1
if not errorlevel 1 goto use_python

echo Python 3 is missing. Install it from https://www.python.org/downloads/
echo During installation, select "Add python.exe to PATH".
echo Then double-click run.bat again.
set "EXIT_CODE=1"
goto finish

:use_py
set "PYTHON_CMD=py -3"
goto python_ready

:use_python
set "PYTHON_CMD=python"

:python_ready
if exist ".venv\Scripts\python.exe" goto install_packages

echo Setting up Python for the first time...
%PYTHON_CMD% -m venv .venv
if errorlevel 1 goto setup_failed

:install_packages
echo Checking required packages (internet may be needed the first time)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto setup_failed

".venv\Scripts\python.exe" run.py
set "EXIT_CODE=%ERRORLEVEL%"
goto finish

:setup_failed
echo.
echo Setup failed. Check your internet connection and Python installation, then try again.
set "EXIT_CODE=1"

:finish
echo.
echo Press any key to close this window...
pause >nul
exit /b %EXIT_CODE%
