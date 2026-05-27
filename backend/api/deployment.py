"""
PHASE 1.5: Deployment API Router

POST /api/deployment/start    — стартует развертывание, возвращает deployment_id
GET  /api/deployment/{id}     — статус + последние логи
GET  /api/deployment/         — список всех развертываний
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models.schemas import ClusterDeploymentRequest, DeploymentStatus, DeploymentResponse
from ..models.db_models import DeploymentRun, DeploymentLog, HealthCheckResult, get_db_engine, init_db
from ..models.license_models import ValidationResult
from ..core.validator import validator
from ..core.orchestrator import orchestrator
from ..core.reporter import Reporter

logger = logging.getLogger(__name__)

router = APIRouter()

# ─── Утилита: синхронная сессия (SQLite без async для Phase 1.5) ───

def _get_session() -> Session:
    engine = get_db_engine(settings.get_database_sync_url())
    init_db(engine)
    return Session(engine)


# ─────────────────────────────────────────────
# Request / Response schemas
# ─────────────────────────────────────────────

class DeploymentLogEntry(BaseModel):
    id: int
    created_at: datetime
    level: str
    phase: Optional[str]
    message: str
    server_ip: Optional[str]

    class Config:
        from_attributes = True


class DeploymentDetail(BaseModel):
    deployment_id: str
    status: str
    created_at: datetime
    updated_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]
    logs: List[DeploymentLogEntry]

    class Config:
        from_attributes = True


class DeploymentListItem(BaseModel):
    deployment_id: str
    status: str
    created_at: datetime
    updated_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_seconds: Optional[int]
    licensed_accounts: Optional[int]

    class Config:
        from_attributes = True


class ClusterNodeStatus(BaseModel):
    ip: str
    hostname: str
    role: str
    last_check_ok: Optional[bool]
    last_check_at: Optional[datetime]
    uptime_since: Optional[str]


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@router.post(
    "/start",
    summary="Запустить развертывание кластера",
    response_model=DeploymentResponse,
    status_code=202,
)
async def start_deployment(request: ClusterDeploymentRequest) -> DeploymentResponse:
    """
    Принимает полную конфигурацию (cluster + license),
    валидирует её и создаёт запись в БД.

    Оркестратор (Phase 4) будет запущен отдельно.
    Сейчас возвращает deployment_id для последующего polling.
    """
    # Валидация без SSH (быстрая, синхронная)
    validation: ValidationResult = await validator.validate_full_deployment(
        request, check_ssh=False
    )

    if not validation.valid:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Конфигурация не прошла валидацию",
                "errors": validation.errors,
                "warnings": validation.warnings,
            },
        )

    # Создаём запись в БД
    deployment_id = str(uuid.uuid4())
    session = _get_session()
    try:
        run = DeploymentRun(
            id=deployment_id,
            status=DeploymentStatus.CONFIGURATION.value,
            cluster_config=request.cluster_config.model_dump(mode="json", exclude={"package_config"}),
            license_config=request.license_config.model_dump(mode="json"),
            package_config=(
                pc.model_dump(mode="json")
                if (pc := request.resolved_package_config()) is not None
                else None
            ),
        )
        session.add(run)

        # Лог о старте
        log_entry = DeploymentLog(
            deployment_id=deployment_id,
            level="INFO",
            phase="configuration",
            message="Развертывание создано. Ожидание запуска оркестратора.",
        )
        session.add(log_entry)

        # Предупреждения из валидации → в логи
        for warning in validation.warnings:
            session.add(DeploymentLog(
                deployment_id=deployment_id,
                level="WARNING",
                phase="configuration",
                message=warning,
            ))

        session.commit()
        logger.info(f"Deployment создан: {deployment_id}")
    finally:
        session.close()

    # Запускаем оркестратор в фоне — HTTP возвращает deployment_id немедленно
    asyncio.create_task(orchestrator.run(deployment_id))
    logger.info(f"Оркестратор запущен фоново для {deployment_id}")

    return DeploymentResponse(
        deployment_id=deployment_id,
        status=DeploymentStatus.CONFIGURATION,
        message=(
            f"Развертывание запущено. ID: {deployment_id}. "
            f"Предупреждений: {len(validation.warnings)}. "
            f"Статус: GET /api/deployment/{deployment_id}"
        ),
    )


@router.get(
    "/cluster/nodes",
    summary="Статус узлов кластера из последнего деплоя",
    response_model=List[ClusterNodeStatus],
)
async def get_cluster_nodes() -> List[ClusterNodeStatus]:
    """
    Возвращает список узлов кластера из последнего деплоя
    с результатами health-check проверок.
    """
    session = _get_session()
    try:
        run: Optional[DeploymentRun] = (
            session.query(DeploymentRun)
            .order_by(DeploymentRun.created_at.desc())
            .first()
        )
        if not run or not run.cluster_config:
            return []

        cc = run.cluster_config
        raw_nodes: List[dict] = []

        def _add(servers, role: str) -> None:
            if not servers:
                return
            if isinstance(servers, list):
                for s in servers:
                    raw_nodes.append({"ip": s.get("ip", ""), "hostname": s.get("hostname", ""), "role": role})
            else:
                raw_nodes.append({"ip": servers.get("ip", ""), "hostname": servers.get("hostname", ""), "role": role})

        _add(cc.get("backends"), "Backend")
        _add(cc.get("frontends"), "Frontend")
        _add(cc.get("database_server"), "Database/NFS")

        # Дедупликация по IP
        seen_ips: set = set()
        unique_nodes = []
        for n in raw_nodes:
            if n["ip"] not in seen_ips:
                seen_ips.add(n["ip"])
                unique_nodes.append(n)

        # Загружаем результаты health-check для этого деплоя
        checks = (
            session.query(HealthCheckResult)
            .filter(HealthCheckResult.deployment_id == run.id)
            .all()
        )

        # Группируем по IP
        by_ip: dict[str, list] = {}
        for c in checks:
            by_ip.setdefault(c.server_ip, []).append(c)

        result: List[ClusterNodeStatus] = []
        for node in unique_nodes:
            ip = node["ip"]
            ip_checks = by_ip.get(ip, [])
            if ip_checks:
                all_ok = all(c.success for c in ip_checks)
                last_at = max(c.checked_at for c in ip_checks)
                # uptime_since из ivamail check details
                uptime_since: Optional[str] = None
                for c in ip_checks:
                    if c.check_type == "ivamail" and c.details:
                        uptime_since = c.details.get("active_since") or None
                        break
            else:
                all_ok = None
                last_at = None
                uptime_since = None

            result.append(ClusterNodeStatus(
                ip=ip,
                hostname=node["hostname"],
                role=node["role"],
                last_check_ok=all_ok,
                last_check_at=last_at,
                uptime_since=uptime_since,
            ))

        return result
    finally:
        session.close()


@router.get(
    "/",
    summary="Список всех развертываний",
    response_model=List[DeploymentListItem],
)
async def list_deployments() -> List[DeploymentListItem]:
    """Возвращает последние 50 развертываний, сортировка — новые первыми."""
    session = _get_session()
    try:
        runs = (
            session.query(DeploymentRun)
            .order_by(DeploymentRun.created_at.desc())
            .limit(50)
            .all()
        )
        items = []
        for r in runs:
            duration: Optional[int] = None
            if r.created_at and r.completed_at:
                duration = int((r.completed_at - r.created_at).total_seconds())

            accounts: Optional[int] = None
            if r.license_config and isinstance(r.license_config, dict):
                accounts = r.license_config.get("licensed_accounts")

            items.append(DeploymentListItem(
                deployment_id=r.id,
                status=r.status,
                created_at=r.created_at,
                updated_at=r.updated_at,
                completed_at=r.completed_at,
                duration_seconds=duration,
                licensed_accounts=accounts,
            ))
        return items
    finally:
        session.close()


@router.get(
    "/{deployment_id}",
    summary="Статус и логи развертывания",
    response_model=DeploymentDetail,
)
async def get_deployment(deployment_id: str) -> DeploymentDetail:
    """Возвращает текущий статус + последние 100 записей лога."""
    session = _get_session()
    try:
        run: Optional[DeploymentRun] = session.get(DeploymentRun, deployment_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Deployment {deployment_id} не найден")

        logs = (
            session.query(DeploymentLog)
            .filter(DeploymentLog.deployment_id == deployment_id)
            .order_by(DeploymentLog.created_at.desc())
            .limit(100)
            .all()
        )

        return DeploymentDetail(
            deployment_id=run.id,
            status=run.status,
            created_at=run.created_at,
            updated_at=run.updated_at,
            completed_at=run.completed_at,
            error_message=run.error_message,
            logs=[
                DeploymentLogEntry(
                    id=l.id,
                    created_at=l.created_at,
                    level=l.level,
                    phase=l.phase,
                    message=l.message,
                    server_ip=l.server_ip,
                )
                for l in logs
            ],
        )
    finally:
        session.close()


@router.get(
    "/{deployment_id}/report",
    summary="HTML-отчёт о развертывании",
    response_class=HTMLResponse,
)
async def get_deployment_report(deployment_id: str) -> HTMLResponse:
    """Возвращает HTML-отчёт, сгенерированный после завершения развертывания."""
    session = _get_session()
    try:
        run = session.get(DeploymentRun, deployment_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Deployment {deployment_id} не найден")
        if not run.report_html:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Отчёт для deployment {deployment_id} ещё не сгенерирован. "
                    f"Текущий статус: {run.status}"
                )
            )
        return HTMLResponse(content=run.report_html)
    finally:
        session.close()


@router.post(
    "/{deployment_id}/report/regenerate",
    summary="Перегенерировать HTML-отчёт из текущего состояния БД",
)
async def regenerate_report(deployment_id: str) -> dict:
    """
    Пересчитывает HTML-отчёт на основе текущих данных в БД (статус, логи, health checks).
    Полезно если отчёт был сохранён с неверным статусом (например до перехода в SUCCESS).
    """
    session = _get_session()
    try:
        run = session.get(DeploymentRun, deployment_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Deployment {deployment_id} не найден")

        all_logs = (
            session.query(DeploymentLog)
            .filter(DeploymentLog.deployment_id == run.id)
            .order_by(DeploymentLog.created_at)
            .all()
        )
        health_results = (
            session.query(HealthCheckResult)
            .filter(HealthCheckResult.deployment_id == run.id)
            .all()
        )

        # Строим health_summary в формате HealthChecker.build_summary()
        passed = sum(1 for h in health_results if h.success)
        total  = len(health_results)

        by_type: dict = {}
        for h in health_results:
            if h.check_type not in by_type:
                by_type[h.check_type] = {"passed": 0, "failed": 0, "servers": []}
            entry = by_type[h.check_type]
            if h.success:
                entry["passed"] += 1
            else:
                entry["failed"] += 1
            entry["servers"].append({
                "ip": h.server_ip,
                "hostname": "",
                "ok": h.success,
                "error": h.error_message,
            })

        health_summary = {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "all_ok": passed == total,
            "by_type": by_type,
        }

        reporter = Reporter()
        html = reporter.generate(run, all_logs, health_results, health_summary)
        reporter.save_to_run(session, run, html)

        return {"ok": True, "deployment_id": deployment_id, "html_length": len(html)}
    finally:
        session.close()
