@echo off
if exist "%~dp0windows\Azurik Level Studio.exe" (
    start "" "%~dp0windows\Azurik Level Studio.exe" %*
    exit /b
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0Launch.ps1"
