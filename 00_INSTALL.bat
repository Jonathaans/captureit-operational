@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    set "PY=python"
  ) else (
    echo.
    echo Python belum ditemukan.
    echo Install Python 3.11/3.12 lalu jalankan file ini lagi.
    echo Jika winget tersedia:
    echo   winget install Python.Python.3.12
    echo.
    pause
    exit /b 1
  )
)

echo Installing RecordCountdown Studio dependencies...
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt

echo.
echo Selesai.
pause
