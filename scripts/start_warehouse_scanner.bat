@echo off
cd /d "%~dp0.."
start "" "%CD%\venv\Scripts\pythonw.exe" "%CD%\scripts\warehouse_scan_desktop.py"
