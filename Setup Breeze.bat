@echo off
cd /d "%~dp0"
set PY=python
where python >nul 2>nul
if not %errorlevel%==0 set PY=py
echo Installing the ICICI Breeze library...
%PY% -m pip install -q breeze-connect
%PY% breeze_setup.py
echo.
pause
