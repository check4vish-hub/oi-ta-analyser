@echo off
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (python dashboard.py) else (py dashboard.py)
if errorlevel 1 pause
