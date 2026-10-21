@echo off
rem G2 local demo (Windows): double-click. Add -NoN8n to run the dashboard only.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_demo.ps1" %*
