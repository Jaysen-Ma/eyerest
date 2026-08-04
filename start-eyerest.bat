@echo off
title EyeRest
cd /d "%~dp0"
python "%~dp0eyerest.py" %*
if errorlevel 1 pause
