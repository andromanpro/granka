@echo off
cd /d "%~dp0"
py -3 -B launch-granka.py
if errorlevel 1 (
  echo.
  echo Granka could not start. Check Python and the application log.
  pause
)
