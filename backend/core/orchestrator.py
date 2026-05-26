"""
PHASE 4C: Deployment Orchestrator

State Machine для полного цикла развертывания кластера IVA Mail.

Фазы и переходы:
  CONFIGURATION → PREFLIGHT → INFRA_SETUP →
  NODE_STARTUP → CLUSTER_CONFIG →
  LICENSE_REQUEST → WAITING_LICENSE →
  LICENSE_INSTALL → REMAINING_NODES →
  HEALTH_CHECKS → REPORTING → SUCCESS
  (любая фаза) → FAILED при ошибке

Каждый переход:
  1. Обновляет DeploymentRun.status в БД
  2. Пишет DeploymentLog записи
  3. При исключении → статус FAILED + error_message

Запуск из API:
  asyncio.create_task(orchestrator.run(deployment_id))
  → HTTP-запрос возвращает deployment_id немедленно
  → клиент polling'ит GET /api/deployment/{id}
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session

from ..models.db_models import (
    DeploymentRun, DeploymentLog, HealthCheckResult,
    get_db_engine, init_db,
)
from ..models.schemas import (
    ClusterInput, DeploymentStatus, PackageConfig,
)
from ..models.license_models import LicenseRequestParams
from .config import settings
from ..infrastructure.ssh_manager import SSHManager
from ..infrastructure.config_generator import ConfigGenerator
from ..infrastructure.postgresql_setup import PostgreSQLSetup
from ..infrastructure.nfs_setup import NFSSetup
from ..infrastructure.ivamail_setup import IvamailSetup
from ..infrastructure.haproxy_setup import HAProxySetup
from ..infrastructure.license_manager import LicenseManager
from ..infrastructure.node_manager import NodeManager
from ..core.health_checker import HealthChecker
from ..core.reporter import reporter
from ..core.validator import validator

logger = logging.getLogger(__name__)


class DeploymentOrchestrator:
    """
    Оркестратор развертывания кластера IVA Mail.

    Создаётся один раз (singleton), исполняет конкурентные
    deployments — каждый в отдельном asyncio.Task.
    """

    def __init__(self):
        self._ssh = SSHManager()

    # ─────────────────────────────────────────────────────────────────
    # Public entry point
    # ─────────────────────────────────────────────────────────────────

    async def run(self, deployment_id: str) -> None:
        """
        Запускает полный цикл развертывания.
        Вызывается через asyncio.create_task() из API.
        """
        session = self._get_session()
        try:
            run: Optional[DeploymentRun] = session.get(DeploymentRun, deployment_id)
            if not run:
                logger.error(f"[Orch] Deployment {deployment_id} не найден в БД")
                return

            cluster = ClusterInput(**run.cluster_config)
            license_params = LicenseRequestParams(**run.license_config)
            raw_pc = run.package_config
            if raw_pc is None and isinstance(run.cluster_config, dict):
                raw_pc = run.cluster_config.get("package_config")
            if raw_pc is not None and not isinstance(raw_pc, dict):
                raw_pc = None
            package_config = (
                PackageConfig.model_validate(raw_pc) if raw_pc is not None else None
            )

            await self._execute_pipeline(
                session, run, cluster, license_params, package_config
            )
        except Exception as e:
            logger.error(f"[Orch] Необработанное исключение в run(): {e}", exc_info=True)
        finally:
            session.close()
            self._ssh.close_all()

    # ─────────────────────────────────────────────────────────────────
    # Pipeline
    # ─────────────────────────────────────────────────────────────────

    async def _execute_pipeline(
        self,
        session: Session,
        run: DeploymentRun,
        cluster: ClusterInput,
        license_params: LicenseRequestParams,
        package_config: Optional[PackageConfig],
    ) -> None:
        """Полный пайплайн — все фазы последовательно."""

        cfg_gen = ConfigGenerator()
        pg_setup = PostgreSQLSetup(self._ssh, cfg_gen)
        nfs_setup = NFSSetup(self._ssh, cfg_gen)
        ivamail_setup = IvamailSetup(self._ssh)
        haproxy_setup = HAProxySetup(self._ssh)
        license_mgr = LicenseManager(self._ssh)
        node_mgr = NodeManager(self._ssh)
        health_checker = HealthChecker(self._ssh)

        try:
            # ── PREFLIGHT ──────────────────────────────────────────
            await self._transition(session, run, DeploymentStatus.PREFLIGHT)
            preflight_logs = await self._preflight(cluster)
            await self._write_logs(session, run.id, "preflight", preflight_logs)

            # ── INFRA_SETUP ────────────────────────────────────────
            await self._transition(session, run, DeploymentStatus.INFRA_SETUP)

            await self._log(session, run.id, "infra_setup", "Настройка PostgreSQL...")
            pg_logs = await pg_setup.setup(cluster)
            await self._write_logs(session, run.id, "infra_setup:postgresql", pg_logs)

            await self._log(session, run.id, "infra_setup", "Настройка NFS...")
            nfs_logs = await nfs_setup.setup(cluster)
            await self._write_logs(session, run.id, "infra_setup:nfs", nfs_logs)

            await self._log(session, run.id, "infra_setup", "Установка пакета IVA Mail...")
            ivamail_logs = await ivamail_setup.setup(cluster, package_config)
            await self._write_logs(session, run.id, "infra_setup:ivamail", ivamail_logs)

            # ── NODE_STARTUP ───────────────────────────────────────
            await self._transition(session, run, DeploymentStatus.NODE_STARTUP)
            await self._log(session, run.id, "node_startup",
                            "Запуск всех узлов кластера без аргументов...")
            node_start_logs = await node_mgr.start_all_nodes(cluster)
            await self._write_logs(session, run.id, "node_startup", node_start_logs)

            # ── CLUSTER_CONFIG ─────────────────────────────────────
            await self._transition(session, run, DeploymentStatus.CLUSTER_CONFIG)
            await self._log(session, run.id, "cluster_config",
                            "Настройка кластерной конфигурации через CMD:ModuleUpdateConfig...")
            cluster_cfg_logs = await node_mgr.configure_cluster_nodes(cluster)
            await self._write_logs(session, run.id, "cluster_config", cluster_cfg_logs)

            # ── LICENSE_REQUEST ────────────────────────────────────
            await self._transition(session, run, DeploymentStatus.LICENSE_REQUEST)
            await self._log(session, run.id, "license_request",
                            "Генерация запроса лицензии через CMD:LicenseRequest...")
            request_info = await license_mgr.prepare_license_request(
                run.id, cluster, license_params
            )
            await self._write_logs(session, run.id, "license_request", [
				f"Файл запроса: {request_info.request_file_path}",
				f"Backend: {request_info.backend1_hostname} ({request_info.backend1_ip})",
				f"Размер ответа: {len(request_info.request_file_content)} символов",
				"Передайте файл запроса вендору IVA Mail для получения лицензии.",
				f"Загрузка лицензии: POST /api/deployment/{run.id}/upload-license",
			])
            await self._log(
                session, run.id, "license_request",
                f"Файл запроса: GET /api/deployment/{run.id}/license-request/download"
            )

            # ── WAITING_LICENSE ────────────────────────────────────
            await self._transition(session, run, DeploymentStatus.WAITING_LICENSE)
            await self._log(session, run.id, "waiting_license",
                            "Ожидание загрузки файла лицензии (.txt) от администратора...")
            license_path = await license_mgr.wait_for_license_file(run.id)
            await self._log(session, run.id, "waiting_license",
                            f"Файл лицензии получен: {license_path}")

            # ── LICENSE_INSTALL ────────────────────────────────────
            await self._transition(session, run, DeploymentStatus.LICENSE_INSTALL)

            await self._log(session, run.id, "license_install",
                            f"CMD:LicenseInstall на {cluster.backends[0].hostname}...")
            primary_logs = await license_mgr.install_on_primary(
                cluster.backends[0], license_path
            )
            await self._write_logs(session, run.id, "license_install", primary_logs)

            # ── REMAINING_NODES ────────────────────────────────────
            await self._transition(session, run, DeploymentStatus.REMAINING_NODES)
            await self._log(session, run.id, "remaining_nodes",
                            "Перезапуск узлов в кластерном режиме (--backend/--frontend)...")
            restart_logs = await node_mgr.restart_in_cluster_mode(cluster)
            await self._write_logs(session, run.id, "remaining_nodes", restart_logs)

            # ── HAPROXY_SETUP ─────────────────────────────────────
            if cluster.haproxy_servers:
                await self._transition(session, run, DeploymentStatus.HAPROXY_SETUP)
                await self._log(session, run.id, "haproxy_setup",
                                f"Установка и настройка HAProxy на {len(cluster.haproxy_servers)} узле(ах)...")
                haproxy_logs = await haproxy_setup.setup(cluster)
                await self._write_logs(session, run.id, "haproxy_setup", haproxy_logs)
            else:
                await self._log(session, run.id, "remaining_nodes",
                                "HAProxy серверы не сконфигурированы — фаза HAPROXY_SETUP пропущена")

            # ── HEALTH_CHECKS ──────────────────────────────────────
            await self._transition(session, run, DeploymentStatus.HEALTH_CHECKS)
            await self._log(session, run.id, "health_checks",
                            "Запуск проверок здоровья кластера...")
            check_results = await health_checker.run_all(cluster)
            health_summary = health_checker.build_summary(check_results)

            # Сохраняем HealthCheckResult в БД
            for cr in check_results:
                db_model = cr.to_db_model(run.id)
                session.add(db_model)
            session.commit()

            # Логируем сводку
            await self._log(
                session, run.id, "health_checks",
                f"Проверки: {health_summary['passed']}/{health_summary['total']} прошло",
                level="INFO" if health_summary["all_ok"] else "WARNING",
            )
            if not health_summary["all_ok"]:
                for check_type, data in health_summary["by_type"].items():
                    for srv in data["servers"]:
                        if not srv["ok"]:
                            await self._log(
                                session, run.id, "health_checks",
                                f"FAIL [{check_type}] {srv['hostname']}: {srv['error']}",
                                level="WARNING",
                                server_ip=srv["ip"],
                            )

            # ── REPORTING ──────────────────────────────────────────
            await self._transition(session, run, DeploymentStatus.REPORTING)
            await self._log(session, run.id, "reporting", "Генерация HTML-отчёта...")

            # Читаем все логи для отчёта
            all_logs = (
                session.query(DeploymentLog)
                .filter(DeploymentLog.deployment_id == run.id)
                .order_by(DeploymentLog.created_at)
                .all()
            )
            html = reporter.generate(run, all_logs, check_results, health_summary)
            reporter.save_to_run(session, run, html)
            await self._log(session, run.id, "reporting",
                            f"Отчёт сохранён ({len(html)} символов)")

            # ── SUCCESS ────────────────────────────────────────────
            run.completed_at = datetime.utcnow()
            await self._transition(session, run, DeploymentStatus.SUCCESS)
            await self._log(session, run.id, "success",
                            "Развертывание кластера IVA Mail завершено успешно ✓")

            logger.info(f"[Orch] ✓ Deployment {run.id} завершён успешно")

        except TimeoutError as e:
            await self._fail(session, run, str(e), "waiting_license")
        except Exception as e:
            phase = run.status if run.status != DeploymentStatus.SUCCESS.value else "unknown"
            logger.error(f"[Orch] Ошибка в фазе {phase}: {e}", exc_info=True)
            await self._fail(session, run, str(e), phase)
        finally:
            cfg_gen.cleanup()

    # ─────────────────────────────────────────────────────────────────
    # Preflight
    # ─────────────────────────────────────────────────────────────────

    async def _preflight(self, cluster: ClusterInput) -> List[str]:
        """
        Проверка SSH-доступности всех узлов перед началом установки.
        Падение здесь — сигнал прервать деплой до начала изменений.
        """
        logs: List[str] = []
        all_servers = list(cluster.backends)
        all_servers.append(cluster.database_server)
        if cluster.nfs_server:
            all_servers.append(cluster.nfs_server)

        tasks = [self._ssh.check_connectivity(s) for s in all_servers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        failed = []
        for server, result in zip(all_servers, results):
            if isinstance(result, Exception):
                failed.append(f"{server.hostname} ({server.ip}): {result}")
                logs.append(f"✗ SSH {server.hostname} ({server.ip}): {result}")
            else:
                ok, msg = result
                if ok:
                    logs.append(f"✓ SSH {server.hostname} ({server.ip}): OK")
                else:
                    failed.append(f"{server.hostname} ({server.ip}): {msg}")
                    logs.append(f"✗ SSH {server.hostname} ({server.ip}): {msg}")

        if failed:
            raise RuntimeError(
                f"Preflight провален — недоступны узлы:\n" +
                "\n".join(f"  • {f}" for f in failed)
            )

        logs.append(f"Preflight пройден: все {len(all_servers)} узла(ов) доступны по SSH ✓")
        return logs

    # ─────────────────────────────────────────────────────────────────
    # State transitions & logging helpers
    # ─────────────────────────────────────────────────────────────────

    async def _transition(
        self,
        session: Session,
        run: DeploymentRun,
        status: DeploymentStatus,
    ) -> None:
        """Атомарно меняет статус и фиксирует в БД."""
        prev = run.status
        run.status = status.value
        run.updated_at = datetime.utcnow()
        session.add(run)
        session.commit()
        logger.info(f"[Orch] {run.id[:8]}… {prev} → {status.value}")

    async def _fail(
        self,
        session: Session,
        run: DeploymentRun,
        error: str,
        phase: str,
    ) -> None:
        """Переводит deployment в FAILED с записью ошибки."""
        run.status = DeploymentStatus.FAILED.value
        run.error_message = f"[{phase}] {error}"
        run.completed_at = datetime.utcnow()
        session.add(run)
        await self._log(session, run.id, phase, f"ОШИБКА: {error}", level="ERROR")
        session.commit()
        logger.error(f"[Orch] ✗ Deployment {run.id} FAILED в фазе {phase}: {error}")

    async def _log(
        self,
        session: Session,
        deployment_id: str,
        phase: str,
        message: str,
        level: str = "INFO",
        server_ip: Optional[str] = None,
    ) -> None:
        """Добавляет одну запись в DeploymentLog."""
        entry = DeploymentLog(
            deployment_id=deployment_id,
            level=level,
            phase=phase,
            message=message,
            server_ip=server_ip,
        )
        session.add(entry)
        session.commit()

    async def _write_logs(
        self,
        session: Session,
        deployment_id: str,
        phase: str,
        messages: List[str],
    ) -> None:
        """Пакетная запись списка строк в DeploymentLog."""
        for msg in messages:
            if not msg.strip():
                continue
            level = "WARNING" if msg.strip().startswith("⚠") else \
                    "ERROR"   if "ОШИБКА" in msg or "FAIL" in msg else "INFO"
            entry = DeploymentLog(
                deployment_id=deployment_id,
                level=level,
                phase=phase,
                message=msg.strip(),
            )
            session.add(entry)
        session.commit()

    # ─────────────────────────────────────────────────────────────────
    # DB helper
    # ─────────────────────────────────────────────────────────────────

    def _get_session(self) -> Session:
        engine = get_db_engine(settings.get_database_sync_url())
        init_db(engine)
        return Session(engine)


# Глобальный singleton
orchestrator = DeploymentOrchestrator()
