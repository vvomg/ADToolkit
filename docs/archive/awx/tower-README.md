# tower/ — Конфигурация AWX

Эта директория содержит конфигурационные файлы для standalone Docker развёртывания AWX 23.x.
Монтируется в контейнер как `/etc/tower/`.

## Файлы

| Файл | Описание | Статус |
|------|----------|--------|
| `settings.py.example` | Шаблон настроек Django/AWX | ✅ В репозитории |
| `nginx.conf` | nginx конфиг (порт 8052) | ✅ В репозитории |
| `uwsgi.ini` | uWSGI конфиг (порт 8050) | ✅ В репозитории |
| `settings.py` | Реальные настройки с паролями | ❌ НЕ коммитить |
| `SECRET_KEY` | Секретный ключ Django | ❌ НЕ коммитить |

## Быстрая настройка на сервере

```bash
# 1. Создать директорию
sudo mkdir -p /opt/iva-mail-ansible/tower
sudo chown user:user /opt/iva-mail-ansible/tower

# 2. Скопировать файлы конфигурации
cp tower/nginx.conf /opt/iva-mail-ansible/tower/
cp tower/uwsgi.ini /opt/iva-mail-ansible/tower/
cp tower/settings.py.example /opt/iva-mail-ansible/tower/settings.py

# 3. Отредактировать пароли в settings.py
nano /opt/iva-mail-ansible/tower/settings.py

# 4. Сгенерировать SECRET_KEY
openssl rand -base64 48 > /opt/iva-mail-ansible/tower/SECRET_KEY
chmod 600 /opt/iva-mail-ansible/tower/SECRET_KEY

# 5. Запустить контейнеры
cd /opt/iva-mail-ansible
docker compose --env-file .env.awx -f docker-compose.awx.yml up -d

# 6. Миграции БД (только первый раз)
docker exec awx_web awx-manage migrate --noinput

# 7. Установить пароль admin
echo "NewPassword123!" | docker exec -i awx_web awx-manage changepassword admin
```

## Доступ к AWX

После запуска:
- URL: http://10.3.6.100:8080
- Логин: admin
- Пароль: (задаётся в шаге 7)
