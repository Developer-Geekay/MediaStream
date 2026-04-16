@echo off
REM MediaStream setup for Windows
REM Run this script once to set up the environment, then use run.bat to start.

echo === MediaStream Setup ===

REM Check Python
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo ERROR: Python not found. Install Python 3.11+ from https://python.org
    pause
    exit /b 1
)

REM Create virtual environment
IF NOT EXIST ".venv" (
    python -m venv .venv
    echo Virtual environment created.
)

call .venv\Scripts\activate.bat

REM Install dependencies
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q
echo Python dependencies installed.

REM Create directories
IF NOT EXIST "media\movies" mkdir media\movies
IF NOT EXIST "media\music" mkdir media\music
IF NOT EXIST "media\photos" mkdir media\photos
IF NOT EXIST "config" mkdir config
IF NOT EXIST "certs" mkdir certs

echo.
echo === Setup complete! ===
echo.
echo To start MediaStream, run:
echo   run.bat
echo.
echo Default login: admin / admin1234  ^<^<^< CHANGE THIS IMMEDIATELY
echo.
echo NOTE: SMB uses port 4450 (not 445) to avoid conflicts with Windows built-in SMB.
echo       To connect: Map Network Drive ^> Folder: \\^<server-ip^>:4450\MEDIAFILES
echo       Or open File Explorer and type: \\^<server-ip^>:4450
echo.
echo NOTE: DLNA/SSDP (port 1900) may need Windows Firewall exception.
echo       Add it via: Windows Defender Firewall ^> Allow an app through firewall
echo.
pause
