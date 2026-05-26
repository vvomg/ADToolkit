@echo off
REM ============================================================================
REM ADToolKit — Windows Installer Build Script
REM
REM Шаги:
REM   1. npm run build  (сборка React SPA)
REM   2. Создание deploy.zip (архив для загрузки на сервер)
REM   3. PyInstaller → ADToolKit-Setup.exe
REM
REM Требования:
REM   - Node.js + npm в PATH
REM   - Python 3.9+ в PATH
REM   - pip install pyinstaller paramiko cryptography
REM ============================================================================

setlocal enabledelayedexpansion

echo.
echo  ===================================================
echo   ADToolKit — Build Windows Installer
echo  ===================================================
echo.

REM ── 0. Проверить инструменты ────────────────────────────────────────────────
where node >nul 2>&1
if errorlevel 1 (
    echo [ERR] Node.js не найден в PATH. Установите Node.js 20+.
    pause & exit /b 1
)
where python >nul 2>&1
if errorlevel 1 (
    echo [ERR] Python не найден в PATH.
    pause & exit /b 1
)

echo [OK] Node.js:
node --version
echo [OK] Python:
python --version

REM ── 1. Сборка frontend ──────────────────────────────────────────────────────
echo.
echo [1/3] Сборка React frontend...
echo.

pushd ..\frontend
call npm ci --silent
if errorlevel 1 (
    echo [ERR] npm ci завершился с ошибкой
    popd & pause & exit /b 1
)

call npm run build
if errorlevel 1 (
    echo [ERR] npm run build завершился с ошибкой
    popd & pause & exit /b 1
)
popd

echo [OK] Frontend собран → frontend\dist\

REM ── 2. Создать deploy.zip ──────────────────────────────────────────────────
echo.
echo [2/3] Создание пакета установки deploy.zip...
echo.

python create_package.py
if errorlevel 1 (
    echo [ERR] Ошибка создания deploy.zip
    pause & exit /b 1
)

echo [OK] deploy.zip создан

REM ── 3. Сборка .exe через PyInstaller ──────────────────────────────────────
echo.
echo [3/3] Сборка ADToolKit-Setup.exe...
echo.

pip install pyinstaller paramiko cryptography --quiet
if errorlevel 1 (
    echo [ERR] Ошибка установки PyInstaller
    pause & exit /b 1
)

pyinstaller ^
    --onefile ^
    --windowed ^
    --name "ADToolKit-Setup" ^
    --add-data "deploy.zip;." ^
    --add-data "..\deploy\setup_server.sh;." ^
    --icon "icon.ico" ^
    installer.py 2>nul

REM Если нет иконки — повторить без неё
if errorlevel 1 (
    echo [WARN] Сборка без иконки (icon.ico не найден)...
    pyinstaller ^
        --onefile ^
        --windowed ^
        --name "ADToolKit-Setup" ^
        --add-data "deploy.zip;." ^
        --add-data "..\deploy\setup_server.sh;." ^
        installer.py
)

if errorlevel 1 (
    echo [ERR] PyInstaller завершился с ошибкой
    pause & exit /b 1
)

echo.
echo  ===================================================
echo   Готово!
echo   Установщик: dist\ADToolKit-Setup.exe
echo  ===================================================
echo.

REM Открыть папку с результатом
explorer dist

pause
