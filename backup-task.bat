@echo off
rem Dipanggil oleh Windows Task Scheduler (tanpa jeda/pause). Hasil dicatat di backups\backup.log
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py") else (set "PY=python")
if not exist backups mkdir backups
echo [%date% %time%] mulai >> backups\backup.log
%PY% backup.py >> backups\backup.log 2>&1
if errorlevel 1 (echo [%date% %time%] GAGAL >> backups\backup.log) else (echo [%date% %time%] selesai >> backups\backup.log)
