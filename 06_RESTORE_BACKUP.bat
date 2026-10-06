@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py") else (set "PY=python")
echo === PULIHKAN BACKUP ===
echo MATIKAN SERVER (jendela 01_START) SEBELUM melanjutkan.
echo Database saat ini disimpan dulu sebagai ops.sqlite3.before-restore-...
echo.
%PY% backup.py list
echo.
set /p F=Ketik nama file backup (mis. ops-backup-20261006-120000.zip): 
if "%F%"=="" goto :eof
set /p OK=Ketik YA untuk memulihkan: 
if /i not "%OK%"=="YA" (echo Dibatalkan. & pause & goto :eof)
if exist "%F%" (set "P=%F%") else (set "P=backups\%F%")
%PY% backup.py restore "%P%" --yes
pause
