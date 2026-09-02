@echo off
REM ================================================================
REM  HireB - local launcher
REM  Double-click this file to start the website on your PC.
REM ================================================================
cd /d "%~dp0"

REM --- First run: create the virtual environment if it's missing ---
if not exist ".venv\Scripts\python.exe" (
    echo First run detected. Creating virtual environment and installing packages...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

REM --- Stop any old server still holding port 8000 (prevents a stale
REM     SQLite server from serving old data after config changes) ---
echo Freeing port 8000 if an old server is running...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do taskkill /F /PID %%p >nul 2>&1

REM --- Apply any pending database changes ---
".venv\Scripts\python.exe" manage.py migrate

echo.
echo ================================================================
echo   HireB is starting...
echo   Open in your browser:  http://localhost:8000/
echo   (Choose HR / Admin login)
echo.
echo   Leave this window OPEN while you use the site.
echo   Close this window (or press Ctrl+C) to stop the server.
echo ================================================================
echo.

REM --- Open the browser (HR login page) automatically after a short delay ---
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8000/"

REM --- Run the server (0.0.0.0 = also reachable from other devices on your network) ---
".venv\Scripts\python.exe" manage.py runserver 0.0.0.0:8000

REM --- Keep the window open if the server stops or an error occurs ---
echo.
echo The server has stopped. Press any key to close this window.
pause >nul
