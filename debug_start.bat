@echo off
echo Starting Super Agent Party...
echo.

echo Step 1: Checking Python environment...
if exist .venv\Scripts\python.exe (
    echo Python environment found!
) else (
    echo ERROR: Python environment not found!
    pause
    exit /b 1
)

echo.
echo Step 2: Testing Python server...
.venv\Scripts\python.exe -c "import sys; print('Python version:', sys.version)"

echo.
echo Step 3: Checking server.py...
if exist server.py (
    echo server.py found!
) else (
    echo ERROR: server.py not found!
    pause
    exit /b 1
)

echo.
echo Step 4: Starting Python server...
.venv\Scripts\python.exe server.py --port 3456 --host 127.0.0.1

pause