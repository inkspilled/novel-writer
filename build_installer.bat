@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: ============================================
::  Novel Writer - Build Installer
::  Output:  output\NovelWriter-Setup.exe
::  模式: onedir（DLL 形式，启动快，杀毒误报少）
:: ============================================

set "PYTHON=.venv\Scripts\python.exe"

echo.
echo  ========================================
echo   Novel Writer - Build Installer
echo  ========================================
echo.

:: Check Python venv
if not exist "%PYTHON%" (
    echo [+] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
    echo [+] Installing dependencies...
    "%PYTHON%" -m pip install -e . -i https://mirrors.aliyun.com/pypi/simple/
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

:: Check/Install PyInstaller
"%PYTHON%" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [+] Installing PyInstaller...
    "%PYTHON%" -m pip install pyinstaller -i https://mirrors.aliyun.com/pypi/simple/
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller.
        pause
        exit /b 1
    )
)

:: Locate Inno Setup 6
set "ISCC="
if exist "D:\Inno Setup 6\ISCC.exe" set "ISCC=D:\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

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

:: Generate logo.ico from logo.png
echo [+] Generating logo.ico from logo.png...
if exist logo.ico del /f logo.ico
"%PYTHON%" -c "from PIL import Image; img = Image.open('logo.png'); img.save('logo.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
if not exist "logo.ico" (
    echo [ERROR] Failed to generate logo.ico from logo.png.
    pause
    exit /b 1
)

:: Build exe with PyInstaller (onedir mode)
echo [+] Building executable [PyInstaller onedir]...
echo.

if exist "novel-writer.spec" (
    "%PYTHON%" -m PyInstaller --clean --noconfirm novel-writer.spec
) else (
    echo [ERROR] novel-writer.spec not found.
    pause
    exit /b 1
)

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed! Check the output above for details.
    pause
    exit /b 1
)

if not exist "dist\NovelWriter\NovelWriter.exe" (
    echo.
    echo [ERROR] No executable found in dist\NovelWriter\ after build.
    pause
    exit /b 1
)

:: Build installer with Inno Setup
echo.
echo [+] Building installer with Inno Setup...

:: Kill any running instance to avoid file lock
taskkill /F /IM "NovelWriter.exe" >nul 2>&1
timeout /t 2 /nobreak >nul

"!ISCC!" /Q installer.iss
if errorlevel 1 (
    echo.
    echo [ERROR] Installer build failed!
    pause
    exit /b 1
)

echo.
echo  ========================================
echo   Build successful!
echo   Output: output\NovelWriter-Setup.exe
echo   模式: onedir (DLL) - 启动快
echo  ========================================
echo.
pause
