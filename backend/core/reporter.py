"""
PHASE 4B: Reporter

Генерирует HTML-отчёт о завершённом развертывании.
Данные берёт из БД (DeploymentRun + logs + health checks).
Рендерит через Jinja2 шаблон report.html.j2.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy.orm import Session

from ..models.db_models import DeploymentRun, DeploymentLog, HealthCheckResult
from ..models.schemas import ClusterInput
from ..models.license_models import LicenseRequestParams
from ..core.config import settings

logger = logging.getLogger(__name__)


class Reporter:
    """
    Генератор HTML-отчёта о развертывании.

    Получает deployment_id, читает данные из БД,
    рендерит через Jinja2 и сохраняет HTML в DeploymentRun.report_html.
    """

    def __init__(self):
        templates_dir = Path(settings.templates_dir)
        if not templates_dir.exists():
            templates_dir = Path(__file__).parent.parent / "templates"

        self._env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=True,
        )
        logger.debug(f"Reporter: templates={templates_dir}")

    def generate(
        self,
        run: DeploymentRun,
        logs: List[DeploymentLog],
        health_results: List[HealthCheckResult],
        health_summary: dict,
    ) -> str:
        """
        Рендерит HTML-отчёт.

        Args:
            run:            запись DeploymentRun из БД
            logs:           список DeploymentLog
            health_results: список HealthCheckResult
            health_summary: dict из HealthChecker.build_summary()

        Returns:
            Строка с HTML-отчётом.
        """
        cluster_config = ClusterInput(**run.cluster_config) if run.cluster_config else None
        license_config = LicenseRequestParams(**run.license_config) if run.license_config else None

        context = {
            "deployment_id": run.id,
            "status": run.status,
            "error_message": run.error_message,
            "created_at": run.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if run.created_at else "—",
            "completed_at": run.completed_at.strftime("%Y-%m-%d %H:%M:%S UTC") if run.completed_at else None,
            "cluster_config": cluster_config,
            "license_config": license_config,
            "ivamail_version": cluster_config.ivamail_version if cluster_config else "—",
            "health_summary": health_summary,
            "logs": logs,
            "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        template = self._env.get_template("report.html.j2")
        html = template.render(**context)
        logger.info(
            f"[Reporter] Отчёт сгенерирован для {run.id}: {len(html)} символов"
        )
        return html

    def save_to_run(self, session: Session, run: DeploymentRun, html: str) -> None:
        """Сохраняет HTML-отчёт в поле DeploymentRun.report_html."""
        run.report_html = html
        session.add(run)
        session.commit()
        logger.info(f"[Reporter] Отчёт сохранён в БД для deployment {run.id}")


# Глобальный экземпляр
reporter = Reporter()
