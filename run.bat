@echo off
REM MediaStream launcher for Windows
IF NOT EXIST ".venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found. Please run setup.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
python run.py
pause
