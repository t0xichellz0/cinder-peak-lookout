@echo off
rem Creates a "Cinder Peak Lookout" icon on your desktop and in the Start menu.
cd /d "%~dp0"
for /f "delims=" %%P in ('py -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%P"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_shortcut.ps1" -Py "%PYW%" -Dir "%~dp0."
echo.
pause
