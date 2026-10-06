@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py") else (set "PY=python")

echo Membuat backup...
%PY% backup.py
if errorlevel 1 (
  echo.
  echo BACKUP GAGAL. Lihat pesan di atas.
) else (
  echo.
  echo Memeriksa backup terbaru...
  %PY% backup.py verify
)
pause
