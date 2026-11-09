@echo off
rem Start the AKADock MCP server on AKADock's own Python. A plugin cannot ask where AKADock is
rem installed, so find it: AKADOCK_HOME, then what the installer recorded, then the default folder.
setlocal

if defined AKADOCK_HOME if exist "%AKADOCK_HOME%\run.py" goto run

for /f "tokens=2,*" %%a in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\AKADock" /v InstallLocation 2^>nul ^| findstr /i InstallLocation') do set "AKADOCK_HOME=%%b"
if defined AKADOCK_HOME if exist "%AKADOCK_HOME%\run.py" goto run

set "AKADOCK_HOME=%LOCALAPPDATA%\Programs\AKADock"
if exist "%AKADOCK_HOME%\run.py" goto run

echo AKADock is not installed. Get it from https://amr-alani.github.io/akadock/ 1>&2
echo (or set AKADOCK_HOME to the folder holding run.py). 1>&2
exit /b 1

:run
"%AKADOCK_HOME%\python\python.exe" "%~dp0akadock_mcp.py"
