@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\model-workbench.ps1" start -OpenWorkbench
if errorlevel 1 pause
