@echo off
rem Double-click to start the RSVP Reader and open it in your browser.
rem The reader runs in the background, so this window closes by itself.
rem To stop the reader, click Quit in the library page.
title Starting RSVP Reader...
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo.
  echo uv isn't installed yet, or Windows can't find it.
  echo Install it by running this in PowerShell, then double-click this file again:
  echo     winget install --id astral-sh.uv -e
  echo.
  pause
  exit /b 1
)

echo Starting RSVP Reader... ^(the first start downloads what it needs and takes a few minutes^)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rsvp.ps1" start
if errorlevel 1 (
  echo.
  echo The reader couldn't start ^(see above^).
  pause
)
