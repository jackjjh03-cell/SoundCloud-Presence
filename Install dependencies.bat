@echo off
setlocal
cd /d "%~dp0"
echo SoundCloud Presence - Minimum dependency installer
echo.
python -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if errorlevel 1 goto :python_error
python -c "import tkinter" >nul 2>&1
if errorlevel 1 goto :tk_error
if not exist "requirements.txt" goto :missing
python -m pip --version >nul 2>&1
if errorlevel 1 (
    python -m ensurepip --default-pip
    if errorlevel 1 goto :install_error
)
python -m pip install -r "requirements.txt"
if errorlevel 1 goto :install_error
python -c "from pypresence import Presence" 
if errorlevel 1 goto :install_error
echo.
echo Installation complete. Double-click SoundCloudPresence.pyw to launch.
echo Optional system tray packages were not installed.
pause
exit /b 0
:python_error
echo Python 3.8 or newer is required and must be available as python on PATH.
goto :failed
:tk_error
echo Tkinter is missing. Enable Tcl/Tk support in your Python installation.
goto :failed
:missing
echo requirements.txt is missing. Extract the complete release first.
goto :failed
:install_error
echo Installation failed. See the error above.
:failed
pause
exit /b 1
