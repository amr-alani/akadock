@echo off
rem Builds AKADock.exe (the app window) with the C# compiler that ships with Windows (.NET Framework 4).
rem Usage: shell\build.bat [output exe path]   (default: AKADock.exe in the AKADock folder)
cd /d "%~dp0.."
set OUT=%~1
if "%OUT%"=="" set OUT=AKADock.exe
"%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /optimize /target:winexe /platform:x64 /win32icon:shell\akadock.ico /out:"%OUT%" /r:shell\Microsoft.Web.WebView2.Core.dll /r:shell\Microsoft.Web.WebView2.WinForms.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll shell\AKADock.cs
