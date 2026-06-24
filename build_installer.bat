@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: ============================================
::  Novel Writer Build Script
::  Usage:  build_installer.bat
::  Output:  output\NovelWriter-Setup.exe
:: ============================================

set "APP_NAME=NovelWriter"
set "PYTHON=.venv\Scripts\python.exe"

echo.
echo  ========================================
echo   %APP_NAME% - Build Installer
echo  ========================================
echo.

:: Check Python venv
if not exist "%PYTHON%" (
    echo [+] Creating virtual environment...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create venv
        pause
        exit /b 1
    )
    echo [+] Installing dependencies...
    "%PYTHON%" -m pip install -e . -i https://mirrors.aliyun.com/pypi/simple/
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
)

:: Check/Install PyInstaller
"%PYTHON%" -c "import PyInstaller" >nul 2>&1
if %errorlevel% neq 0 (
    echo [+] Installing PyInstaller...
    "%PYTHON%" -m pip install pyinstaller -i https://mirrors.aliyun.com/pypi/simple/
)

:: Locate Inno Setup
set "ISCC="
for %%P in (
    "D:\Inno Setup 6\ISCC.exe"
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
) do (
    if exist %%P set "ISCC=%%~P"
)

if not defined ISCC (
    echo.
    echo [ERROR] Inno Setup 6 not found.
    echo         Download: https://jrsoftware.org/isdl.php
    pause
    exit /b 1
)

:: Clean old builds
echo [+] Cleaning old builds...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist output rmdir /s /q output
if exist "%USERPROFILE%\AppData\Local\pyinstaller" rmdir /s /q "%USERPROFILE%\AppData\Local\pyinstaller"

:: Generate logo.ico from logo.png
echo [+] Generating logo.ico from logo.png...
if exist logo.ico del /f logo.ico
"%PYTHON%" -c "from PIL import Image; img = Image.open('logo.png'); img.save('logo.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
if not exist "logo.ico" (
    echo [ERROR] Failed to generate logo.ico from logo.png
    pause
    exit /b 1
)

:: Build exe with PyInstaller
echo [+] Building executable...
echo.

if exist "novel-writer.spec" (
    "%PYTHON%" -m PyInstaller --clean --noconfirm novel-writer.spec
) else (
    echo [ERROR] novel-writer.spec not found.
    pause
    exit /b 1
)

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Build failed! Check the output above for details.
    pause
    exit /b 1
)

if not exist "dist\%APP_NAME%.exe" (
    echo.
    echo [ERROR] dist\%APP_NAME%.exe not found after build.
    pause
    exit /b 1
)

:: Build installer with Inno Setup
echo.
echo [+] Building installer with Inno Setup...

taskkill /F /IM "%APP_NAME%.exe" >nul 2>&1
timeout /t 2 /nobreak >nul

"!ISCC!" /Q installer.iss
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Installer build failed!
    pause
    exit /b 1
)

echo.
echo  ========================================
echo   Build successful!
echo   Output: output\%APP_NAME%-Setup.exe
echo  ========================================
echo.
pause
