@echo off
setlocal
set "CAMERA=%~1"
if "%CAMERA%"=="" set "CAMERA=EOS Webcam Utility"
echo ==== PYTHON ====
where py
where python
echo.
echo ==== FFMPEG ====
where ffmpeg
ffmpeg -version | findstr /B "ffmpeg version"
echo.
echo ==== DIRECTSHOW VIDEO DEVICES ====
ffmpeg -hide_banner -list_devices true -f dshow -i dummy 2>&1
echo.
echo ==== SUPPORTED MODES: %CAMERA% ====
ffmpeg -hide_banner -list_options true -f dshow -i video="%CAMERA%" 2>&1
echo.
echo Catatan: pesan "Error opening input file" tepat setelah daftar mode di atas
echo adalah keluaran normal dari perintah list_options, bukan hasil capture.
echo.
echo ==== 3-SECOND CAPTURE TEST: %CAMERA% ====
if not exist "%~dp0data" mkdir "%~dp0data"
ffmpeg -hide_banner -y -f dshow -i video="%CAMERA%" -t 3 -an -c:v libx264 -preset ultrafast -pix_fmt yuv420p "%~dp0data\diagnostic_camera.mp4" 2>&1
if errorlevel 1 (
  echo CAPTURE FAILED. Tutup Camera/Zoom/OBS/Chrome lalu ulangi.
) else (
  echo CAPTURE OK: data\diagnostic_camera.mp4
)
echo.
pause
