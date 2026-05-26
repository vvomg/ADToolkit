# 🚀 AWX Quick Start Guide — Развёртывание на контроллере (10.3.6.100)

Этот файл содержит пошаговые инструкции для быстрого развёртывания AWX на вашем контроллере и доступа к интерфейсу управления IVA Mail кластером.

---

## ⚡ Быстрый старт (5 минут)

### Вариант 1: Автоматизированное развёртывание (рекомендуется)

```bash
# 1. Перейти в директорию проекта
cd /path/to/iva-mail-ansible

# 2. Запустить плейбук развёртывания AWX
ansible-playbook playbooks/10-awx-setup.yml \
  -i inventory/controllers.yml \
  -e "awx_admin_password=YourSecurePassword123 awx_db_password=DBSecurePass456"

# 3. Дождаться завершения (~ 5-10 минут)
# Плейбук автоматически:
#   - Установит Docker и Docker Compose
#   - Запустит контейнеры AWX
#   - Применит всю конфигурацию
#   - Выведет URL и учётные данные для входа
```

**Результат:**
```
✓ AWX Web UI доступна: http://10.3.6.100:8080
✓ Администратор: admin
✓ Пароль: YourSecurePassword123
✓ 9 job templates готовы к использованию
✓ Workflow "IVA Mail Full Deployment" активен
```

---

## 📋 Вариант 2: Ручное развёртывание (для тестирования)

Если вы хотите контролировать каждый шаг:

```bash
# 1. На контроллере: установить Docker
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker $(whoami)
sudo systemctl start docker
sudo systemctl enable docker

# 2. Скопировать и заполнить переменные окружения
cp .env.awx.example .env.awx

# Отредактировать .env.awx:
# - AWX_ADMIN_PASSWORD=YourSecurePassword123
# - AWX_DB_PASSWORD=DBSecurePass456

# 3. Запустить контейнеры
docker compose --env-file .env.awx -f docker-compose.awx.yml up -d

# 4. Дождаться готовности (~ 2-3 минуты)
watch docker compose --env-file .env.awx -f docker-compose.awx.yml ps

# 5. Проверить здоровье AWX
curl http://localhost:8080/api/v2/ping/
# Ответ: {"version":"23.x.x"}

# 6. Применить конфигурацию
export AWX_HOST=http://10.3.6.100:8080
export AWX_USERNAME=admin
export AWX_PASSWORD=YourSecurePassword123
ansible-playbook awx/as-code/apply-awx-config.yml
```

---

## 🌐 Доступ к AWX

### Web Interface
- **URL:** http://10.3.6.100:8080
- **Администратор:** admin
- **Пароль:** (из .env.awx `AWX_ADMIN_PASSWORD`)

### Первый вход
1. Откройте http://10.3.6.100:8080 в браузере
2. Нажмите на иконку пользователя (сверху справа)
3. Выберите "User preferences"
4. Измените пароль администратора на более надёжный

---

## 🔐 Создание учётных данных (Credentials)

После входа в AWX необходимо создать 6 учётных данных для работы с кластером:

### 📋 Пошаговое руководство

1. **IVA Mail SSH Key**
   - Тип: Machine
   - Пользователь: root
   - SSH Private Key: (содержимое вашего SSH ключа)
   - Тест: Ping любого бэкенда (10.3.6.126)

2. **PostgreSQL Admin**
   - Тип: PostgreSQL
   - Хост: 10.3.6.128
   - Порт: 5432
   - БД: ivamail
   - Пользователь: postgres
   - Пароль: (из .env `POSTGRES_ADMIN_PASSWORD`)

3. **IVA Mail CMD**
   - Тип: Custom (IVA Mail CMD)
   - Пользователь: admin
   - Пароль: (из .env `MAIL_ADMIN_PASSWORD`)
   - Тест: Проверка порта 106 на бэкенде

**Подробное руководство:** `docs/AWX-CREDENTIALS-SETUP.md`

---

## 🎯 Первый запуск: 00-Bootstrap

Теперь можно запустить первый job template для настройки кластера:

1. Перейти в **Templates** → **00-Bootstrap**
2. Нажать **Launch**
3. Заполнить Survey переменные:
   ```
   Backend Hosts: 10.3.6.126,10.3.6.127
   Frontend Hosts: 10.3.6.102,10.3.6.103
   HAProxy Hosts: 10.3.6.101
   Storage Host: 10.3.6.128
   Monitoring Host: 10.3.6.108
   Package Strategy: url (или другой вариант)
   ```
4. Нажать **Next** → **Launch**
5. Смотреть логи в реальном времени

---

## 📚 Полные руководства

Для получения подробной информации см.:

- **docs/DEPLOYMENT-AWX.md**
  - Полная архитектура AWX
  - Устранение неполадок (7 сценариев)
  - Управление данными и резервные копии
  - Обновление версии

- **docs/AWX-CREDENTIALS-SETUP.md**
  - Пошаговое создание каждого Credential
  - Тестирование подключений
  - Лучшие практики безопасности

- **iva-mail-ansible/awx/as-code/README.md**
  - Структура конфигурации как код
  - Порядок применения ресурсов
  - Идемпотентность

---

## 🔧 Администрирование

### Просмотр статуса контейнеров
```bash
docker compose --env-file .env.awx -f docker-compose.awx.yml ps
```

### Просмотр логов
```bash
# Все логи
docker compose --env-file .env.awx -f docker-compose.awx.yml logs -f

# Только AWX Web
docker compose --env-file .env.awx -f docker-compose.awx.yml logs -f awx_web

# Только AWX Task
docker compose --env-file .env.awx -f docker-compose.awx.yml logs -f awx_task
```

### Перезагрузка контейнеров
```bash
docker compose --env-file .env.awx -f docker-compose.awx.yml restart
```

### Остановка контейнеров
```bash
docker compose --env-file .env.awx -f docker-compose.awx.yml down
```

### Запуск заново
```bash
docker compose --env-file .env.awx -f docker-compose.awx.yml up -d
```

---

## ⚠️ Типичные проблемы

### Порт 8080 уже используется
```bash
# Проверить что использует порт
sudo netstat -tulpn | grep 8080

# Измените порт в docker-compose.awx.yml:
# ports: "9080:8052" (вместо 8080:8052)
```

### AWX медленно стартует
- Первый запуск может занять 3-5 минут
- PostgreSQL и Redis инициализируются
- Используйте `docker logs` для отслеживания прогресса

### Ошибка подключения к PostgreSQL
```bash
# Перезагрузить контейнер БД
docker compose --env-file .env.awx -f docker-compose.awx.yml restart awx_postgres

# Дождаться здоровья (check healthcheck)
docker compose --env-file .env.awx -f docker-compose.awx.yml ps
```

### Забыли пароль администратора
```bash
# Сбросить пароль через Django shell
docker compose --env-file .env.awx -f docker-compose.awx.yml exec awx_web \
  awx-manage changepassword admin
```

---

## 📊 Архитектура AWX

```
┌─────────────────────────────────────────────────────┐
│  Docker Host (10.3.6.100)                           │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐   │
│  │  AWX Web   │  │  AWX Task  │  │   Redis    │   │
│  │  :8080     │  │  (Celery)  │  │   :6379    │   │
│  │  (REST API)│  │  (Workers) │  │  (Broker)  │   │
│  └──────┬─────┘  └──────┬─────┘  └────────────┘   │
│         │               │                          │
│         └───────┬───────┘                          │
│                 │                                  │
│         ┌───────▼────────────┐                     │
│         │  PostgreSQL 15     │                     │
│         │  (Internal DB)     │                     │
│         │  :5432             │                     │
│         └────────────────────┘                     │
│                                                     │
│  Volume Mounts:                                    │
│  - awx_projects    → /var/lib/awx/projects        │
│  - awx_media       → /var/lib/awx/public/media    │
│                                                     │
└─────────────────────────────────────────────────────┘
           │
           │ Ansible Connections (SSH)
           │
      ┌────▼──────────────────────────────────┐
      │  IVA Mail Cluster Nodes                │
      │  • 10.3.6.102 (Frontend 1)             │
      │  • 10.3.6.103 (Frontend 2)             │
      │  • 10.3.6.126 (Backend 1)              │
      │  • 10.3.6.127 (Backend 2)              │
      │  • 10.3.6.128 (PostgreSQL + NFS)       │
      │  • 10.3.6.101 (HAProxy)                │
      │  • 10.3.6.108 (Monitoring)             │
      └─────────────────────────────────────────┘
```

---

## 📞 Поддержка

Для вопросов см.:
- Логи контейнеров: `docker compose logs -f`
- AWX документация: https://docs.ansible.com/ansible-tower/
- Проект IVA Mail: см. `iva-mail-ansible/README.md`

---

## ✅ Контрольный список успешного развёртывания

- [ ] Docker установлен (`docker --version`)
- [ ] Docker Compose установлен (`docker compose version`)
- [ ] Контейнеры запущены (`docker ps`)
- [ ] AWX Web доступна (http://10.3.6.100:8080)
- [ ] Логин работает (admin / пароль)
- [ ] Организация "IVA Mail" видна
- [ ] 9 Job Templates видны
- [ ] Workflow "IVA Mail Full Deployment" видна
- [ ] 6 Credentials созданы
- [ ] Job Template 00-Bootstrap работает

---

**Готово!** Ваш AWX контроллер полностью настроен и готов к управлению IVA Mail кластером. 🎉
