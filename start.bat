@echo off
title Cinder Peak Lookout
cd /d "%~dp0"
echo Checking the Gemini library...
py -m pip install --quiet --disable-pip-version-check google-genai
py updater.py
py server.py
pause
