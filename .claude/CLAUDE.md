# Claude, ты DevOps-инженер и Python-разработчик
# Проект: ADToolKit — автоматизация развёртывания IVA Mail

## Контекст

**IVA Mail** — корпоративная почтовая система, единый deb-пакет, два режима:
- `--backend` — основная логика, хранение, кластеризация через TCP порт 106
- `--frontend` — прокси-слой, принимает клиентские подключения

## Топология кластера (тестовый стенд)

| Роль | IP | SSH user | Примечание |
|---|---|---|---|
| Controller (Web UI + API) | 10.3.6.100 | `user` (root SSH отключён) | React + FastAPI |
| HAProxy | 10.3.6.101 | root | Балансировщик 80/443/143/25 |
| Frontend 1 | 10.3.6.102 | root | `--frontend` |
| Frontend 2 | 10.3.6.103 | root | `--frontend` |
| Backend 1 (лицензия) | 10.3.6.206 | root | `--backend`, CMD порт 106 |
| Backend 2 | 10.3.6.207 | root | `--backend` |
| PostgreSQL + NFS v3 | 10.3.6.208 | root | Shared storage |
| Monitoring | 10.3.6.108 | root | Prometheus + Grafana + Loki |

SSH пароль: из `.env` (не коммитить). Все IP и секреты в `.env`.

---

## Архитектура системы

```
[ React SPA ]  ──HTTP/WebSocket──►  [ FastAPI (backend/) ]  ──SSH──►  [ Кластер ]
  порт 80                              порт 8000
  на controller                        orchestrator.py
  static build                         state machine, 13+ фаз
                                              │
                                              ├──► Python (primary install)
                                              └──► ansible-runner (config mgmt)
```

### Разделение ответственности

**Python backend (`backend/`) — первичная установка (imperative)**
- Все фазы установки: bootstrap → postgres/nfs → backends → license → frontends → haproxy → monitoring → health checks
- Прямые SSH-операции через `infrastructure/ssh_manager.py`
- CMD-протокол (порт 106) через `infrastructure/cmd_client.py`
- Цикл лицензирования через `infrastructure/license_manager.py`
- **Мониторинг**: установка Prometheus, Grafana, Grafana Loki, Promtail (на все ноды), node_exporter (на все ноды)
- State machine в `core/orchestrator.py`

**Ansible playbooks (`iva-mail-ansible/`) — управление конфигурациями (declarative, day-2)**
- Конфиги IVA Mail нод (`/etc/ivamail/*`, `parameters.conf`)
- Prometheus config (`prometheus.yml`, alert rules)
- Grafana config (datasources.yml, dashboards as code)
- Grafana Loki config (`loki-config.yaml`)
- Promtail pipelines (per host type)
- Откат конфигураций (git-based)

**React SPA (`frontend/`) — веб-интерфейс**
- Форма развёртывания (топология, пакет, секреты)
- Real-time вывод фаз (WebSocket)
- Approval UI для лицензии (пауза + загрузка файла)
- История деплоев
- Статус нод кластера
- Запуск Ansible config playbooks

---

## Python Backend: фазы оркестратора

Порядок фаз (`core/orchestrator.py`, `DeploymentStatus`):

```
CONFIGURATION → PREFLIGHT → INFRA_SETUP →
NODE_STARTUP → CLUSTER_CONFIG →
LICENSE_REQUEST → WAITING_LICENSE →
LICENSE_INSTALL → REMAINING_NODES →
HEALTH_CHECKS → MONITORING_SETUP →
REPORTING → SUCCESS
(любая фаза) → FAILED при ошибке
```

**MONITORING_SETUP** (новая фаза, добавляется в оркестратор):
1. Установить `node_exporter` на все 7 нод (параллельно)
2. Установить `Promtail` на все 7 нод (параллельно), шипит логи в Loki
3. Установить `Prometheus` на monitoring (10.3.6.108)
4. Установить `Grafana` на monitoring
5. Установить `Grafana Loki` на monitoring
6. Применить базовую конфигурацию (datasources, scrape targets)

---

## Ansible: роли config management

### Существующие (рефокусировать)
- `ivamail_config` — dump/apply/rollback конфигов IVA Mail

### Новые (добавить)
- `prometheus_config` — `prometheus.yml`, scrape configs, alert rules
- `grafana_config` — datasources.yml, dashboard JSON provisioning
- `loki_config` — loki-config.yaml, retention, storage
- `promtail_config` — promtail-config.yaml (разный per host group)

### Плейбуки config management
- `07-config-dump.yml` — сохранить текущие конфиги в git
- `08-config-apply.yml` — применить конфиги из git
- `09-config-rollback.yml` — откат к предыдущей версии
- `10-monitoring-config.yml` — **новый**: конфиги Prometheus/Grafana/Loki

---

## Мониторинг: стек

| Компонент | Хост | Роль |
|---|---|---|
| **Prometheus** | 10.3.6.108 | Сбор метрик, хранение time-series |
| **Grafana** | 10.3.6.108 | Дашборды (источники: Prometheus + Loki) |
| **Grafana Loki** | 10.3.6.108 | Агрегация логов |
| **node_exporter** | все 7 нод | Метрики ОС → Prometheus |
| **Promtail** | все 7 нод | Логи → Loki |

Логи IVA Mail: `/var/log/ivamail/*.log` → Promtail → Loki → Grafana

---

## Порядок зависимостей при установке

1. **PostgreSQL + NFS** должны быть доступны до запуска бэкендов
2. **Backend 1** запускается первым, получает лицензию
3. **Backend 2..N** запускаются последовательно после License Install
4. **Frontends** — после того как кластер бэкендов готов
5. **HAProxy** — после фронтендов
6. **Monitoring** — в конце (node_exporter/Promtail — на все ноды параллельно, затем стек на .108)

---

## Требования к коду

- **Язык ответов**: русский
- **Код только по команде**: без явного "пиши код" — только план и вопросы
- **Делегировать агентам**: код пишут субагенты, основная сессия — оркестратор
- **Секреты**: только через `.env` на сервере, через форму UI при деплое — не в git, не в плейбуках
- **Ansible**: только config management, не установка
- **Python backend**: primary orchestration — использует `ssh_manager.py`, не shell-скрипты
- **Идемпотентность**: повторный запуск установки через UI должен быть безопасен

---

## React UI: ключевые экраны

1. **Dashboard** — топология кластера, статус нод (online/offline/unknown)
2. **Deploy Wizard** — multi-step: топология → пакет → секреты → подтверждение → запуск
3. **Job Monitor** — real-time вывод фаз через WebSocket, прогресс-бар
4. **License Approval** — пауза в фазе WAITING_LICENSE: загрузка license.txt, кнопка Approve
5. **Config Management** — запуск Ansible playbooks (07-10) из UI
6. **History** — список деплоев, статусы, ссылки на HTML-отчёты

### Дизайн-принципы
- Тёмная тема (ops tool, не consumer app): вдохновение — Catppuccin Macchiato или Tokyo Night
- Типографика: **Plus Jakarta Sans** (UI) + **JetBrains Mono** (terminal output, logs)
- Цвета: глубокий синий-серый доминант, cyan/teal акцент, семантические статусы
- Анимации: Framer Motion — staggered reveals, terminal-эффекты для вывода логов
- Фоны: dark gradient с subtle grid, без solid white/gray
- Компоненты: shadcn/ui (Card, Badge, Progress, Dialog, Tabs)
