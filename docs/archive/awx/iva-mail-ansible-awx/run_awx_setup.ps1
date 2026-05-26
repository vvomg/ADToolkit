# run_awx_setup.ps1
# Загружает install-awx.sh + setup_awx.py на сервер и запускает установку AWX.
# Запускается локально на Windows; использует plink/pscp (PuTTY) для работы с сервером.
#
# Использование:
#   .\run_awx_setup.ps1 -TargetHost 10.3.6.100 -SshPassword "mypass" -AwxPass "AwxAdmin123!"
#
# Требования: PuTTY (plink.exe + pscp.exe), Windows 10+

param(
    [string]$TargetHost   = "10.3.6.100",
    [string]$SshUser      = "user",
    [string]$SshPassword  = "DefaultP4ss",
    [string]$AwxPass      = "AwxAdmin123!",
    [string]$DbPass       = "DBAdmin456!",
    [string]$ProjectPath  = "/opt/iva-mail-ansible",
    [string]$AwxPort      = "8080",
    [string]$AwxVersion   = "23.9.0",
    [switch]$SkipDocker,
    [switch]$SkipPreconfig,
    [switch]$Reinstall,
    [string]$Plink        = "",
    [string]$Pscp         = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Поиск PuTTY инструментов в стандартных путях
# ---------------------------------------------------------------------------
$puttySearchPaths = @(
    "C:\Program Files\PuTTY",
    "C:\Program Files (x86)\PuTTY",
    "$env:LOCALAPPDATA\Programs\PuTTY",
    "$env:USERPROFILE\AppData\Local\Programs\PuTTY",
    "C:\Tools\PuTTY",
    "C:\Windows\System32"
)

function Find-PuttyTool {
    param([string]$ToolName, [string]$Override)
    if ($Override -and (Test-Path $Override)) { return $Override }
    foreach ($dir in $puttySearchPaths) {
        $candidate = Join-Path $dir $ToolName
        if (Test-Path $candidate) { return $candidate }
    }
    # Попробовать PATH
    $fromPath = Get-Command $ToolName -ErrorAction SilentlyContinue
    if ($fromPath) { return $fromPath.Source }
    return $null
}

$PlinkExe = Find-PuttyTool "plink.exe" $Plink
$PscpExe  = Find-PuttyTool "pscp.exe"  $Pscp

# ---------------------------------------------------------------------------
# Баннер
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  +--------------------------------------------------+" -ForegroundColor Cyan
Write-Host "  |   IVA Mail AWX Setup - Windows Launcher          |" -ForegroundColor Cyan
Write-Host "  +--------------------------------------------------+" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Target: $SshUser@$TargetHost" -ForegroundColor White
Write-Host "  Project: $ProjectPath" -ForegroundColor White
Write-Host "  AWX port: $AwxPort" -ForegroundColor White
Write-Host ""

# ---------------------------------------------------------------------------
# STEP 1: Проверка PuTTY
# ---------------------------------------------------------------------------
Write-Host "=== Step 1: Проверка PuTTY инструментов ===" -ForegroundColor Cyan

if (-not $PlinkExe) {
    Write-Host "  [ERROR] plink.exe не найден." -ForegroundColor Red
    Write-Host "  Установите PuTTY: https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html" -ForegroundColor Yellow
    Write-Host "  Или укажите путь: -Plink 'C:\path\to\plink.exe'" -ForegroundColor Yellow
    exit 1
}
if (-not $PscpExe) {
    Write-Host "  [ERROR] pscp.exe не найден." -ForegroundColor Red
    Write-Host "  Установите PuTTY или укажите: -Pscp 'C:\path\to\pscp.exe'" -ForegroundColor Yellow
    exit 1
}

Write-Host "  [OK] plink: $PlinkExe" -ForegroundColor Green
Write-Host "  [OK] pscp:  $PscpExe" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
function Invoke-Remote {
    param([string]$Cmd, [switch]$NoStrictExit)
    $result = & $PlinkExe -pw $SshPassword -batch "${SshUser}@${TargetHost}" $Cmd
    if ($LASTEXITCODE -ne 0 -and -not $NoStrictExit) {
        Write-Host "  [WARN] Remote command exited $LASTEXITCODE" -ForegroundColor Yellow
    }
    return $result
}

function Upload-File {
    param([string]$LocalPath, [string]$RemotePath)
    & $PscpExe -pw $SshPassword -batch $LocalPath "${SshUser}@${TargetHost}:${RemotePath}"
    if ($LASTEXITCODE -ne 0) {
        throw "pscp upload failed for $LocalPath -> $RemotePath"
    }
}

function Show-Progress {
    param([string]$Message, [int]$Step, [int]$Total)
    $pct = [int](($Step / $Total) * 100)
    Write-Progress -Activity "AWX Setup" -Status $Message -PercentComplete $pct
    Write-Host "  [$Step/$Total] $Message" -ForegroundColor White
}

# ---------------------------------------------------------------------------
# STEP 2: Определение пути к локальным файлам
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Step 2: Локальные файлы ===" -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
# Путь к install-awx.sh — на уровень вверх от awx/
$LocalInstallScript = Join-Path (Split-Path -Parent (Split-Path -Parent $ScriptDir)) "install-awx.sh"
$LocalSetupPy       = Join-Path $ScriptDir "setup_awx.py"

# Если скрипт запущен не из awx/, попробуем ADToolKit/
if (-not (Test-Path $LocalInstallScript)) {
    # Fallback: ищем относительно скрипта
    $candidates = @(
        (Join-Path $ScriptDir "..\..\install-awx.sh"),
        (Join-Path $ScriptDir "..\install-awx.sh"),
        (Join-Path $ScriptDir "install-awx.sh")
    )
    foreach ($c in $candidates) {
        $resolved = [System.IO.Path]::GetFullPath($c)
        if (Test-Path $resolved) {
            $LocalInstallScript = $resolved
            break
        }
    }
}

if (-not (Test-Path $LocalInstallScript)) {
    Write-Host "  [ERROR] install-awx.sh не найден. Ожидаемый путь: $LocalInstallScript" -ForegroundColor Red
    Write-Host "  Укажите правильный путь или запустите скрипт из директории проекта." -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path $LocalSetupPy)) {
    Write-Host "  [WARN] setup_awx.py не найден: $LocalSetupPy" -ForegroundColor Yellow
}

Write-Host "  [OK] install-awx.sh: $LocalInstallScript" -ForegroundColor Green
Write-Host "  [OK] setup_awx.py:   $LocalSetupPy" -ForegroundColor Green

# ---------------------------------------------------------------------------
# STEP 3: Проверка подключения к серверу
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Step 3: Проверка подключения ===" -ForegroundColor Cyan

$pingResult = Invoke-Remote "echo 'connection_ok'" -NoStrictExit
if ($pingResult -notmatch "connection_ok") {
    Write-Host "  [ERROR] Не удалось подключиться к $TargetHost" -ForegroundColor Red
    Write-Host "  Проверьте: IP-адрес, SSH-пользователь, пароль, доступность порта 22" -ForegroundColor Yellow
    exit 1
}
Write-Host "  [OK] Подключение к $TargetHost установлено" -ForegroundColor Green

$serverInfo = Invoke-Remote "uname -r && lsb_release -d 2>/dev/null || cat /etc/os-release | grep PRETTY_NAME" -NoStrictExit
Write-Host "  Server: $serverInfo" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# STEP 4: Подготовка директории на сервере
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Step 4: Подготовка директории на сервере ===" -ForegroundColor Cyan

Invoke-Remote "sudo mkdir -p '$ProjectPath'" | Out-Null
Invoke-Remote "sudo chown -R ${SshUser}:${SshUser} '$ProjectPath' 2>/dev/null || true" -NoStrictExit | Out-Null
Write-Host "  [OK] Директория $ProjectPath готова" -ForegroundColor Green

# ---------------------------------------------------------------------------
# STEP 5: Загрузка install-awx.sh на сервер
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Step 5: Загрузка install-awx.sh ===" -ForegroundColor Cyan

$remoteInstallScript = "$ProjectPath/install-awx.sh"
Upload-File $LocalInstallScript $remoteInstallScript
Invoke-Remote "chmod +x '$remoteInstallScript'" | Out-Null
Write-Host "  [OK] install-awx.sh загружен -> $remoteInstallScript" -ForegroundColor Green

# ---------------------------------------------------------------------------
# STEP 6: Загрузка setup_awx.py на сервер
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Step 6: Загрузка setup_awx.py ===" -ForegroundColor Cyan

if (Test-Path $LocalSetupPy) {
    # Создать целевую директорию
    Invoke-Remote "mkdir -p '$ProjectPath/iva-mail-ansible/awx'" | Out-Null
    $remoteSetupPy = "$ProjectPath/iva-mail-ansible/awx/setup_awx.py"
    Upload-File $LocalSetupPy $remoteSetupPy
    Write-Host "  [OK] setup_awx.py загружен -> $remoteSetupPy" -ForegroundColor Green
} else {
    Write-Host "  [WARN] setup_awx.py не найден локально, пропуск" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# STEP 7: Формирование аргументов install-awx.sh
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Step 7: Запуск install-awx.sh ===" -ForegroundColor Cyan

$installArgs = "--project-path '$ProjectPath' --awx-pass '$AwxPass' --db-pass '$DbPass' --awx-port '$AwxPort' --awx-version '$AwxVersion'"
if ($SkipDocker)   { $installArgs += " --skip-docker" }
if ($SkipPreconfig){ $installArgs += " --skip-preconfigure" }
if ($Reinstall)    { $installArgs += " --reinstall" }

Write-Host "  Команда: sudo bash $remoteInstallScript $installArgs" -ForegroundColor Gray
Write-Host "  (Это займёт 5-15 минут...)" -ForegroundColor Yellow
Write-Host ""

# Запуск установщика — вывод в реальном времени через plink
# plink не поддерживает true live streaming, но выводит результат блоками
$installCmd = "sudo bash '$remoteInstallScript' $installArgs 2>&1"
$installOutput = Invoke-Remote $installCmd -NoStrictExit
$installOutput | ForEach-Object { Write-Host "  $_" }

# ---------------------------------------------------------------------------
# STEP 8: Проверка результата
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Step 8: Проверка установки ===" -ForegroundColor Cyan

$containerStatus = Invoke-Remote "docker compose --env-file '$ProjectPath/.env.awx' -f '$ProjectPath/docker-compose.awx.yml' ps 2>/dev/null" -NoStrictExit
if ($containerStatus) {
    Write-Host "  Статус контейнеров:" -ForegroundColor White
    $containerStatus | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
} else {
    Write-Host "  [WARN] Не удалось получить статус контейнеров" -ForegroundColor Yellow
}

# Проверка HTTP доступности
$awxCheck = Invoke-Remote "curl -sf --max-time 10 http://localhost:${AwxPort}/api/v2/ping/ 2>/dev/null && echo 'AWX_UP' || echo 'AWX_DOWN'" -NoStrictExit
if ($awxCheck -match "AWX_UP") {
    Write-Host "  [OK] AWX API доступен на порту $AwxPort" -ForegroundColor Green
} else {
    Write-Host "  [WARN] AWX API не отвечает на порту $AwxPort (возможно, ещё запускается)" -ForegroundColor Yellow
}

Write-Progress -Activity "AWX Setup" -Completed

# ---------------------------------------------------------------------------
# Итоговый отчёт
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  +--------------------------------------------------+" -ForegroundColor Green
Write-Host "  |   УСТАНОВКА ЗАВЕРШЕНА                            |" -ForegroundColor Green
Write-Host "  +--------------------------------------------------+" -ForegroundColor Green
Write-Host ""
Write-Host "  URL:           http://${TargetHost}:${AwxPort}" -ForegroundColor White
Write-Host "  Администратор: admin" -ForegroundColor White
Write-Host "  Пароль AWX:    $AwxPass" -ForegroundColor White
Write-Host ""
Write-Host "  СЛЕДУЮЩИЕ ШАГИ:" -ForegroundColor Yellow
Write-Host "    1. Откройте http://${TargetHost}:${AwxPort}" -ForegroundColor White
Write-Host "    2. Войдите: admin / $AwxPass" -ForegroundColor White
Write-Host "    3. Credentials -> IVA Mail SSH Key -> Edit (реальный SSH ключ)" -ForegroundColor White
Write-Host "    4. Credentials -> IVA Mail CMD -> Edit (cmd_user / cmd_password)" -ForegroundColor White
Write-Host "    5. Credentials -> PostgreSQL Admin -> Edit (pg_admin_password)" -ForegroundColor White
Write-Host "    6. Templates -> 00-Bootstrap -> Launch" -ForegroundColor White
Write-Host ""
Write-Host "  УПРАВЛЕНИЕ НА СЕРВЕРЕ:" -ForegroundColor Gray
Write-Host "    Статус:  docker compose --env-file $ProjectPath/.env.awx -f $ProjectPath/docker-compose.awx.yml ps" -ForegroundColor Gray
Write-Host "    Логи:    docker compose --env-file $ProjectPath/.env.awx -f $ProjectPath/docker-compose.awx.yml logs -f awx_web" -ForegroundColor Gray
Write-Host "    Стоп:    docker compose --env-file $ProjectPath/.env.awx -f $ProjectPath/docker-compose.awx.yml stop" -ForegroundColor Gray
Write-Host ""
