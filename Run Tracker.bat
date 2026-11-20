@echo off
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (python tracker.py) else (py tracker.py)
echo.
pause
