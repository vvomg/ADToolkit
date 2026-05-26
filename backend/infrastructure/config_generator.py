"""
PHASE 3: Config Generator

Генерирует конфигурационные файлы из Jinja2-шаблонов.
Все файлы создаются локально во временной директории,
затем загружаются на целевые серверы через SFTP.
"""

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..models.schemas import ClusterInput, ServerConfig
from ..core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class GeneratedFile:
    """Сгенерированный файл: локальный путь + целевой путь на сервере."""
    local_path: str
    remote_path: str
    description: str


class ConfigGenerator:
    """
    Генератор конфигурационных файлов через Jinja2.

    Принцип: шаблоны → рендер с контекстом → временный файл → SFTP upload.
    Ни одна конфигурация не собирается конкатенацией строк в коде.
    """

    def __init__(self):
        templates_dir = Path(settings.templates_dir)
        if not templates_dir.exists():
            # Fallback: ищем рядом с модулем
            templates_dir = Path(__file__).parent.parent / "templates"

        self._env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            undefined=StrictUndefined,  # Ошибка если переменная не определена
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._tmpdir = tempfile.mkdtemp(prefix="ivamail_configs_")
        logger.info(f"ConfigGenerator: templates={templates_dir}, tmpdir={self._tmpdir}")

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def generate_pg_hba(self, cluster: ClusterInput) -> GeneratedFile:
        """Генерирует pg_hba.conf для разрешения доступа с бэкендов."""
        context = {
            "backends": [{"ip": b.ip} for b in cluster.backends],
            "db_user": "ivamail",
        }
        return self._render(
            template_name="pg_hba.conf.j2",
            context=context,
            output_filename="pg_hba.conf",
            remote_path="/etc/postgresql/pg_hba.conf",
            description="pg_hba.conf (доступ бэкендов к PostgreSQL)",
        )

    def generate_postgresql_conf(
        self,
        cluster: ClusterInput,
        pg_version: str = "16",
        overrides: Dict[str, Any] | None = None,
    ) -> GeneratedFile:
        """Генерирует postgresql.conf с настройками производительности."""
        n_backends = len(cluster.backends)
        context: Dict[str, Any] = {
            "pg_version": pg_version,
            "pg_port": settings.postgres_port,
            "max_connections": 100 + n_backends * 50,
            "shared_buffers": "256MB",
            "work_mem": "4MB",
            "maintenance_work_mem": "64MB",
        }
        if overrides:
            context.update(overrides)

        return self._render(
            template_name="postgresql.conf.j2",
            context=context,
            output_filename="postgresql.conf",
            remote_path=f"/etc/postgresql/{pg_version}/main/postgresql.conf",
            description="postgresql.conf",
        )

    def generate_nfs_exports(self, cluster: ClusterInput) -> GeneratedFile:
        """Генерирует /etc/exports с правами доступа для каждого бэкенда."""
        context = {
            "backends": [{"ip": b.ip} for b in cluster.backends],
            "nfs_share_path": cluster.nfs_share_path,
        }
        return self._render(
            template_name="nfs_exports.j2",
            context=context,
            output_filename="nfs_exports",
            remote_path="/etc/exports",
            description="/etc/exports (NFS экспорт)",
        )

    def generate_all(self, cluster: ClusterInput) -> List[GeneratedFile]:
        """Генерирует все конфиги кластера за один вызов."""
        files = [
            self.generate_pg_hba(cluster),
            self.generate_postgresql_conf(cluster),
            self.generate_nfs_exports(cluster),
        ]
        logger.info(f"Сгенерировано {len(files)} конфигурационных файлов")
        return files

    def cleanup(self) -> None:
        """Удаляет временные файлы."""
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        logger.debug(f"Временная директория удалена: {self._tmpdir}")

    # ─────────────────────────────────────────────────────────────────
    # Private
    # ─────────────────────────────────────────────────────────────────

    def _render(
        self,
        template_name: str,
        context: Dict[str, Any],
        output_filename: str,
        remote_path: str,
        description: str,
    ) -> GeneratedFile:
        """Рендерит шаблон и сохраняет результат в tmpdir."""
        template = self._env.get_template(template_name)
        rendered = template.render(**context)

        local_path = os.path.join(self._tmpdir, output_filename)
        with open(local_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(rendered)

        logger.debug(f"Сгенерирован: {output_filename} → {remote_path} ({len(rendered)} bytes)")
        return GeneratedFile(
            local_path=local_path,
            remote_path=remote_path,
            description=description,
        )


# Глобальный экземпляр
config_generator = ConfigGenerator()
