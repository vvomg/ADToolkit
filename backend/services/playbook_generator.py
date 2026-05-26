"""
playbook_generator.py — генератор Ansible-плейбуков из config-store.

Читает YAML-файлы конфигурации и формирует playbook с задачами применения
конфигурации на нодах через cmd_client.py (CMD-протокол IVA Mail).

Схема config-store:
  {config_store_dir}/{ip}/
    _meta.yml
    modules/
      SMTP.yml
      Cluster.yml
    domains/
      example.com/
        _config.yml
        objects/
          admin.yml
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _load_yaml(path: str | Path) -> dict[str, Any]:
    """Загружает YAML-файл и возвращает словарь. При ошибке — пустой dict."""
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("Файл не найден: %s", path)
        return {}
    except Exception as exc:
        logger.error("Ошибка чтения %s: %s", path, exc)
        return {}


def _config_to_kv_args(config: dict[str, Any]) -> list[str]:
    """
    Конвертирует плоский dict конфига в список аргументов --kv key=value.

    Правила:
    - Только плоские значения первого уровня (str, int, float, bool)
    - Пропускаем ключи, начинающиеся с '_' (служебные метаданные)
    - Пропускаем None / null значения
    - Вложенные объекты (dict/list) пропускаются
    """
    args: list[str] = []
    for key, value in config.items():
        # Пропускаем метаключи
        if str(key).startswith("_"):
            continue
        # Пропускаем None
        if value is None:
            continue
        # Принимаем только скалярные значения
        if isinstance(value, (str, int, float, bool)):
            args.extend(["--kv", f"{key}={value}"])
    return args


def _kv_args_for_diff(
    config: dict[str, Any],
    diff_keys: dict[str, Any],
) -> list[str]:
    """
    Для режима diff: генерирует --kv только для ключей из diff_keys.
    diff_keys имеет вид {key: {"old": ..., "new": ...}}.
    """
    args: list[str] = []
    for key in diff_keys:
        if str(key).startswith("_"):
            continue
        value = config.get(key)
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            args.extend(["--kv", f"{key}={value}"])
    return args


def _indent(text: str, spaces: int) -> str:
    """Добавляет отступ к каждой строке текста."""
    prefix = " " * spaces
    return "\n".join(prefix + line if line.strip() else line for line in text.splitlines())


def _kv_block(kv_args: list[str]) -> str:
    """
    Формирует многострочный блок cmd аргументов для ansible.builtin.script.
    Пример: --kv MaxMessageSize=52428800 --kv MaxRecipients=100
    """
    if not kv_args:
        return ""
    # Разбиваем попарно: ["--kv", "key=val", "--kv", "key2=val2"]
    pairs = []
    i = 0
    while i < len(kv_args) - 1:
        if kv_args[i] == "--kv":
            pairs.append(f"--kv {kv_args[i + 1]}")
            i += 2
        else:
            i += 1
    return " ".join(pairs)


# ---------------------------------------------------------------------------
# Генерация задач для одного хоста
# ---------------------------------------------------------------------------

def _task_module(
    ip: str,
    module: str,
    kv_args: list[str],
    cmd_client_path: str,
    cmd_port: int,
    cmd_timeout: int,
) -> str:
    """Генерирует YAML-блок задачи применения модуля."""
    if not kv_args:
        logger.debug("Модуль %s на %s: нет kv-аргументов, задача пропускается", module, ip)
        return ""

    kv_str = _kv_block(kv_args)
    return f"""\
    - name: "Apply module {module} → {ip}"
      ansible.builtin.script:
        cmd: >-
          {cmd_client_path}
          --host {ip}
          --port {cmd_port}
          --timeout {cmd_timeout}
          --action module-update
          --module {module}
          {kv_str}
      environment:
        IVAMAIL_CMD_USER: "{{{{ lookup('env', 'IVAMAIL_CMD_USER') }}}}"
        IVAMAIL_CMD_PASSWORD: "{{{{ lookup('env', 'IVAMAIL_CMD_PASSWORD') }}}}"
      no_log: true
      changed_when: true
      tags: [config_apply, "{ip}", "module_{module}"]
"""


def _task_domain(
    ip: str,
    domain: str,
    kv_args: list[str],
    cmd_client_path: str,
    cmd_port: int,
    cmd_timeout: int,
) -> str:
    """Генерирует YAML-блок задачи применения домена."""
    if not kv_args:
        logger.debug("Домен %s на %s: нет kv-аргументов, задача пропускается", domain, ip)
        return ""

    kv_str = _kv_block(kv_args)
    return f"""\
    - name: "Apply domain {domain} → {ip}"
      ansible.builtin.script:
        cmd: >-
          {cmd_client_path}
          --host {ip}
          --port {cmd_port}
          --timeout {cmd_timeout}
          --action domain-update
          --domain {domain}
          {kv_str}
      environment:
        IVAMAIL_CMD_USER: "{{{{ lookup('env', 'IVAMAIL_CMD_USER') }}}}"
        IVAMAIL_CMD_PASSWORD: "{{{{ lookup('env', 'IVAMAIL_CMD_PASSWORD') }}}}"
      no_log: true
      changed_when: true
      tags: [config_apply, "{ip}", "domain_{domain}"]
"""


def _task_object(
    ip: str,
    domain: str,
    obj_name: str,
    kv_args: list[str],
    cmd_client_path: str,
    cmd_port: int,
    cmd_timeout: int,
) -> str:
    """Генерирует YAML-блок задачи применения объекта домена."""
    if not kv_args:
        logger.debug(
            "Объект %s@%s на %s: нет kv-аргументов, задача пропускается", obj_name, domain, ip
        )
        return ""

    kv_str = _kv_block(kv_args)
    uid = f"{obj_name}@{domain}"
    return f"""\
    - name: "Apply object {uid} → {ip}"
      ansible.builtin.script:
        cmd: >-
          {cmd_client_path}
          --host {ip}
          --port {cmd_port}
          --timeout {cmd_timeout}
          --action object-update
          --domain {domain}
          --object {obj_name}
          {kv_str}
      environment:
        IVAMAIL_CMD_USER: "{{{{ lookup('env', 'IVAMAIL_CMD_USER') }}}}"
        IVAMAIL_CMD_PASSWORD: "{{{{ lookup('env', 'IVAMAIL_CMD_PASSWORD') }}}}"
      no_log: true
      changed_when: true
      tags: [config_apply, "{ip}", "object_{obj_name}"]
"""


# ---------------------------------------------------------------------------
# Основная функция генерации плейбука
# ---------------------------------------------------------------------------

def generate_apply_playbook(
    hosts: list[str],
    config_store_dir: str,
    mode: str = "full",
    include_objects: bool = False,
    diff_data: dict[str, Any] | None = None,
    cmd_client_path: str = "scripts/cmd_client.py",
    cmd_port: int = 106,
    cmd_timeout: int = 30,
) -> str:
    """
    Генерирует Ansible YAML-плейбук из данных config-store.

    Аргументы:
        hosts:            Список IP-адресов нод (например, ["10.3.6.126", "10.3.6.127"])
        config_store_dir: Путь к корневой директории config-store
        mode:             "full" — все ключи; "diff" — только изменённые ключи
        include_objects:  Включать ли задачи для объектов доменов
        diff_data:        Для mode="diff": {ip: {resource: {key: {old, new}}}}
                          resource = "module_SMTP" | "domain_example.com" | "object_admin@example.com"
        cmd_client_path:  Путь к cmd_client.py (относительно ansible-проекта)
        cmd_port:         Порт CMD-сервера IVA Mail
        cmd_timeout:      Таймаут CMD-команды в секундах

    Возвращает: строку с готовым YAML-плейбуком.
    """
    store_root = Path(config_store_dir)
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    hosts_str = ", ".join(hosts)

    logger.info(
        "Генерация плейбука: hosts=%s mode=%s include_objects=%s",
        hosts_str, mode, include_objects,
    )

    # ---------------------------------------------------------------------------
    # Play 1: Добавление нод в инвентарь
    # ---------------------------------------------------------------------------

    hosts_loop_items = "\n".join(f"        - {ip}" for ip in hosts)

    play1 = f"""\
- name: "Apply IVA Mail config (generated)"
  hosts: localhost
  gather_facts: no
  tasks:
    - name: Add target nodes to inventory
      ansible.builtin.add_host:
        name: "{{{{ item }}}}"
        groups:
          - config_targets
        ansible_host: "{{{{ item }}}}"
        ansible_user: root
      loop:
{hosts_loop_items}
"""

    # ---------------------------------------------------------------------------
    # Play 2: Задачи применения конфигурации (serial: 1, delegate к localhost)
    # ---------------------------------------------------------------------------

    all_tasks: list[str] = []

    for ip in hosts:
        ip_dir = store_root / ip
        if not ip_dir.is_dir():
            logger.warning("Директория для ноды %s не найдена: %s", ip, ip_dir)
            continue

        # --- Модули ---
        modules_path = ip_dir / "modules"
        if modules_path.is_dir():
            for yml_file in sorted(modules_path.glob("*.yml")):
                module_name = yml_file.stem
                config = _load_yaml(yml_file)
                if not config:
                    continue

                # Для режима diff: фильтруем ключи
                if mode == "diff" and diff_data is not None:
                    diff_key = f"module_{module_name}"
                    ip_diff = diff_data.get(ip, {})
                    resource_diff = ip_diff.get(diff_key, {})
                    if not resource_diff:
                        logger.debug("diff: нет изменений для %s/%s, пропускаем", ip, module_name)
                        continue
                    kv_args = _kv_args_for_diff(config, resource_diff)
                else:
                    kv_args = _config_to_kv_args(config)

                task = _task_module(ip, module_name, kv_args, cmd_client_path, cmd_port, cmd_timeout)
                if task:
                    all_tasks.append(task)

        # --- Домены ---
        domains_path = ip_dir / "domains"
        if domains_path.is_dir():
            for domain_dir in sorted(domains_path.iterdir()):
                if not domain_dir.is_dir():
                    continue
                domain_name = domain_dir.name

                # Читаем _config.yml домена
                domain_config_file = domain_dir / "_config.yml"
                if domain_config_file.exists():
                    config = _load_yaml(domain_config_file)
                    if config:
                        if mode == "diff" and diff_data is not None:
                            diff_key = f"domain_{domain_name}"
                            ip_diff = diff_data.get(ip, {})
                            resource_diff = ip_diff.get(diff_key, {})
                            if not resource_diff:
                                logger.debug(
                                    "diff: нет изменений для %s/domain/%s, пропускаем",
                                    ip, domain_name,
                                )
                                config = None
                            else:
                                kv_args = _kv_args_for_diff(config, resource_diff)
                        else:
                            kv_args = _config_to_kv_args(config)

                        if config is not None:
                            task = _task_domain(
                                ip, domain_name, kv_args, cmd_client_path, cmd_port, cmd_timeout
                            )
                            if task:
                                all_tasks.append(task)

                # --- Объекты домена (опционально) ---
                if include_objects:
                    objects_path = domain_dir / "objects"
                    if objects_path.is_dir():
                        for obj_file in sorted(objects_path.glob("*.yml")):
                            obj_name = obj_file.stem
                            obj_config = _load_yaml(obj_file)
                            if not obj_config:
                                continue

                            if mode == "diff" and diff_data is not None:
                                diff_key = f"object_{obj_name}@{domain_name}"
                                ip_diff = diff_data.get(ip, {})
                                resource_diff = ip_diff.get(diff_key, {})
                                if not resource_diff:
                                    continue
                                kv_args = _kv_args_for_diff(obj_config, resource_diff)
                            else:
                                kv_args = _config_to_kv_args(obj_config)

                            task = _task_object(
                                ip, domain_name, obj_name, kv_args,
                                cmd_client_path, cmd_port, cmd_timeout,
                            )
                            if task:
                                all_tasks.append(task)

    # Если задач нет — добавляем заглушку, чтобы плейбук был валидным
    if not all_tasks:
        logger.warning("Плейбук сгенерирован без задач применения (пустой config-store или diff)")
        all_tasks.append(
            "    - name: No config changes to apply\n"
            "      ansible.builtin.debug:\n"
            '        msg: "No configuration tasks generated"\n'
        )

    tasks_block = "\n".join(all_tasks)

    play2 = f"""\
- name: "Apply config to nodes"
  hosts: localhost
  gather_facts: no
  serial: 1
  tasks:
{tasks_block}
"""

    # ---------------------------------------------------------------------------
    # Заголовочный комментарий
    # ---------------------------------------------------------------------------
    header = f"""\
# Generated by ADToolKit playbook_generator
# Timestamp: {now_ts}
# Hosts: {hosts_str}
# Mode: {mode}
# Include objects: {include_objects}
# DO NOT EDIT MANUALLY — regenerate via ADToolKit UI

"""

    return header + play1 + "\n" + play2


# ---------------------------------------------------------------------------
# Сохранение плейбука
# ---------------------------------------------------------------------------

def save_generated_playbook(
    content: str,
    config_store_dir: str,
    prefix: str = "apply",
) -> str:
    """
    Сохраняет сгенерированный плейбук в {config_store_dir}/_generated/{prefix}-{timestamp}.yml.

    Создаёт директорию _generated если не существует.
    Возвращает абсолютный путь к созданному файлу.
    """
    store_root = Path(config_store_dir)
    generated_dir = store_root / "_generated"

    try:
        generated_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("Не удалось создать директорию %s: %s", generated_dir, exc)
        raise

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    filename = f"{prefix}-{timestamp}.yml"
    file_path = generated_dir / filename

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("Плейбук сохранён: %s", file_path)
    except OSError as exc:
        logger.error("Ошибка записи плейбука %s: %s", file_path, exc)
        raise

    return str(file_path.resolve())
