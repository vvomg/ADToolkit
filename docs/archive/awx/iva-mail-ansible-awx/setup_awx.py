#!/usr/bin/env python3
"""
AWX 23.9.0 Pre-configuration Script
Настраивает AWX через REST API:
  - Организация IVA Mail
  - Credential Types (IVA Mail CMD, PostgreSQL Admin)
  - SSH Credential placeholder (IVA Mail SSH Key)
  - Project (manual, local_path: iva-mail-ansible)
  - Inventory (IVA Mail Dynamic)
  - 13 Job Templates (с полными Survey specs для каждого)
  - Workflow: IVA Mail Full Deployment

Использование:
  python3 setup_awx.py [--url URL] [--user USER] [--password PASS]
                       [--wait-timeout SEC] [--dry-run] [--verbose]

Переменные окружения (используются как умолчания):
  AWX_URL             — URL AWX (default: http://localhost:8080)
  AWX_ADMIN_USER      — имя пользователя (default: admin)
  AWX_ADMIN_PASSWORD  — пароль (default: AwxAdmin123!)
"""

import argparse
import os
import requests
import json
import sys
import time

# ---------------------------------------------------------------------------
# CLI аргументы
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="AWX 23.9.0 Pre-configuration for IVA Mail",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("AWX_URL", "http://localhost:8080"),
        help="URL AWX (default: %(default)s)"
    )
    parser.add_argument(
        "--user",
        default=os.environ.get("AWX_ADMIN_USER", "admin"),
        help="Имя администратора AWX (default: %(default)s)"
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("AWX_ADMIN_PASSWORD", "AwxAdmin123!"),
        help="Пароль администратора AWX (default: из AWX_ADMIN_PASSWORD или AwxAdmin123!)"
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=300,
        metavar="SEC",
        help="Таймаут ожидания AWX перед началом работы, сек (default: 300)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать что будет создано, не изменять AWX"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Подробный вывод HTTP-запросов"
    )
    return parser.parse_args()


ARGS = parse_args()

AWX_URL = ARGS.url.rstrip("/")
AUTH    = (ARGS.user, ARGS.password)
HEADERS = {"Content-Type": "application/json"}
DRY_RUN = ARGS.dry_run
VERBOSE = ARGS.verbose

if DRY_RUN:
    print("\n  *** РЕЖИМ DRY-RUN: изменения в AWX не производятся ***\n")

# ---------------------------------------------------------------------------
# Счётчики для итогового отчёта
# ---------------------------------------------------------------------------
_stats = {
    "organizations":    {"created": 0, "exists": 0},
    "credential_types": {"created": 0, "exists": 0},
    "credentials":      {"created": 0, "exists": 0},
    "projects":         {"created": 0, "exists": 0},
    "inventories":      {"created": 0, "exists": 0},
    "job_templates":    {"created": 0, "exists": 0},
    "workflows":        {"created": 0, "exists": 0},
}

# ---------------------------------------------------------------------------
# Ожидание готовности AWX
# ---------------------------------------------------------------------------

def wait_for_awx(timeout: int = 300) -> None:
    """Ожидать доступности AWX API перед началом работы."""
    print(f"\n  Ожидание AWX API (таймаут {timeout}s)...", end="", flush=True)
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            r = requests.get(f"{AWX_URL}/api/v2/ping/", timeout=5)
            if r.status_code == 200:
                print(" OK")
                return
            last_error = f"http {r.status_code}"
        except requests.exceptions.ConnectionError as e:
            last_error = "connection refused"
        except requests.exceptions.Timeout:
            last_error = "timeout"
        print(".", end="", flush=True)
        time.sleep(5)
    print(f"\n  ОШИБКА: AWX недоступен после {timeout}s (последняя ошибка: {last_error})")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def api_get(path, params=None):
    if VERBOSE:
        print(f"  GET {path} params={params}")
    r = requests.get(f"{AWX_URL}{path}", auth=AUTH, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def api_post(path, data):
    if DRY_RUN:
        return {"id": 0, "_dry_run": True}
    if VERBOSE:
        print(f"  POST {path} data={json.dumps(data)[:200]}")
    r = requests.post(f"{AWX_URL}{path}", auth=AUTH, headers=HEADERS,
                      data=json.dumps(data), timeout=30)
    if r.status_code in (200, 201):
        return r.json()
    # 400 may mean already exists with same name — try to return existing
    print(f"  POST {path} -> {r.status_code}: {r.text[:300]}")
    r.raise_for_status()

def find_or_create(list_path, create_path, match_field, match_value, data,
                   label=None, stat_key=None):
    """Return existing object id or create new one."""
    t0 = time.time()
    label_str = label or match_value

    if DRY_RUN:
        print(f"  [DRY-RUN] Would create: {label_str}")
        if stat_key and stat_key in _stats:
            _stats[stat_key]["created"] += 1
        return None

    existing = api_get(list_path, params={"page_size": 200})
    for item in existing.get("results", []):
        if item.get(match_field) == match_value:
            iid = item["id"]
            elapsed = time.time() - t0
            print(f"  EXISTS   {label_str} (id={iid}, {elapsed:.1f}s)")
            if stat_key and stat_key in _stats:
                _stats[stat_key]["exists"] += 1
            return iid

    created = api_post(create_path, data)
    iid = created["id"]
    elapsed = time.time() - t0
    print(f"  CREATED  {label_str} (id={iid}, {elapsed:.1f}s)")
    if stat_key and stat_key in _stats:
        _stats[stat_key]["created"] += 1
    return iid

def attach_credential(jt_id, cred_id):
    """Attach credential to job template via dis-associate / associate."""
    if DRY_RUN:
        return
    url = f"/api/v2/job_templates/{jt_id}/credentials/"
    r = requests.post(f"{AWX_URL}{url}", auth=AUTH, headers=HEADERS,
                      data=json.dumps({"id": cred_id}), timeout=30)
    if r.status_code in (200, 201, 204):
        return
    # Already attached returns 400 with "already exists" — treat as OK
    if r.status_code == 400 and "already" in r.text.lower():
        return
    print(f"    attach cred {cred_id} to JT {jt_id} -> {r.status_code}: {r.text[:200]}")

def add_survey(jt_id, spec):
    if DRY_RUN:
        print(f"  [DRY-RUN] Would set survey spec for JT {jt_id}")
        return
    r = requests.post(f"{AWX_URL}/api/v2/job_templates/{jt_id}/survey_spec/",
                      auth=AUTH, headers=HEADERS, data=json.dumps(spec), timeout=30)
    if r.status_code in (200, 201, 204):
        print(f"  Survey spec set for JT {jt_id}")
    else:
        print(f"  Survey set failed {r.status_code}: {r.text[:200]}")

def find_id(list_path, name):
    """Return id of first object matching name, or None."""
    res = api_get(list_path, params={"name": name, "page_size": 10})
    for item in res.get("results", []):
        if item["name"] == name:
            return item["id"]
    return None

def wait_project_sync(project_id, timeout=120):
    """Wait for AWX project to reach 'successful' status."""
    print(f"  Waiting for project {project_id} to sync...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = api_get(f"/api/v2/projects/{project_id}/")
        status = p.get("status", "")
        if status == "successful":
            print(" OK")
            return True
        if status in ("failed", "error", "canceled"):
            print(f" FAILED ({status})")
            return False
        print(".", end="", flush=True)
        time.sleep(5)
    print(" TIMEOUT")
    return False

# ---------------------------------------------------------------------------
# Ожидание готовности AWX перед началом работы
# ---------------------------------------------------------------------------
_script_start = time.time()
wait_for_awx(timeout=ARGS.wait_timeout)

# ---------------------------------------------------------------------------
# Step 1: Organization
# ---------------------------------------------------------------------------
print("\n=== Step 1: Organization ===")
org_id = find_or_create(
    "/api/v2/organizations/", "/api/v2/organizations/",
    "name", "IVA Mail",
    {"name": "IVA Mail", "description": "Автоматизация развёртывания IVA Mail кластера"},
    label="IVA Mail",
    stat_key="organizations"
)

# ---------------------------------------------------------------------------
# Step 2: Credential Types
# ---------------------------------------------------------------------------
print("\n=== Step 2: Credential Types ===")

ct_ivamail_cmd_id = find_or_create(
    "/api/v2/credential_types/", "/api/v2/credential_types/",
    "name", "IVA Mail CMD",
    {
        "name": "IVA Mail CMD",
        "description": "Учётные данные для CMD-протокола IVA Mail (порт 106)",
        "kind": "cloud",
        "inputs": {
            "fields": [
                {"id": "cmd_user", "type": "string", "label": "CMD User"},
                {"id": "cmd_password", "type": "string", "label": "CMD Password", "secret": True}
            ],
            "required": ["cmd_user", "cmd_password"]
        },
        "injectors": {
            "env": {
                "IVAMAIL_CMD_USER": "{{ cmd_user }}",
                "IVAMAIL_CMD_PASSWORD": "{{ cmd_password }}"
            }
        }
    },
    label="IVA Mail CMD",
    stat_key="credential_types"
)

ct_pg_admin_id = find_or_create(
    "/api/v2/credential_types/", "/api/v2/credential_types/",
    "name", "PostgreSQL Admin",
    {
        "name": "PostgreSQL Admin",
        "description": "Административный доступ к PostgreSQL для развёртывания IVA Mail",
        "kind": "cloud",
        "inputs": {
            "fields": [
                {"id": "pg_admin_user", "type": "string", "label": "PostgreSQL Admin User"},
                {"id": "pg_admin_password", "type": "string",
                 "label": "PostgreSQL Admin Password", "secret": True}
            ],
            "required": ["pg_admin_user", "pg_admin_password"]
        },
        "injectors": {
            "env": {
                "PG_ADMIN_USER": "{{ pg_admin_user }}",
                "PG_ADMIN_PASSWORD": "{{ pg_admin_password }}"
            }
        }
    },
    label="PostgreSQL Admin",
    stat_key="credential_types"
)

# ---------------------------------------------------------------------------
# Step 3: SSH Credential placeholder (Machine type)
# ---------------------------------------------------------------------------
print("\n=== Step 3: SSH Credential (placeholder) ===")

# Built-in Machine credential type id
machine_ct = api_get("/api/v2/credential_types/", params={"namespace": "ssh", "page_size": 50})
machine_ct_id = None
for ct in machine_ct.get("results", []):
    if ct.get("kind") == "ssh":
        machine_ct_id = ct["id"]
        break
if machine_ct_id is None:
    # fallback — search by name
    for ct in api_get("/api/v2/credential_types/",
                      params={"page_size": 200}).get("results", []):
        if ct.get("name") == "Machine":
            machine_ct_id = ct["id"]
            break
print(f"  Machine credential type id: {machine_ct_id}")

ssh_cred_id = find_or_create(
    "/api/v2/credentials/", "/api/v2/credentials/",
    "name", "IVA Mail SSH Key",
    {
        "name": "IVA Mail SSH Key",
        "description": "SSH доступ к узлам IVA Mail (placeholder — замените ключ!)",
        "organization": org_id,
        "credential_type": machine_ct_id,
        "inputs": {
            "username": "root",
            "password": "PLACEHOLDER_CHANGE_ME"
        }
    },
    label="IVA Mail SSH Key",
    stat_key="credentials"
)

# Placeholder credentials for IVA Mail CMD
ivamail_cmd_cred_id = find_or_create(
    "/api/v2/credentials/", "/api/v2/credentials/",
    "name", "IVA Mail CMD",
    {
        "name": "IVA Mail CMD",
        "description": "CMD-протокол IVA Mail (placeholder — заполните данные!)",
        "organization": org_id,
        "credential_type": ct_ivamail_cmd_id,
        "inputs": {
            "cmd_user": "admin",
            "cmd_password": "PLACEHOLDER_CHANGE_ME"
        }
    },
    label="IVA Mail CMD credential",
    stat_key="credentials"
)

# Placeholder credentials for PostgreSQL Admin
pg_admin_cred_id = find_or_create(
    "/api/v2/credentials/", "/api/v2/credentials/",
    "name", "PostgreSQL Admin",
    {
        "name": "PostgreSQL Admin",
        "description": "PostgreSQL Admin (placeholder — заполните данные!)",
        "organization": org_id,
        "credential_type": ct_pg_admin_id,
        "inputs": {
            "pg_admin_user": "postgres",
            "pg_admin_password": "PLACEHOLDER_CHANGE_ME"
        }
    },
    label="PostgreSQL Admin credential",
    stat_key="credentials"
)

# ---------------------------------------------------------------------------
# Step 4: Project (manual, local_path: iva-mail-ansible)
# AWX requires the directory to exist in /var/lib/awx/projects/ before creating
# a manual project via API.
# ---------------------------------------------------------------------------
print("\n=== Step 4: Project ===")
import subprocess as _sp
_proj_dir = "/var/lib/awx/projects/iva-mail-ansible"
# 1. Ensure directory exists in AWX projects volume
_mk = _sp.run(
    ["bash", "-c", f"docker exec awx_web mkdir -p {_proj_dir} 2>/dev/null || "
                   f"mkdir -p {_proj_dir} 2>/dev/null || true"],
    capture_output=True, text=True
)
print(f"  Ensure project dir: {_proj_dir} ({_mk.returncode})")
# 2. Copy playbooks from project source into AWX projects volume
_src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # iva-mail-ansible/ root
_copy_cmd = (
    f"for f in {_src}/*.yml; do "
    f"  docker cp \"$f\" awx_web:{_proj_dir}/ 2>/dev/null || true; "
    f"done; "
    f"for d in roles inventory group_vars host_vars; do "
    f"  [ -d {_src}/$d ] && docker cp {_src}/$d awx_web:{_proj_dir}/ 2>/dev/null || true; "
    f"done"
)
_cp = _sp.run(["bash", "-c", _copy_cmd], capture_output=True, text=True)
print(f"  Copy playbooks to AWX volume ({_cp.returncode})")
project_id = find_or_create(
    "/api/v2/projects/", "/api/v2/projects/",
    "name", "iva-mail-ansible",
    {
        "name": "iva-mail-ansible",
        "description": "Ansible-плейбуки для развёртывания IVA Mail кластера",
        "organization": org_id,
        "scm_type": "",          # manual project
        "local_path": "iva-mail-ansible"
    },
    label="iva-mail-ansible",
    stat_key="projects"
)

# ---------------------------------------------------------------------------
# Step 5: Inventory
# ---------------------------------------------------------------------------
print("\n=== Step 5: Inventory ===")
inventory_id = find_or_create(
    "/api/v2/inventories/", "/api/v2/inventories/",
    "name", "IVA Mail Dynamic",
    {
        "name": "IVA Mail Dynamic",
        "description": "Динамический инвентарий — хосты добавляются через add_host из Survey",
        "organization": org_id
    },
    label="IVA Mail Dynamic",
    stat_key="inventories"
)

# ---------------------------------------------------------------------------
# Step 6: Job Templates
# ---------------------------------------------------------------------------
print("\n=== Step 6: Job Templates ===")

# Map template name -> list of credential objects {name, id}
CRED_MAP = {
    "IVA Mail SSH Key":  ssh_cred_id,
    "PostgreSQL Admin":  pg_admin_cred_id,
    "IVA Mail CMD":      ivamail_cmd_cred_id,
}

JOB_TEMPLATES = [
    {
        "name": "00-Bootstrap",
        "description": "Начальная настройка всех узлов IVA Mail кластера",
        "playbook": "00-bootstrap.yml",
        "credentials": ["IVA Mail SSH Key"],
        "survey_enabled": True,
        "ask_variables_on_launch": False,
    },
    {
        "name": "01-Postgres-NFS",
        "description": "Развёртывание PostgreSQL и NFS на storage-узле (10.3.6.128)",
        "playbook": "01-postgres-nfs.yml",
        "credentials": ["IVA Mail SSH Key", "PostgreSQL Admin"],
        "survey_enabled": True,
        "ask_variables_on_launch": False,
    },
    {
        "name": "02-Backends-Install",
        "description": "Установка IVA Mail на бэкенды, DB setup, cluster config",
        "playbook": "02-backends-install.yml",
        "credentials": ["IVA Mail SSH Key", "PostgreSQL Admin", "IVA Mail CMD"],
        "survey_enabled": True,
        "ask_variables_on_launch": False,
    },
    {
        "name": "02-License-Request",
        "description": "Генерация запроса лицензии IVA Mail (LicenseRequest на be1)",
        "playbook": "02-license-request.yml",
        "credentials": ["IVA Mail SSH Key", "IVA Mail CMD"],
        "survey_enabled": True,
        "ask_variables_on_launch": False,
    },
    {
        "name": "02-License-Install-And-Restart",
        "description": "Установка лицензии + последовательный перезапуск бэкендов",
        "playbook": "02-license-install-and-restart.yml",
        "credentials": ["IVA Mail SSH Key", "IVA Mail CMD"],
        "survey_enabled": True,
        "ask_variables_on_launch": False,
    },
    {
        "name": "03-Frontends",
        "description": "Установка и настройка фронтендов IVA Mail",
        "playbook": "03-frontends.yml",
        "credentials": ["IVA Mail SSH Key", "IVA Mail CMD"],
        "survey_enabled": True,
        "ask_variables_on_launch": False,
    },
    {
        "name": "04-HAProxy",
        "description": "Установка и конфигурация HAProxy",
        "playbook": "04-haproxy.yml",
        "credentials": ["IVA Mail SSH Key"],
        "survey_enabled": True,
        "ask_variables_on_launch": False,
    },
    {
        "name": "05-Monitoring",
        "description": "Установка Prometheus, Grafana, Graylog, node_exporter",
        "playbook": "05-monitoring.yml",
        "credentials": ["IVA Mail SSH Key"],
        "survey_enabled": True,
        "ask_variables_on_launch": False,
    },
    {
        "name": "06-Backup-Config",
        "description": "Настройка pg_dump, rsync NFS, git-репозиторий конфигураций",
        "playbook": "06-backup-config.yml",
        "credentials": ["IVA Mail SSH Key"],
        "survey_enabled": True,
        "ask_variables_on_launch": False,
    },
    {
        "name": "07-Config-Dump",
        "description": "Снимок конфигурации IVA Mail (ModuleReadConfig) → git-репозиторий",
        "playbook": "07-config-dump.yml",
        "credentials": ["IVA Mail SSH Key", "IVA Mail CMD"],
        "survey_enabled": True,
        "ask_variables_on_launch": False,
    },
    {
        "name": "08-Config-Apply",
        "description": "Применить конфигурацию IVA Mail из JSON-файлов (ModuleUpdateConfig)",
        "playbook": "08-config-apply.yml",
        "credentials": ["IVA Mail SSH Key", "IVA Mail CMD"],
        "survey_enabled": True,
        "ask_variables_on_launch": False,
    },
    {
        "name": "09-Config-Rollback",
        "description": "Откат конфигурации IVA Mail к git-снимку + опциональный перезапуск",
        "playbook": "09-config-rollback.yml",
        "credentials": ["IVA Mail SSH Key", "IVA Mail CMD"],
        "survey_enabled": True,
        "ask_variables_on_launch": False,
    },
    {
        "name": "Health-Check",
        "description": "Проверка состояния всех узлов IVA Mail кластера",
        "playbook": "health-check.yml",
        "credentials": ["IVA Mail SSH Key"],
        "survey_enabled": True,
        "ask_variables_on_launch": False,
    },
]

jt_ids = {}
for tpl in JOB_TEMPLATES:
    name = tpl["name"]
    body = {
        "name": name,
        "description": tpl["description"],
        "organization": org_id,
        "project": project_id,
        "playbook": tpl["playbook"],
        "inventory": inventory_id,
        "job_type": "run",
        "verbosity": 1,
        "survey_enabled": tpl["survey_enabled"],
        "ask_variables_on_launch": tpl["ask_variables_on_launch"],
    }
    jt_id = find_or_create(
        "/api/v2/job_templates/", "/api/v2/job_templates/",
        "name", name, body, label=name, stat_key="job_templates"
    )
    jt_ids[name] = jt_id

    # Attach credentials
    for cred_name in tpl["credentials"]:
        cred_id = CRED_MAP.get(cred_name)
        if cred_id:
            attach_credential(jt_id, cred_id)
            print(f"    Attached credential '{cred_name}' to '{name}'")
        else:
            print(f"    WARNING: credential '{cred_name}' not found in CRED_MAP")

# ---------------------------------------------------------------------------
# Step 7: Survey specs for ALL 13 Job Templates
# ---------------------------------------------------------------------------
print("\n=== Step 7: Survey specs for all Job Templates ===")

# ── Общие вопросы, которые переиспользуются в нескольких шаблонах ─────────
Q_BACKEND_HOSTS = {
    "question_name": "Backend Hosts",
    "question_description": "IP-адреса бэкендов через запятую (например: 10.3.6.126,10.3.6.127)",
    "variable": "backend_hosts",
    "type": "text",
    "required": True,
    "default": "10.3.6.126,10.3.6.127",
    "min": 7,
    "max": 1024,
}
Q_FRONTEND_HOSTS = {
    "question_name": "Frontend Hosts",
    "question_description": "IP-адреса фронтендов через запятую (например: 10.3.6.102,10.3.6.103)",
    "variable": "frontend_hosts",
    "type": "text",
    "required": True,
    "default": "10.3.6.102,10.3.6.103",
    "min": 7,
    "max": 1024,
}
Q_HAPROXY_HOSTS = {
    "question_name": "HAProxy Host",
    "question_description": "IP-адрес сервера HAProxy (например: 10.3.6.101)",
    "variable": "haproxy_hosts",
    "type": "text",
    "required": True,
    "default": "10.3.6.101",
    "min": 7,
    "max": 255,
}
Q_STORAGE_HOST = {
    "question_name": "Storage Host",
    "question_description": "IP-адрес сервера PostgreSQL+NFS (например: 10.3.6.128)",
    "variable": "storage_host",
    "type": "text",
    "required": True,
    "default": "10.3.6.128",
    "min": 7,
    "max": 255,
}
Q_MONITORING_HOST = {
    "question_name": "Monitoring Host",
    "question_description": "IP-адрес сервера мониторинга (например: 10.3.6.108)",
    "variable": "monitoring_host",
    "type": "text",
    "required": True,
    "default": "10.3.6.108",
    "min": 7,
    "max": 255,
}
Q_CMD_PORT = {
    "question_name": "CMD Port",
    "question_description": "TCP-порт CMD-протокола IVA Mail (обычно 106)",
    "variable": "cmd_port",
    "type": "integer",
    "required": False,
    "default": 106,
    "min": 1,
    "max": 65535,
}
Q_NODE_DELAY = {
    "question_name": "Node Startup Delay (seconds)",
    "question_description": "Задержка между последовательными запусками узлов кластера",
    "variable": "node_startup_delay",
    "type": "integer",
    "required": False,
    "default": 10,
    "min": 0,
    "max": 300,
}
Q_PKG_SOURCE_TYPE = {
    "question_name": "Package Source Type",
    "question_description": "Способ доставки deb-пакета IVA Mail на хосты",
    "variable": "package_source_type",
    "type": "multiplechoice",
    "required": True,
    "default": "url",
    "choices": "url\ncontroller\nserver_same\nserver_per_node",
}
Q_PKG_URL = {
    "question_name": "Package URL",
    "question_description": "URL пакета (используется при package_source_type=url)",
    "variable": "package_url",
    "type": "text",
    "required": False,
    "default": "",
    "min": 0,
    "max": 2048,
}
Q_PKG_CONTROLLER_PATH = {
    "question_name": "Package Controller Path",
    "question_description": "Путь к .deb файлу на контроллере AWX (при package_source_type=controller)",
    "variable": "package_controller_path",
    "type": "text",
    "required": False,
    "default": "/opt/ivamail/packages/ivamail.deb",
    "min": 0,
    "max": 2048,
}
Q_PKG_SERVER_SAME = {
    "question_name": "Package Server Path (same on all nodes)",
    "question_description": "Единый путь к .deb файлу на всех нодах (при package_source_type=server_same)",
    "variable": "package_server_path",
    "type": "text",
    "required": False,
    "default": "/tmp/ivamail.deb",
    "min": 0,
    "max": 2048,
}
Q_PKG_PER_NODE_PATHS = {
    "question_name": "Package Per-Node Paths (YAML)",
    "question_description": (
        "Пути к .deb файлу для каждой ноды в формате YAML (при package_source_type=server_per_node):\n"
        "10.3.6.126: /tmp/ivamail-be1.deb\n"
        "10.3.6.127: /tmp/ivamail-be2.deb"
    ),
    "variable": "package_per_node_paths",
    "type": "textarea",
    "required": False,
    "default": "",
    "min": 0,
    "max": 4096,
}

# ── Survey specs по шаблонам ──────────────────────────────────────────────

SURVEY_SPECS = {}

# 00-Bootstrap: топология + пакет + задержка
SURVEY_SPECS["00-Bootstrap"] = {
    "name": "Bootstrap — параметры кластера IVA Mail",
    "description": "Топология узлов, способ доставки пакета и таймауты",
    "spec": [
        Q_BACKEND_HOSTS,
        Q_FRONTEND_HOSTS,
        Q_HAPROXY_HOSTS,
        Q_STORAGE_HOST,
        Q_MONITORING_HOST,
        Q_PKG_SOURCE_TYPE,
        Q_PKG_URL,
        Q_PKG_CONTROLLER_PATH,
        Q_PKG_SERVER_SAME,
        Q_PKG_PER_NODE_PATHS,
        Q_NODE_DELAY,
    ],
}

# 01-Postgres-NFS: storage host + backend IPs + имя БД + таймаут
SURVEY_SPECS["01-Postgres-NFS"] = {
    "name": "PostgreSQL + NFS — параметры",
    "description": "Storage-узел, IP бэкендов для pg_hba, имя БД",
    "spec": [
        Q_STORAGE_HOST,
        Q_BACKEND_HOSTS,
        {
            "question_name": "PostgreSQL Database Name",
            "question_description": "Имя базы данных IVA Mail (будет создана, если не существует)",
            "variable": "pg_database",
            "type": "text",
            "required": True,
            "default": "ivamail",
            "min": 1,
            "max": 63,
        },
        {
            "question_name": "PostgreSQL Wait Timeout (seconds)",
            "question_description": "Максимальное время ожидания готовности PostgreSQL к подключениям",
            "variable": "pg_wait_timeout",
            "type": "integer",
            "required": False,
            "default": 60,
            "min": 10,
            "max": 600,
        },
    ],
}

# 02-Backends-Install: бэкенды + пакет + DB + NFS + кластер + таймауты
SURVEY_SPECS["02-Backends-Install"] = {
    "name": "Backends Install — параметры установки",
    "description": "Бэкенды, storage, frontend-список, пакет, кластерные параметры",
    "spec": [
        Q_BACKEND_HOSTS,
        Q_STORAGE_HOST,
        Q_FRONTEND_HOSTS,
        Q_PKG_SOURCE_TYPE,
        Q_PKG_URL,
        Q_PKG_CONTROLLER_PATH,
        Q_PKG_SERVER_SAME,
        Q_PKG_PER_NODE_PATHS,
        Q_CMD_PORT,
        Q_NODE_DELAY,
        {
            "question_name": "PostgreSQL Database Name",
            "question_description": "Имя базы данных IVA Mail",
            "variable": "pg_database",
            "type": "text",
            "required": True,
            "default": "ivamail",
            "min": 1,
            "max": 63,
        },
        {
            "question_name": "NFS Mount Point",
            "question_description": "Точка монтирования NFS-шары на бэкендах",
            "variable": "nfs_mount_point",
            "type": "text",
            "required": False,
            "default": "/mnt/mailshare",
            "min": 3,
            "max": 255,
        },
        {
            "question_name": "PostgreSQL Wait Timeout (seconds)",
            "question_description": "Максимальное время ожидания готовности PostgreSQL",
            "variable": "pg_wait_timeout",
            "type": "integer",
            "required": False,
            "default": 60,
            "min": 10,
            "max": 600,
        },
    ],
}

# 02-License-Request: бэкенды + CMD + параметры лицензии
SURVEY_SPECS["02-License-Request"] = {
    "name": "License Request — параметры",
    "description": "Генерация файла запроса лицензии IVA Mail",
    "spec": [
        Q_BACKEND_HOSTS,
        Q_CMD_PORT,
        {
            "question_name": "Licensee Name (RU)",
            "question_description": "Название организации-лицензиата (кириллица)",
            "variable": "licensee_name",
            "type": "text",
            "required": True,
            "default": "ООО Организация",
            "min": 1,
            "max": 255,
        },
        {
            "question_name": "Licensee Name (EN)",
            "question_description": "Название организации-лицензиата (латиница)",
            "variable": "licensee_name_eng",
            "type": "text",
            "required": True,
            "default": "Organization LLC",
            "min": 1,
            "max": 255,
        },
        {
            "question_name": "Licensed Accounts",
            "question_description": "Максимальное количество учётных записей",
            "variable": "licensed_accounts",
            "type": "integer",
            "required": True,
            "default": 100,
            "min": 1,
            "max": 100000,
        },
        {
            "question_name": "Licensed Resources",
            "question_description": "Максимальное количество ресурсов (квота хранилища, ГБ)",
            "variable": "licensed_resources",
            "type": "integer",
            "required": True,
            "default": 500,
            "min": 1,
            "max": 1000000,
        },
    ],
}

# 02-License-Install-And-Restart: бэкенды + CMD + путь к license.txt + задержки
SURVEY_SPECS["02-License-Install-And-Restart"] = {
    "name": "License Install + Restart — параметры",
    "description": "Установка лицензии и последовательный перезапуск бэкендов",
    "spec": [
        Q_BACKEND_HOSTS,
        Q_CMD_PORT,
        {
            "question_name": "License File Path",
            "question_description": "Путь к файлу лицензии (.txt) на контроллере AWX",
            "variable": "license_file_path",
            "type": "text",
            "required": True,
            "default": "/opt/ivamail/license.txt",
            "min": 5,
            "max": 1024,
        },
        Q_NODE_DELAY,
        {
            "question_name": "CMD Port Wait Timeout (seconds)",
            "question_description": "Максимальное время ожидания доступности CMD-порта (106) после перезапуска узла",
            "variable": "cmd_port_wait_timeout",
            "type": "integer",
            "required": False,
            "default": 60,
            "min": 10,
            "max": 600,
        },
    ],
}

# 03-Frontends: фронтенды + бэкенды + пакет + CMD + задержка
SURVEY_SPECS["03-Frontends"] = {
    "name": "Frontends Install — параметры",
    "description": "Установка и настройка фронтендов IVA Mail",
    "spec": [
        Q_FRONTEND_HOSTS,
        Q_BACKEND_HOSTS,
        Q_PKG_SOURCE_TYPE,
        Q_PKG_URL,
        Q_PKG_CONTROLLER_PATH,
        Q_PKG_SERVER_SAME,
        Q_PKG_PER_NODE_PATHS,
        Q_CMD_PORT,
        Q_NODE_DELAY,
    ],
}

# 04-HAProxy: haproxy + фронтенды + порты
SURVEY_SPECS["04-HAProxy"] = {
    "name": "HAProxy — параметры",
    "description": "Балансировщик нагрузки на фронтенды IVA Mail",
    "spec": [
        Q_HAPROXY_HOSTS,
        Q_FRONTEND_HOSTS,
        {
            "question_name": "HTTP Port",
            "question_description": "Внешний HTTP-порт на HAProxy",
            "variable": "haproxy_http_port",
            "type": "integer",
            "required": False,
            "default": 80,
            "min": 1,
            "max": 65535,
        },
        {
            "question_name": "HTTPS Port",
            "question_description": "Внешний HTTPS-порт на HAProxy",
            "variable": "haproxy_https_port",
            "type": "integer",
            "required": False,
            "default": 443,
            "min": 1,
            "max": 65535,
        },
        {
            "question_name": "IMAP Port",
            "question_description": "IMAP-порт на HAProxy",
            "variable": "haproxy_imap_port",
            "type": "integer",
            "required": False,
            "default": 143,
            "min": 1,
            "max": 65535,
        },
        {
            "question_name": "SMTP Port",
            "question_description": "SMTP-порт на HAProxy",
            "variable": "haproxy_smtp_port",
            "type": "integer",
            "required": False,
            "default": 25,
            "min": 1,
            "max": 65535,
        },
    ],
}

# 05-Monitoring: monitoring host + список всех хостов + retention
SURVEY_SPECS["05-Monitoring"] = {
    "name": "Monitoring — параметры",
    "description": "Prometheus, Grafana, Graylog, node_exporter",
    "spec": [
        Q_MONITORING_HOST,
        {
            "question_name": "All Cluster Hosts",
            "question_description": "Все хосты кластера для node_exporter (через запятую)",
            "variable": "all_hosts",
            "type": "text",
            "required": True,
            "default": "10.3.6.126,10.3.6.127,10.3.6.102,10.3.6.103,10.3.6.101,10.3.6.128",
            "min": 7,
            "max": 2048,
        },
        {
            "question_name": "Prometheus Retention Days",
            "question_description": "Срок хранения метрик Prometheus (дни)",
            "variable": "prometheus_retention_days",
            "type": "integer",
            "required": False,
            "default": 30,
            "min": 1,
            "max": 365,
        },
        {
            "question_name": "Grafana Admin Password",
            "question_description": (
                "Пароль администратора Grafana. "
                "Если пусто — роль оставит пароль по умолчанию (admin). "
                "Используйте AWX Credentials для production-паролей."
            ),
            "variable": "grafana_admin_password",
            "type": "text",
            "required": False,
            "default": "admin",
            "min": 0,
            "max": 128,
        },
    ],
}

# 06-Backup-Config: storage + backup dest + расписание
SURVEY_SPECS["06-Backup-Config"] = {
    "name": "Backup Config — параметры",
    "description": "pg_dump, rsync NFS-шары, git-репозиторий конфигураций",
    "spec": [
        Q_STORAGE_HOST,
        {
            "question_name": "Backup Destination Host",
            "question_description": "IP-адрес сервера назначения резервных копий",
            "variable": "backup_dest_host",
            "type": "text",
            "required": True,
            "default": "10.3.6.108",
            "min": 7,
            "max": 255,
        },
        {
            "question_name": "Backup Destination Path",
            "question_description": "Путь на сервере назначения для хранения резервных копий",
            "variable": "backup_dest_path",
            "type": "text",
            "required": False,
            "default": "/var/backups/ivamail",
            "min": 3,
            "max": 1024,
        },
        {
            "question_name": "pg_dump Schedule (cron)",
            "question_description": "Расписание в формате cron для ежедневного pg_dump",
            "variable": "pgdump_schedule",
            "type": "text",
            "required": False,
            "default": "0 2 * * *",
            "min": 5,
            "max": 64,
        },
    ],
}

# 07-Config-Dump: бэкенды + CMD + путь вывода
SURVEY_SPECS["07-Config-Dump"] = {
    "name": "Config Dump — параметры",
    "description": "Снимок конфигурации IVA Mail в git-репозиторий",
    "spec": [
        Q_BACKEND_HOSTS,
        Q_CMD_PORT,
        {
            "question_name": "Config Dump Path",
            "question_description": "Директория для сохранения конфигурационного снимка",
            "variable": "config_dump_path",
            "type": "text",
            "required": False,
            "default": "/opt/ivamail/config-snapshots",
            "min": 3,
            "max": 1024,
        },
    ],
}

# 08-Config-Apply: бэкенды + CMD + путь к конфигу + restart
SURVEY_SPECS["08-Config-Apply"] = {
    "name": "Config Apply — параметры",
    "description": "Применение конфигурации IVA Mail из JSON-файлов",
    "spec": [
        Q_BACKEND_HOSTS,
        Q_CMD_PORT,
        {
            "question_name": "Config Source Path",
            "question_description": "Путь к директории или файлу конфигурации для применения",
            "variable": "config_source_path",
            "type": "text",
            "required": True,
            "default": "/opt/ivamail/config-snapshots/latest",
            "min": 3,
            "max": 1024,
        },
        {
            "question_name": "Restart After Apply",
            "question_description": "Перезапустить сервис после применения конфигурации",
            "variable": "restart_after_apply",
            "type": "multiplechoice",
            "required": False,
            "default": "no",
            "choices": "yes\nno",
        },
    ],
}

# 09-Config-Rollback: бэкенды + git tag/commit + restart
SURVEY_SPECS["09-Config-Rollback"] = {
    "name": "Config Rollback — параметры",
    "description": "Откат конфигурации IVA Mail к git-снимку",
    "spec": [
        Q_BACKEND_HOSTS,
        Q_CMD_PORT,
        {
            "question_name": "Git Tag or Commit",
            "question_description": "Git-тег или commit hash для отката конфигурации",
            "variable": "git_target",
            "type": "text",
            "required": True,
            "default": "HEAD~1",
            "min": 1,
            "max": 255,
        },
        {
            "question_name": "Restart After Rollback",
            "question_description": "Перезапустить сервис после отката конфигурации",
            "variable": "restart_after_rollback",
            "type": "multiplechoice",
            "required": False,
            "default": "yes",
            "choices": "yes\nno",
        },
    ],
}

# Health-Check: все группы хостов
SURVEY_SPECS["Health-Check"] = {
    "name": "Health Check — параметры",
    "description": "Проверка состояния всех узлов и сервисов IVA Mail",
    "spec": [
        Q_BACKEND_HOSTS,
        Q_FRONTEND_HOSTS,
        Q_HAPROXY_HOSTS,
        Q_STORAGE_HOST,
        Q_MONITORING_HOST,
        Q_CMD_PORT,
        {
            "question_name": "Fail On First Error",
            "question_description": "Остановить проверку при первой ошибке",
            "variable": "fail_on_first_error",
            "type": "multiplechoice",
            "required": False,
            "default": "no",
            "choices": "yes\nno",
        },
    ],
}

# ── Кросс-валидация: все ключи SURVEY_SPECS должны совпадать с именами JT ──
for _key in SURVEY_SPECS:
    if _key not in jt_ids:
        print(f"  WARN  SURVEY_SPECS['{_key}'] не имеет соответствующего JT — проверьте написание имени")

# ── Применяем survey specs ко всем шаблонам ───────────────────────────────
_survey_ok = 0
_survey_fail = 0
for jt_name, spec in SURVEY_SPECS.items():
    jt_id = jt_ids.get(jt_name)
    if jt_id is None:
        print(f"  SKIP  {jt_name} — JT not found")
        continue
    q_count = len(spec.get("spec", []))
    if DRY_RUN:
        print(f"  [DRY-RUN] Would set survey for '{jt_name}' ({q_count}q)")
        _survey_ok += 1
        continue
    # PATCH: disable ask_variables_on_launch (already set at creation, but ensure)
    r_patch = requests.patch(
        f"{AWX_URL}/api/v2/job_templates/{jt_id}/",
        auth=AUTH, headers=HEADERS,
        data=json.dumps({"ask_variables_on_launch": False, "survey_enabled": True}),
        timeout=30
    )
    # POST survey spec (returns 204 No Content on success)
    r_survey = requests.post(
        f"{AWX_URL}/api/v2/job_templates/{jt_id}/survey_spec/",
        auth=AUTH, headers=HEADERS,
        data=json.dumps(spec),
        timeout=30
    )
    if r_patch.status_code in (200, 201, 204) and r_survey.status_code in (200, 201, 204):
        print(f"  OK    {jt_name:<44} {q_count}q  PATCH={r_patch.status_code} SURVEY={r_survey.status_code}")
        _survey_ok += 1
    else:
        print(f"  FAIL  {jt_name}  PATCH={r_patch.status_code} SURVEY={r_survey.status_code}")
        if r_patch.status_code not in (200, 201, 204):
            print(f"        PATCH body: {r_patch.text[:200]}")
        if r_survey.status_code not in (200, 201, 204):
            print(f"        SURVEY body: {r_survey.text[:200]}")
        _survey_fail += 1

print(f"\n  Surveys: {_survey_ok} OK, {_survey_fail} FAIL")

# ---------------------------------------------------------------------------
# Step 8: Workflow Template
# ---------------------------------------------------------------------------
print("\n=== Step 8: Workflow Template ===")

wf_id = find_or_create(
    "/api/v2/workflow_job_templates/", "/api/v2/workflow_job_templates/",
    "name", "IVA Mail Full Deployment",
    {
        "name": "IVA Mail Full Deployment",
        "description": (
            "Полный цикл развёртывания кластера IVA Mail: "
            "установка → запрос лицензии → одобрение → установка лицензии и перезапуск"
        ),
        "organization": org_id,
        "survey_enabled": False,
        "ask_variables_on_launch": True
    },
    label="IVA Mail Full Deployment",
    stat_key="workflows"
)

# Build workflow nodes
# AWX API: POST /api/v2/workflow_job_templates/{id}/workflow_nodes/
# Each node: unified_job_template (JT id) or approval node

def get_or_create_wf_node(wf_id, identifier, node_data):
    """Get existing WF node by checking the list, create if absent."""
    if DRY_RUN:
        print(f"  [DRY-RUN] Would create wf_node '{identifier}'")
        return 0

    existing = api_get(f"/api/v2/workflow_job_templates/{wf_id}/workflow_nodes/",
                       params={"page_size": 50})
    for node in existing.get("results", []):
        if node.get("identifier") == identifier:
            print(f"  EXISTS   wf_node '{identifier}' (id={node['id']})")
            return node["id"]
    r = requests.post(
        f"{AWX_URL}/api/v2/workflow_job_templates/{wf_id}/workflow_nodes/",
        auth=AUTH, headers=HEADERS, data=json.dumps(node_data), timeout=30
    )
    if r.status_code in (200, 201):
        nid = r.json()["id"]
        print(f"  CREATED  wf_node '{identifier}' (id={nid})")
        return nid
    print(f"  ERROR creating wf_node '{identifier}': {r.status_code} {r.text[:300]}")
    return None

def link_wf_nodes(parent_id, child_id, link_type="success"):
    """Link two workflow nodes."""
    if DRY_RUN:
        return True
    url = f"{AWX_URL}/api/v2/workflow_job_template_nodes/{parent_id}/{link_type}_nodes/"
    r = requests.post(url, auth=AUTH, headers=HEADERS,
                      data=json.dumps({"id": child_id}), timeout=30)
    if r.status_code in (200, 201, 204):
        return True
    if r.status_code == 400 and "already" in r.text.lower():
        return True
    print(f"  ERROR linking {parent_id}->{child_id} ({link_type}): {r.status_code} {r.text[:200]}")
    return False

print("  Creating workflow nodes...")

# Node 1: 00-Bootstrap
node_bootstrap_id = get_or_create_wf_node(wf_id, "node-bootstrap", {
    "identifier": "node-bootstrap",
    "unified_job_template": jt_ids.get("00-Bootstrap"),
    "inventory": inventory_id
})

# Node 2: 01-Postgres-NFS
node_postgres_id = get_or_create_wf_node(wf_id, "node-postgres-nfs", {
    "identifier": "node-postgres-nfs",
    "unified_job_template": jt_ids.get("01-Postgres-NFS"),
    "inventory": inventory_id
})

# Node 3: 02-Backends-Install
node_backends_id = get_or_create_wf_node(wf_id, "node-backends-install", {
    "identifier": "node-backends-install",
    "unified_job_template": jt_ids.get("02-Backends-Install"),
    "inventory": inventory_id
})

# Node 4: 02-License-Request
node_license_req_id = get_or_create_wf_node(wf_id, "node-license-request", {
    "identifier": "node-license-request",
    "unified_job_template": jt_ids.get("02-License-Request"),
    "inventory": inventory_id
})

# Node 5: Approval Node
node_approval_id = get_or_create_wf_node(wf_id, "node-approval", {
    "identifier": "node-approval",
    "unified_job_template": None,
    "all_parents_must_converge": False
})

# For approval node we need to set it up as approval via PATCH
if node_approval_id and not DRY_RUN:
    r = requests.patch(
        f"{AWX_URL}/api/v2/workflow_job_template_nodes/{node_approval_id}/",
        auth=AUTH, headers=HEADERS,
        data=json.dumps({
            "unified_job_template": None
        }), timeout=30
    )
    # Create an approval template for this node
    approval_r = requests.post(
        f"{AWX_URL}/api/v2/workflow_job_template_nodes/{node_approval_id}/create_approval_template/",
        auth=AUTH, headers=HEADERS,
        data=json.dumps({
            "name": "Одобрить установку лицензии",
            "description": (
                "1. Скачайте файл запроса из /opt/ivamail/license_requests/ на контроллере.\n"
                "2. Отправьте файл вендору IVA Mail.\n"
                "3. Получив лицензионный .txt файл, разместите его на контроллере AWX.\n"
                "4. Укажите путь к файлу в переменной license_file_path.\n"
                "5. Нажмите Approve для продолжения развёртывания."
            ),
            "timeout": 86400
        }), timeout=30
    )
    if approval_r.status_code in (200, 201):
        print(f"  Approval template created for node {node_approval_id}")
    else:
        print(f"  Approval template: {approval_r.status_code} {approval_r.text[:200]}")

# Node 6: 02-License-Install-And-Restart
node_license_inst_id = get_or_create_wf_node(wf_id, "node-license-install", {
    "identifier": "node-license-install",
    "unified_job_template": jt_ids.get("02-License-Install-And-Restart"),
    "inventory": inventory_id
})

# Node 7: 03-Frontends
node_frontends_id = get_or_create_wf_node(wf_id, "node-frontends", {
    "identifier": "node-frontends",
    "unified_job_template": jt_ids.get("03-Frontends"),
    "inventory": inventory_id
})

# Node 8: 04-HAProxy
node_haproxy_id = get_or_create_wf_node(wf_id, "node-haproxy", {
    "identifier": "node-haproxy",
    "unified_job_template": jt_ids.get("04-HAProxy"),
    "inventory": inventory_id
})

# Node 9: 05-Monitoring
node_monitoring_id = get_or_create_wf_node(wf_id, "node-monitoring", {
    "identifier": "node-monitoring",
    "unified_job_template": jt_ids.get("05-Monitoring"),
    "inventory": inventory_id
})

# Link nodes: chain topology
# 00-Bootstrap -> 01-Postgres-NFS -> 02-Backends-Install -> 02-License-Request
# -> [Approval] -> 02-License-Install-And-Restart -> 03-Frontends -> 04-HAProxy -> 05-Monitoring
print("  Linking workflow nodes...")
links = [
    (node_bootstrap_id,    node_postgres_id,     "success"),
    (node_postgres_id,     node_backends_id,     "success"),
    (node_backends_id,     node_license_req_id,  "success"),
    (node_license_req_id,  node_approval_id,     "success"),
    (node_approval_id,     node_license_inst_id, "success"),
    (node_license_inst_id, node_frontends_id,    "success"),
    (node_frontends_id,    node_haproxy_id,      "success"),
    (node_haproxy_id,      node_monitoring_id,   "success"),
]
for (parent, child, ltype) in links:
    if parent and child:
        ok = link_wf_nodes(parent, child, ltype)
        print(f"  Link {parent} -[{ltype}]-> {child}: {'OK' if ok else 'FAILED'}")

# ---------------------------------------------------------------------------
# Final Summary
# ---------------------------------------------------------------------------
_total_elapsed = time.time() - _script_start

print("\n=== Final Summary ===")
print(f"Organization 'IVA Mail'           : id={org_id}")
print(f"Credential Type 'IVA Mail CMD'    : id={ct_ivamail_cmd_id}")
print(f"Credential Type 'PostgreSQL Admin': id={ct_pg_admin_id}")
print(f"Credential 'IVA Mail SSH Key'     : id={ssh_cred_id}")
print(f"Credential 'IVA Mail CMD'         : id={ivamail_cmd_cred_id}")
print(f"Credential 'PostgreSQL Admin'     : id={pg_admin_cred_id}")
print(f"Project 'iva-mail-ansible'        : id={project_id}")
print(f"Inventory 'IVA Mail Dynamic'      : id={inventory_id}")
print(f"Workflow 'IVA Mail Full Deployment': id={wf_id}")
print("\nJob Templates:")
for name, jt_id in jt_ids.items():
    print(f"  {name:<44} id={jt_id}")

# Verify via GET
if not DRY_RUN:
    print("\n=== Verification ===")
    jt_list = api_get("/api/v2/job_templates/", params={"page_size": 50})
    jt_count = jt_list.get("count", 0)
    print(f"Total Job Templates in AWX:      {jt_count}")
    wf_list = api_get("/api/v2/workflow_job_templates/", params={"page_size": 50})
    wf_count = wf_list.get("count", 0)
    print(f"Total Workflow Templates in AWX: {wf_count}")
else:
    jt_count = len(JOB_TEMPLATES)
    wf_count = 1

# ---------------------------------------------------------------------------
# NEXT STEPS
# ---------------------------------------------------------------------------
print()
print("  ╔══════════════════════════════════════════════════════╗")
print("  ║          AWX PRECONFIG COMPLETE                      ║")
print("  ╚══════════════════════════════════════════════════════╝")
print()
print(f"  Время выполнения: {_total_elapsed:.1f}s")
print()
print("  Создано ресурсов:")
for key, val in _stats.items():
    total = val["created"] + val["exists"]
    if total > 0:
        print(f"    {key:<20}: {val['created']} создано, {val['exists']} уже существовало")
print(f"    Job Templates        : {jt_count} шт.")
print(f"    Workflows            : {wf_count} шт.")
print(f"    Survey specs         : {_survey_ok} OK, {_survey_fail} FAIL")
print()
print("  СЛЕДУЮЩИЕ ШАГИ:")
print("    1. Откройте AWX -> Credentials")
print("    2. Откройте 'IVA Mail SSH Key' -> Edit")
print("       Замените PLACEHOLDER_CHANGE_ME на реальный SSH ключ/пароль")
print("    3. Откройте 'IVA Mail CMD' -> Edit")
print("       Установите cmd_user и cmd_password для порта 106")
print("    4. Откройте 'PostgreSQL Admin' -> Edit")
print("       Установите pg_admin_password")
print("    5. Запустите workflow: 'IVA Mail Full Deployment'")
print()
