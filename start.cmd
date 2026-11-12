@echo off
rem Start the service without Docker: double-click this file.
rem First run creates .venv and installs dependencies, then starts the server
rem and opens http://localhost:8000. To stop: close this window or press Ctrl+C.
rem Keep this file ASCII-only: cmd misreads UTF-8 Cyrillic in scripts.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First run: creating .venv ...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo Python 3.11+ not found. Install it from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo Installing dependencies, this takes about a minute ...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 (
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
)

rem Open the browser in 3 seconds, when the server is already up
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8000"

echo Service: http://localhost:8000   (to stop - close this window)
".venv\Scripts\python.exe" -m uvicorn app.main:app --port 8000
if errorlevel 1 (
    echo.
    echo Server did not start. If port 8000 is busy, close the other service window.
    pause
)
