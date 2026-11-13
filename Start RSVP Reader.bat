@echo off
rem Double-click to start the RSVP Reader and open it in your browser.
rem Keep this window open while you read; closing it stops the reader.
title RSVP Reader - keep this window open while you read
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

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rsvp.ps1" serve --open
if errorlevel 1 (
  echo.
  echo The reader stopped with an error ^(see above^).
  pause
)
