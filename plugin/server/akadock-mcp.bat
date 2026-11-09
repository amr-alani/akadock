@echo off
rem Start the AKADock MCP server on AKADock's own Python. A plugin cannot ask where AKADock is
rem installed, so find it: AKADOCK_HOME, then what the installer recorded, then the default folder.
rem Written without labels or bracketed blocks on purpose, so it runs whatever line endings it
rem arrives with - cmd cannot find a label in a file saved with Unix line endings.
setlocal

if not exist "%AKADOCK_HOME%\run.py" for /f "tokens=2,*" %%a in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\AKADock" /v InstallLocation 2^>nul ^| findstr /i InstallLocation') do set "AKADOCK_HOME=%%b"

if not exist "%AKADOCK_HOME%\run.py" set "AKADOCK_HOME=%LOCALAPPDATA%\Programs\AKADock"

if not exist "%AKADOCK_HOME%\run.py" echo AKADock is not installed. Get it from https://amr-alani.github.io/akadock/ 1>&2
if not exist "%AKADOCK_HOME%\run.py" echo (or set AKADOCK_HOME to the folder that holds run.py). 1>&2
if not exist "%AKADOCK_HOME%\run.py" exit /b 1

"%AKADOCK_HOME%\python\python.exe" "%~dp0akadock_mcp.py"
