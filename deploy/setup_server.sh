#!/usr/bin/env bash
# =============================================================================
# ADToolKit — Server Setup Script
# Запускается установщиком (installer.py) через SSH на целевом сервере.
# Поддерживает: Debian/Ubuntu и RHEL/CentOS/Rocky Linux
# =============================================================================
set -euo pipefail

APP_DIR="/opt/adtoolkit"
SERVICE_USER="adtoolkit"
DEPLOY_ARCHIVE="/tmp/adtoolkit-deploy.zip"
NGINX_SERVICE="nginx"

# ── Цвета для вывода ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERR]${NC}  $*" >&2; }

# ── Определить пакетный менеджер ──────────────────────────────────────────────
detect_os() {
    if command -v apt-get &>/dev/null; then
        PKG="apt"
    elif command -v yum &>/dev/null || command -v dnf &>/dev/null; then
        PKG="yum"
        command -v dnf &>/dev/null && PKG="dnf"
    else
        error "Не удалось определить пакетный менеджер"
        exit 1
    fi
    info "Пакетный менеджер: $PKG"
}

# ── Установить зависимости ────────────────────────────────────────────────────
install_deps() {
    info "Установка системных зависимостей..."

    if [ "$PKG" = "apt" ]; then
        DEBIAN_FRONTEND=noninteractive apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            python3 python3-pip python3-venv \
            nginx \
            ansible \
            git \
            unzip \
            curl
    else
        $PKG update -y -q
        $PKG install -y -q epel-release 2>/dev/null || true
        $PKG install -y -q \
            python3 python3-pip \
            nginx \
            ansible \
            git \
            unzip \
            curl
        # python3-venv аналог
        $PKG install -y -q python3-virtualenv 2>/dev/null || pip3 install virtualenv -q
    fi

    success "Системные зависимости установлены"
}

# ── Создать системного пользователя ───────────────────────────────────────────
create_user() {
    if id -u "$SERVICE_USER" &>/dev/null; then
        info "Пользователь $SERVICE_USER уже существует"
    else
        info "Создание пользователя $SERVICE_USER..."
        useradd -r -m -d "$APP_DIR" -s /sbin/nologin "$SERVICE_USER"
        success "Пользователь $SERVICE_USER создан"
    fi
}

# ── Распаковать архив ─────────────────────────────────────────────────────────
extract_archive() {
    info "Распаковка архива в $APP_DIR..."
    mkdir -p "$APP_DIR"
    unzip -o "$DEPLOY_ARCHIVE" -d "$APP_DIR" > /dev/null
    chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
    chmod -R 755 "$APP_DIR"
    success "Архив распакован"
}

# ── Инициализировать config-store как отдельный git-репозиторий ──────────────
init_config_store() {
    # config-store — отдельный репозиторий, НЕ в составе ansible-проекта.
    # Путь совпадает с CONFIG_STORE_DIR в /opt/adtoolkit/.env
    STORE_DIR="/opt/ivamail-config-store"
    info "Инициализация config-store в $STORE_DIR..."

    mkdir -p "$STORE_DIR"
    chown -R "$SERVICE_USER:$SERVICE_USER" "$STORE_DIR"
    chmod 750 "$STORE_DIR"

    if [ ! -d "$STORE_DIR/.git" ]; then
        sudo -u "$SERVICE_USER" git -C "$STORE_DIR" init -q
        sudo -u "$SERVICE_USER" git -C "$STORE_DIR" config user.email "adtoolkit@local"
        sudo -u "$SERVICE_USER" git -C "$STORE_DIR" config user.name  "ADToolKit Ansible"
        # Создаём .gitkeep чтобы git log работал сразу
        sudo -u "$SERVICE_USER" touch "$STORE_DIR/.gitkeep"
        sudo -u "$SERVICE_USER" git -C "$STORE_DIR" add .gitkeep
        sudo -u "$SERVICE_USER" git -C "$STORE_DIR" commit -m "chore: init ivamail config-store" -q
        success "Config-store инициализирован: $STORE_DIR"
    else
        info "Config-store уже существует: $STORE_DIR"
    fi
}

# ── Создать .env файл с переменными окружения ─────────────────────────────────
create_env_file() {
    ENV_FILE="$APP_DIR/.env"
    if [ ! -f "$ENV_FILE" ]; then
        info "Создание $ENV_FILE..."
        cat > "$ENV_FILE" <<EOF
# ADToolKit backend environment variables
# Создан автоматически setup_server.sh

# Путь к git-репозиторию конфигураций IVA Mail (config-store)
CONFIG_STORE_DIR=/opt/ivamail-config-store

# Путь к Ansible-проекту (для ansible-playbook)
ANSIBLE_PROJECT_DIR=$APP_DIR/iva-mail-ansible
EOF
        chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"
        chmod 640 "$ENV_FILE"
        success ".env создан: $ENV_FILE"
    else
        # Добавить CONFIG_STORE_DIR если отсутствует
        if ! grep -q "CONFIG_STORE_DIR" "$ENV_FILE"; then
            echo "" >> "$ENV_FILE"
            echo "CONFIG_STORE_DIR=/opt/ivamail-config-store" >> "$ENV_FILE"
            info "CONFIG_STORE_DIR добавлен в существующий $ENV_FILE"
        else
            info ".env уже содержит CONFIG_STORE_DIR"
        fi
    fi
}

# ── Python virtualenv + pip deps ──────────────────────────────────────────────
install_python_deps() {
    info "Создание Python virtualenv..."
    sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/venv" 2>/dev/null || \
        sudo -u "$SERVICE_USER" virtualenv "$APP_DIR/venv"

    info "Установка Python зависимостей (может занять 1-2 минуты)..."
    sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip -q
    sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install \
        -r "$APP_DIR/backend/requirements.txt" -q

    success "Python зависимости установлены"
}

# ── Настройка nginx ───────────────────────────────────────────────────────────
configure_nginx() {
    info "Настройка nginx..."

    if [ "$PKG" = "apt" ]; then
        # Debian/Ubuntu: sites-available / sites-enabled
        cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/adtoolkit
        ln -sf /etc/nginx/sites-available/adtoolkit /etc/nginx/sites-enabled/adtoolkit
        rm -f /etc/nginx/sites-enabled/default
    else
        # RHEL/CentOS: conf.d/
        cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/conf.d/adtoolkit.conf
        # Убрать дефолтный server block если есть
        sed -i 's/^[[:space:]]*server {/# server {/' /etc/nginx/nginx.conf 2>/dev/null || true
    fi

    nginx -t && success "Конфигурация nginx валидна" || { error "Ошибка конфигурации nginx"; exit 1; }
}

# ── Настройка systemd ─────────────────────────────────────────────────────────
configure_systemd() {
    info "Настройка systemd сервиса..."
    cp "$APP_DIR/deploy/adtoolkit-backend.service" /etc/systemd/system/adtoolkit-backend.service
    systemctl daemon-reload
    systemctl enable adtoolkit-backend
    systemctl enable "$NGINX_SERVICE"
    success "systemd сервисы настроены"
}

# ── Запустить сервисы ─────────────────────────────────────────────────────────
start_services() {
    info "Запуск сервисов..."

    systemctl restart adtoolkit-backend
    sleep 2
    systemctl is-active --quiet adtoolkit-backend && \
        success "adtoolkit-backend запущен" || \
        { error "adtoolkit-backend не запустился"; journalctl -u adtoolkit-backend --no-pager -n 20; exit 1; }

    systemctl restart "$NGINX_SERVICE"
    sleep 1
    systemctl is-active --quiet "$NGINX_SERVICE" && \
        success "nginx запущен" || \
        { error "nginx не запустился"; journalctl -u nginx --no-pager -n 20; exit 1; }
}

# ── Health check ──────────────────────────────────────────────────────────────
health_check() {
    info "Проверка работоспособности API..."
    sleep 3
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/deployment/status 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "404" ]; then
        success "API отвечает (HTTP $HTTP_CODE)"
    else
        warn "API вернул HTTP $HTTP_CODE — проверьте логи: journalctl -u adtoolkit-backend -f"
    fi
}

# ── Итог ─────────────────────────────────────────────────────────────────────
print_summary() {
    SERVER_IP=$(hostname -I | awk '{print $1}')
    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║   ADToolKit успешно установлен!        ║${NC}"
    echo -e "${GREEN}╠════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║${NC}  Web UI:  ${BLUE}http://$SERVER_IP${NC}"
    echo -e "${GREEN}║${NC}  API:     ${BLUE}http://$SERVER_IP/api${NC}"
    echo -e "${GREEN}║${NC}  Логи:    journalctl -u adtoolkit-backend -f"
    echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    echo ""
    info "=== ADToolKit Server Setup ==="
    info "Начало установки: $(date)"
    echo ""

    detect_os
    install_deps
    create_user
    extract_archive
    init_config_store
    create_env_file
    install_python_deps
    configure_nginx
    configure_systemd
    start_services
    health_check
    print_summary

    # Очистка
    rm -f "$DEPLOY_ARCHIVE"
}

main "$@"
