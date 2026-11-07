@echo off
setlocal
cd /d "%~dp0.."
if not exist output\daily mkdir output\daily
where uv >nul 2>nul
if errorlevel 1 set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV set "UV=uv"
rem Do not redirect into output\daily\run.log: the engine appends to that file
rem itself, and cmd's >> handle makes the engine's own append fail with
rem PermissionError, which turns a successful run into exit code 1.
"%UV%" run horizon-daily --config data\config.daily.json --out output\daily >> output\daily\console.log 2>&1
exit /b %ERRORLEVEL%
