@echo off
rem Double-click to start Live Translator. First run installs everything (~5 min).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo First run: creating environment and downloading models...
    py -3 -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
    ".venv\Scripts\python.exe" -m livetranslator.download_models || goto :error
)
rem The guard starts the app and, however it ends (even a crash), puts the
rem real microphone and app volumes back.
start "" ".venv\Scripts\pythonw.exe" -W ignore -m livetranslator.guard run
exit /b 0

:error
echo.
echo Setup failed. See the message above.
pause
