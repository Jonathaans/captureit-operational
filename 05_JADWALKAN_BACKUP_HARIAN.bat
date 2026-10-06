@echo off
setlocal
cd /d "%~dp0"
set "T=%~1"
if "%T%"=="" set /p T=Jam backup harian (format 24 jam, mis. 12:00) [12:00]: 
if "%T%"=="" set "T=12:00"
echo.
echo Komputer harus menyala pada jam tersebut agar backup berjalan.
schtasks /Create /SC DAILY /ST %T% /TN "CaptureItOps Backup" /TR "\"%~dp0backup-task.bat\"" /F
if errorlevel 1 (
  echo Gagal membuat jadwal. Coba jalankan sebagai Administrator.
) else (
  echo Jadwal dibuat: backup harian jam %T%. Hasil: backups\backup.log
  echo Untuk menghapus: schtasks /Delete /TN "CaptureItOps Backup" /F
)
pause
