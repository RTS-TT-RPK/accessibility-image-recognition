@echo off
title DSH Plugin Setup - table-image
cd /d "%~dp0"
python dsh_plugin_setup.py %*
echo.
pause
