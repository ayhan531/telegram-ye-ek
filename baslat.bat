@echo off
chcp 65001 >nul
title Telegram Uye Aktarici Dashboard

echo ========================================================
echo   Telegram Uye Aktarici Web Dashboard Baslatiliyor
echo ========================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [HATA] Python bulunamadi! Lutfen Python yukleyin.
    pause
    exit /b
)

echo [*] Gerekli paketler kontrol ediliyor...
python -m pip install -r requirements.txt --quiet

echo.
echo [*] Sunucu baslatiliyor: http://localhost:8000
echo [*] Tarayiciniz otomatik acilacaktir...
echo.

start "" "http://localhost:8000"

python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
pause
