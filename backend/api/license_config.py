"""
PHASE 1.5: License Config API Router

GET  /api/license-config/schema    — JSON Schema для динамической формы
POST /api/license-config/validate  — валидация license_config vs cluster_config
POST /api/license-config/estimate  — рекомендуемые параметры по размеру кластера
"""

import logging
import math
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..models.license_models import LicenseRequestParams, ValidationResult
from ..models.schemas import ClusterInput
from ..core.validator import validator

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────
# Request / Response schemas
# ─────────────────────────────────────────────

class ValidateLicenseRequest(BaseModel):
    license_config: LicenseRequestParams
    cluster_config: ClusterInput


class EstimateRequest(BaseModel):
    cluster_config: ClusterInput


class EstimateResponse(BaseModel):
    recommended: LicenseRequestParams
    notes: list[str]


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@router.get(
    "/schema",
    summary="JSON Schema полей формы лицензии",
    response_model=Dict[str, Any],
)
async def get_license_schema() -> Dict[str, Any]:
    """
    Возвращает JSON Schema для LicenseRequestParams.
    Frontend использует её для динамической генерации формы с подсказками.
    """
    schema = LicenseRequestParams.model_json_schema()
    logger.debug("Возвращаем JSON Schema для LicenseRequestParams")
    return schema


@router.post(
    "/validate",
    summary="Валидация параметров лицензии против кластера",
    response_model=ValidationResult,
)
async def validate_license(body: ValidateLicenseRequest) -> ValidationResult:
    """
    Проверяет совместимость license_config и cluster_config:
    - cluster_backends совпадает с len(backends)
    - cluster_frontends совпадает с len(frontends)
    - licensed_accounts / resources в разумных пределах
    """
    result = await validator.validate_license_config(
        body.license_config,
        body.cluster_config,
    )
    logger.info(
        f"validate_license: valid={result.valid} "
        f"errors={len(result.errors)} warnings={len(result.warnings)}"
    )
    return result


# ─────────────────────────────────────────────
# Sizing tables
# ─────────────────────────────────────────────

# Таблица ресурсных аккаунтов (общие ящики, переговорки)
_RESOURCE_TABLE = [
    (100,   10),
    (1_000,  50),
    (5_000, 100),
    (10_000, 500),
]

# Таблица аппаратных требований на 1 узел бэкенда
_SIZING_TABLE = [
    {"accounts": 100,    "vCPU": 1, "ram_gb": 2,  "hdd_gb": 20,  "iops": 125},
    {"accounts": 500,    "vCPU": 2, "ram_gb": 4,  "hdd_gb": 40,  "iops": 625},
    {"accounts": 1_000,  "vCPU": 2, "ram_gb": 4,  "hdd_gb": 40,  "iops": 1_250},
    {"accounts": 3_000,  "vCPU": 4, "ram_gb": 6,  "hdd_gb": 60,  "iops": 3_750},
    {"accounts": 5_000,  "vCPU": 4, "ram_gb": 8,  "hdd_gb": 60,  "iops": 6_250},
    {"accounts": 10_000, "vCPU": 6, "ram_gb": 10, "hdd_gb": 100, "iops": 12_500},
]


def _calc_resource_accounts(accounts: int) -> int:
    """Lookup-таблица ресурсных аккаунтов (не эвристика)."""
    for threshold, resources in _RESOURCE_TABLE:
        if accounts <= threshold:
            return resources
    return math.ceil(accounts / 10_000) * 500


def _get_sizing(accounts: int) -> dict:
    """Найти строку sizing-таблицы для заданного числа аккаунтов (ceiling)."""
    for row in _SIZING_TABLE:
        if accounts <= row["accounts"]:
            return row
    return _SIZING_TABLE[-1]


@router.post(
    "/estimate",
    summary="Рекомендуемые параметры лицензии по размеру кластера",
    response_model=EstimateResponse,
)
async def estimate_license(body: EstimateRequest) -> EstimateResponse:
    """
    По конфигурации кластера предлагает стартовые значения для лицензии.
    Использует lookup-таблицы; администратор может скорректировать значения.

    Ресурсные аккаунты = общие ящики, переговорки и т.д. (не IOPS).
    Число узлов = ceil(accounts / 5 000) на каждую роль.
    """
    cluster = body.cluster_config
    n_backends  = len(cluster.backends)
    n_frontends = len(cluster.frontends or [])

    # Вычисляем предполагаемое число аккаунтов из размера кластера
    licensed_accounts = max(n_backends, n_frontends) * 5_000
    if licensed_accounts == 0:
        licensed_accounts = 10_000

    resource_accounts     = _calc_resource_accounts(licensed_accounts)
    recommended_backends  = math.ceil(licensed_accounts / 5_000)
    recommended_frontends = math.ceil(licensed_accounts / 5_000)
    sizing                = _get_sizing(licensed_accounts)

    notes: list[str] = [
        f"Кластер: {n_backends} бэкенд(ов), {n_frontends} фронтенд(а/ов) → ~{licensed_accounts:,} аккаунтов.",
        f"Ресурсные аккаунты (общие ящики, переговорки): {resource_accounts}.",
        f"Рекомендуемые требования на узел бэкенда: "
        f"{sizing['vCPU']} vCPU, {sizing['ram_gb']} ГБ RAM, "
        f"{sizing['hdd_gb']} ГБ HDD, {sizing['iops']:,} IOPS.",
        f"Минимум: 4 vCPU, 8 ГБ RAM, {sizing['hdd_gb']} ГБ HDD, {sizing['iops']:,} IOPS.",
    ]

    recommended = LicenseRequestParams(
        licensed_accounts=licensed_accounts,
        cluster_backends=recommended_backends,
        cluster_frontends=recommended_frontends,
        licensed_resources=resource_accounts,
        licensee_name="Введите название организации",
        licensee_name_eng="Enter organization name",
    )

    logger.info(
        "estimate_license: backends=%d frontends=%d → accounts=%d resources=%d",
        n_backends, n_frontends, licensed_accounts, resource_accounts,
    )
    return EstimateResponse(recommended=recommended, notes=notes)
