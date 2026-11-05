@echo off
setlocal
cd /d D:\Docker\Horizon
if not exist output\daily mkdir output\daily
where uv >nul 2>nul
if errorlevel 1 set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV set "UV=uv"
"%UV%" run horizon-daily --config data\config.daily.json --out output\daily >> output\daily\run.log 2>&1
exit /b %ERRORLEVEL%
