# 🚀 Инструкции по развёртыванию AWX на 10.3.6.100

Я подготовил всё необходимое для развёртывания AWX. Ниже три способа выполнить это.

---

## ⚡ Способ 1: Используя PowerShell с sshpass (Рекомендуется)

### 1. Установить sshpass

На Windows машине установите sshpass через Chocolatey:

```powershell
choco install -y sshpass
```

Или вручную скачайте сборку: https://github.com/mkropat/sshpass-win

### 2. Запустить развёртывание

```powershell
# Переменные
$ip = "10.3.6.100"
$user = "user"
$password = "DefaultP4ss"
$scriptPath = "E:\AI Projects\claude-code-orchestrator-kit-main\projects\ADToolKit\deploy-awx.sh"

# Загрузить скрипт
sshpass -p $password scp -o StrictHostKeyChecking=accept-new $scriptPath "${user}@${ip}:/tmp/deploy-awx.sh"

# Выполнить скрипт
sshpass -p $password ssh -o StrictHostKeyChecking=accept-new ${user}@${ip} "bash /tmp/deploy-awx.sh /opt/iva-mail-ansible AwxAdmin123! DBAdmin456!"
```

---

## 📝 Способ 2: Вручную через SSH

### 1. Подключиться к серверу

```bash
ssh user@10.3.6.100
# Пароль: DefaultP4ss
```

### 2. Клонировать или создать проект

```bash
# Опция A: Если Git доступен
git clone <your-repo-url> /opt/iva-mail-ansible

# Опция B: Создать директорию
mkdir -p /opt/iva-mail-ansible
cd /opt/iva-mail-ansible
```

### 3. Скопировать docker-compose.awx.yml

Скопируйте из проекта на локальной машине:
```
E:\AI Projects\claude-code-orchestrator-kit-main\projects\ADToolKit\docker-compose.awx.yml
```

На сервер в `/opt/iva-mail-ansible/`

### 4. Запустить скрипт развёртывания

Скопируйте deploy-awx.sh на сервер и выполните:

```bash
# На сервере
bash /tmp/deploy-awx.sh /opt/iva-mail-ansible AwxAdmin123! DBAdmin456!
```

---

## 🖥️ Способ 3: Используя WinSCP

### 1. Открыть WinSCP

Скачайте: https://winscp.net/download/WinSCP620.exe

### 2. Подключиться

- **Host:** 10.3.6.100
- **Username:** user
- **Password:** DefaultP4ss
- **Protocol:** SSH

### 3. Загрузить файлы

Скопируйте в `/opt/iva-mail-ansible/`:
- `docker-compose.awx.yml`
- `deploy-awx.sh`

### 4. Выполнить скрипт

В WinSCP откройте Terminal и выполните:

```bash
bash /opt/iva-mail-ansible/deploy-awx.sh /opt/iva-mail-ansible AwxAdmin123! DBAdmin456!
```

---

## 📁 Файлы, готовые к развёртыванию

Все файлы находятся в:
```
E:\AI Projects\claude-code-orchestrator-kit-main\projects\ADToolKit\
```

### Основные файлы:

| Файл | Назначение | Где использовать |
|------|-----------|------------------|
| `deploy-awx.sh` | Bash-скрипт развёртывания | Выполнить на 10.3.6.100 |
| `docker-compose.awx.yml` | Docker Compose конфиг | Скопировать в /opt/iva-mail-ansible/ |
| `deploy-awx.ps1` | PowerShell оркестратор | Выполнить локально с sshpass |

### Документация:

| Файл | Содержимое |
|------|-----------|
| `AWX-QUICKSTART.md` | Быстрый старт |
| `DEPLOYMENT-SUMMARY.md` | Полный обзор + архитектура |
| `docs/DEPLOYMENT-AWX.md` | Детальное руководство |
| `docs/AWX-CREDENTIALS-SETUP.md` | Создание credentials |

---

## 🎯 Что произойдёт при запуске deploy-awx.sh

Скрипт автоматически выполнит:

```
✅ STEP 1: Проверка Docker
   - Если Docker не установлен → установит
   - Если установлен → пропустит

✅ STEP 2: Подготовка директории проекта
   - Создаст /opt/iva-mail-ansible
   - Установит правильные права доступа

✅ STEP 3: Создание .env.awx
   - Создаст файл с параметрами Docker

✅ STEP 4: Проверка docker-compose.awx.yml
   - Убедится что файл на месте

✅ STEP 5: Запуск контейнеров AWX
   - docker compose up -d

✅ STEP 6: Статус контейнеров
   - Покажет какие контейнеры запущены

✅ STEP 7: Ожидание инициализации AWX
   - Будет ждать пока AWX API не будет доступна
   - Это займет 2-5 минут

✅ ИТОГ: Вывод информации для доступа
   - URL: http://10.3.6.100:8080
   - Администратор: admin
   - Пароль: AwxAdmin123!
```

**Общее время: 5-10 минут**

---

## 🔐 Параметры развёртывания

Если хотите изменить пароли, отредактируйте их в команде:

```bash
bash deploy-awx.sh /opt/iva-mail-ansible ВАШ_ПАРОЛЬ_AWX ВАШ_ПАРОЛЬ_БД
```

По умолчанию:
- Проект: `/opt/iva-mail-ansible`
- AWX пароль: `AwxAdmin123!`
- БД пароль: `DBAdmin456!`

---

## ✅ После развёртывания

### 1. Доступ к AWX

```
URL: http://10.3.6.100:8080
Логин: admin
Пароль: AwxAdmin123! (или ваш)
```

### 2. Создание credentials (ОБЯЗАТЕЛЬНО)

Следуйте: `docs/AWX-CREDENTIALS-SETUP.md`

Нужны 6 credentials:
- IVA Mail SSH Key
- PostgreSQL Admin
- IVA Mail CMD
- (+ 3 custom типа, структура уже определена)

### 3. Первый запуск job

```
Job Template: 00-Bootstrap
Survey параметры:
  Backend Hosts: 10.3.6.126,10.3.6.127
  Frontend Hosts: 10.3.6.102,10.3.6.103
  HAProxy Hosts: 10.3.6.101
  Storage Host: 10.3.6.128
  Monitoring Host: 10.3.6.108
```

---

## 🛠️ Управление контейнерами после развёртывания

```bash
# На сервере 10.3.6.100
cd /opt/iva-mail-ansible

# Статус
docker compose --env-file .env.awx -f docker-compose.awx.yml ps

# Логи
docker compose --env-file .env.awx -f docker-compose.awx.yml logs -f awx_web

# Перезагрузка
docker compose --env-file .env.awx -f docker-compose.awx.yml restart

# Остановка
docker compose --env-file .env.awx -f docker-compose.awx.yml down
```

---

## 🔍 Если что-то пошло не так

### Проверить статус контейнеров

```bash
ssh user@10.3.6.100
docker ps
```

### Посмотреть логи

```bash
docker logs awx_web
docker logs awx_postgres
docker logs awx_redis
```

### Проверить API

```bash
curl http://10.3.6.100:8080/api/v2/ping/
```

### Перезагрузить контейнеры

```bash
cd /opt/iva-mail-ansible
docker compose --env-file .env.awx -f docker-compose.awx.yml restart
```

---

## 📋 Чеклист развёртывания

- [ ] Загружены файлы на сервер
- [ ] Выполнен deploy-awx.sh
- [ ] Docker контейнеры запущены (`docker ps`)
- [ ] AWX API доступна (curl http://localhost:8080/api/v2/ping/)
- [ ] Открыт http://10.3.6.100:8080 в браузере
- [ ] Логин с admin/AwxAdmin123! работает
- [ ] Созданы 6 credentials
- [ ] SSH credential тестирован (ping backend)
- [ ] Job Template 00-Bootstrap видна
- [ ] Выполнен первый job запуск

---

## 📞 Поддержка

Все документы доступны в проекте:
- `AWX-QUICKSTART.md` — Быстрый старт
- `DEPLOYMENT-SUMMARY.md` — Полный обзор
- `docs/DEPLOYMENT-AWX.md` — Troubleshooting
- `docs/AWX-CREDENTIALS-SETUP.md` — Credentials

---

**Готовы начинать развёртывание!** 🎯
