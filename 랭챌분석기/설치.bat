@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==================================================
echo  [1/2] Python parts install (pip)
echo ==================================================
py -3 -m pip install --upgrade pip
py -3 -m pip install -r requirements.txt
if errorlevel 1 python -m pip install -r requirements.txt
echo.
echo ==================================================
echo  [2/2] Tesseract OCR check
echo ==================================================
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" goto tess_ok
where tesseract >nul 2>nul
if not errorlevel 1 goto tess_ok
echo Tesseract not found. Installing with winget...
winget install -e --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements
goto done
:tess_ok
echo Tesseract OK
:done
echo.
echo DONE. Close this window and double-click "실행.bat".
pause
