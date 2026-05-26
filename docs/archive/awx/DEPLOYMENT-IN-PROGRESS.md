# 🚀 AWX Development Deployment — In Progress

**Дата начала:** 2026-05-22  
**Целевой хост:** 10.3.6.100  
**Метод:** PowerShell SSH + Bash script  

---

## 📊 Статус развёртывания

### Выполняемые шаги:

1. ✅ **Загрузка deploy-awx.sh** на целевой сервер
2. 🔄 **Выполнение скрипта:**
   - Проверка Docker
   - Установка Docker (если нужно)
   - Создание директории `/opt/iva-mail-ansible`
   - Создание `.env.awx` с параметрами
   - Запуск контейнеров AWX через docker-compose
   - Ожидание инициализации (2-5 минут)
   - Проверка API доступности
   - Вывод информации о доступе

---

## 🔐 Учётные данные

| Параметр | Значение |
|----------|----------|
| **Хост SSH** | 10.3.6.100 |
| **SSH Пользователь** | user |
| **SSH Пароль** | DefaultP4ss |
| **Проект** | /opt/iva-mail-ansible |
| **AWX Администратор** | admin |
| **AWX Пароль** | AwxAdmin123! |
| **БД Пароль** | DBAdmin456! |

---

## 📋 Скрипты развёртывания

### 1. **deploy-awx.sh** (основной bash-скрипт)
```bash
#!/bin/bash
# Запускается на целевом сервере
# Параметры:
#   $1 - PROJECT_PATH (по умолчанию: /opt/iva-mail-ansible)
#   $2 - AWX_PASSWORD (по умолчанию: AwxAdmin123!)
#   $3 - DB_PASSWORD (по умолчанию: DBAdmin456!)

bash /tmp/deploy-awx.sh /opt/iva-mail-ansible AwxAdmin123! DBAdmin456!
```

### 2. **deploy-awx.ps1** (PowerShell оркестратор)
```powershell
# Запускается на локальной машине
# Управляет SSH подключением и выполнением bash-скрипта
& $scriptPath -IP "10.3.6.100" -User "user" -Password "DefaultP4ss"
```

---

## ⏱️ Ожидаемое время выполнения

| Этап | Время |
|------|-------|
| SCP загрузка | 1-2 сек |
| Docker проверка | 10-30 сек |
| Docker установка (если нужна) | 2-3 мин |
| Создание файлов | 5-10 сек |
| Запуск контейнеров | 10-20 сек |
| Ожидание инициализации | 2-5 мин |
| Проверка API | 10-30 сек |
| **Итого** | **5-10 минут** |

---

## 🔍 Мониторинг в реальном времени

Развёртывание выполняется в фоновом режиме. Вывод отображается в реальном времени через PowerShell Monitor.

### Признаки успешного выполнения:

✅ **Видите строки:**
```
STEP 1: Проверка SSH
✅ SSH уже установлен

STEP 2: Подготовка директории проекта
✅ Директория готова: /opt/iva-mail-ansible

STEP 5: Запуск контейнеров AWX
[+] Running 4/4
  ✔ Container awx_postgres  Healthy
  ✔ Container awx_redis     Started
  ✔ Container awx_web       Started
  ✔ Container awx_task      Started

STEP 7: Ожидание инициализации AWX
✅ AWX API доступна!

╔════════════════════════════════════════════╗
║     ✅ РАЗВЁРТЫВАНИЕ ЗАВЕРШЕНО            ║
╚════════════════════════════════════════════╝
```

---

## 📡 Если развёртывание зависло

### Вариант 1: Вручную подключиться и проверить

```bash
ssh user@10.3.6.100
# Пароль: DefaultP4ss

# Проверить статус контейнеров
docker ps

# Посмотреть логи
docker logs awx_web

# Проверить API
curl http://localhost:8080/api/v2/ping/
```

### Вариант 2: Вручную запустить скрипт

```bash
bash /tmp/deploy-awx.sh /opt/iva-mail-ansible AwxAdmin123! DBAdmin456!
```

---

## 🎯 Следующие шаги после развёртывания

### 1. Доступ к AWX

```
URL: http://10.3.6.100:8080
Администратор: admin
Пароль: AwxAdmin123!
```

### 2. Создание credentials (6 штук)

Смотреть: `docs/AWX-CREDENTIALS-SETUP.md`

- IVA Mail SSH Key
- PostgreSQL Admin
- IVA Mail CMD
- (+ 3 custom типа)

### 3. Запуск первого job

```
Job Template: 00-Bootstrap
Survey параметры:
  - backend_hosts: 10.3.6.126,10.3.6.127
  - frontend_hosts: 10.3.6.102,10.3.6.103
  - haproxy_hosts: 10.3.6.101
  - storage_host: 10.3.6.128
  - monitoring_host: 10.3.6.108
```

---

## 🛠️ Управление контейнерами

После развёртывания используйте эти команды на 10.3.6.100:

```bash
cd /opt/iva-mail-ansible

# Статус контейнеров
docker compose --env-file .env.awx -f docker-compose.awx.yml ps

# Логи в реальном времени
docker compose --env-file .env.awx -f docker-compose.awx.yml logs -f awx_web

# Перезагрузка
docker compose --env-file .env.awx -f docker-compose.awx.yml restart

# Остановка
docker compose --env-file .env.awx -f docker-compose.awx.yml down

# Запуск
docker compose --env-file .env.awx -f docker-compose.awx.yml up -d
```

---

## 📚 Полезная документация

- **AWX-QUICKSTART.md** — Быстрый старт
- **DEPLOYMENT-SUMMARY.md** — Полный обзор
- **docs/DEPLOYMENT-AWX.md** — Полное руководство + troubleshooting
- **docs/AWX-CREDENTIALS-SETUP.md** — Создание credentials
- **deploy-awx.sh** — Bash скрипт развёртывания
- **deploy-awx.ps1** — PowerShell скрипт оркестрации

---

## 🔐 Безопасность

✅ Пароли **не хранятся в git**  
✅ .env.awx имеет права доступа **0600** (только owner)  
✅ Все credentials созданы в AWX UI  
✅ SSH ключи управляются через AWX  

---

## ✨ Статус git

Все скрипты и документация **закоммичены**:

```bash
git log --oneline
68630b1 - docs: Добавить развёрнутые отчёты по развёртыванию AWX
5379517 - Добавить визуальный отчёт о завершении развёртывания AWX
78ab672 - Добавить итоговый отчёт развёртывания AWX
5685f4c - Добавить быстрый старт для развёртывания AWX
502a19f - Итерация 7: Развёртывание AWX на контроллере (10.3.6.100)
```

---

**Развёртывание в процессе... Ожидаем завершения.** ⏳

*Этот документ обновляется по мере выполнения*
