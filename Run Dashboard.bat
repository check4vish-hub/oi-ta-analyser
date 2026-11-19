@echo off
cd /d "%~dp0"
echo Running F^&O scanner on the symbols in watchlist.txt ...
echo.
where python >nul 2>nul
if %errorlevel%==0 (python dashboard.py) else (py dashboard.py)
echo.
pause
