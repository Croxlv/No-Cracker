@echo off
title AuthMe SHA256 Kirici
cd /d "c:\Users\erolc\OneDrive\Masaüstü\SHA256-AuthMe-decode-main"

echo Kutuphaneler kontrol ediliyor...
python -m pip install -r requirements.txt --quiet

python authme_gui.py
if errorlevel 1 (
    echo.
    echo HATA: Python bulunamadi veya script calistirilamadi!
    echo "py" komutu deneniyor...
    py -m pip install -r requirements.txt --quiet
    py authme_gui.py
)
pause
