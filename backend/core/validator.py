"""
PHASE 2: Configuration Validator

Валидирует cluster и license конфигурации перед развертыванием.
Multi-level: Pydantic → business logic → SSH connectivity.
"""

import logging
from typing import List, Optional

from ..models.license_models import LicenseRequestParams, ValidationResult
from ..models.schemas import ClusterInput, ClusterDeploymentRequest
from ..infrastructure.ssh_manager import ssh_manager

logger = logging.getLogger(__name__)


class ConfigValidator:
    """Валидатор конфигураций кластера и лицензии."""

    async def validate_license_config(
        self,
        license_config: LicenseRequestParams,
        cluster_config: ClusterInput,
    ) -> ValidationResult:
        """Проверить параметры лицензии против конфигурации кластера."""
        result = ValidationResult(valid=True)

        # Количество бэкендов
        expected_backends = len(cluster_config.backends)
        if license_config.cluster_backends != expected_backends:
            result.add_error(
                f"license_config.cluster_backends={license_config.cluster_backends} "
                f"не совпадает с количеством бэкендов ({expected_backends})"
            )

        # Количество фронтендов
        expected_frontends = len(cluster_config.frontends or [])
        if license_config.cluster_frontends != expected_frontends:
            result.add_error(
                f"license_config.cluster_frontends={license_config.cluster_frontends} "
                f"не совпадает с количеством фронтендов ({expected_frontends})"
            )

        # Минимальные accounts
        if license_config.licensed_accounts < 100:
            result.add_warning(
                f"licensed_accounts={license_config.licensed_accounts} — очень мало для продакшена"
            )

        # Resources vs accounts соотношение
        ratio = license_config.licensed_resources / license_config.licensed_accounts
        if ratio > 1.0:
            result.add_warning(
                f"licensed_resources ({license_config.licensed_resources}) > "
                f"licensed_accounts ({license_config.licensed_accounts}) — нетипичное соотношение"
            )

        logger.info(
            f"License валидация: valid={result.valid}, "
            f"errors={len(result.errors)}, warnings={len(result.warnings)}"
        )
        return result

    async def validate_cluster_config(
        self,
        cluster_config: ClusterInput,
        check_ssh: bool = False,
    ) -> ValidationResult:
        """Проверить конфигурацию кластера."""
        result = ValidationResult(valid=True)

        # Минимум 2 бэкенда
        if len(cluster_config.backends) < 2:
            result.add_error("Кластер требует минимум 2 бэкенда")

        # Уникальность IP
        all_ips: List[str] = [b.ip for b in cluster_config.backends]
        all_ips.append(cluster_config.database_server.ip)
        if cluster_config.nfs_server:
            all_ips.append(cluster_config.nfs_server.ip)

        if len(all_ips) != len(set(all_ips)):
            result.add_warning(
                "Несколько узлов имеют одинаковые IP — убедитесь, что это намеренно"
            )

        # Проверка SSH доступности (опционально)
        if check_ssh:
            all_servers = list(cluster_config.backends)
            all_servers.append(cluster_config.database_server)
            if cluster_config.frontends:
                all_servers.extend(cluster_config.frontends)
            if cluster_config.nfs_server:
                all_servers.append(cluster_config.nfs_server)

            for server in all_servers:
                ok, msg = await ssh_manager.check_connectivity(server)
                if not ok:
                    result.add_error(f"SSH недоступен [{server.ip}]: {msg}")
                else:
                    logger.debug(f"SSH OK: {server.ip}")

        logger.info(
            f"Cluster валидация: valid={result.valid}, "
            f"errors={len(result.errors)}, warnings={len(result.warnings)}"
        )
        return result

    def _validate_ivamail_package(self, request: ClusterDeploymentRequest) -> Optional[str]:
        """Без ivamail_version в репозиториях Debian нет пакета ivamail — нужен дистрибутив."""
        pc = request.resolved_package_config()
        ver = (request.cluster_config.ivamail_version or "").strip()
        if ver:
            return None
        if pc is None:
            return (
                "Не задан источник пакета IVA Mail: укажите package_config "
                "(файл, URL или путь на сервере) или ivamail_version с репозиторием вендора."
            )
        for b in request.cluster_config.backends:
            src = pc.get_source_for(b.ip)
            if src is None or not src.is_install_ready():
                return (
                    f"Для бэкенда {b.hostname} ({b.ip}) не указан полный источник дистрибутива "
                    "(или в режиме per-node нет записи для этого IP)."
                )
        return None

    async def validate_full_deployment(
        self,
        request: ClusterDeploymentRequest,
        check_ssh: bool = False,
    ) -> ValidationResult:
        """Полная валидация запроса развертывания."""
        cluster_validation = await self.validate_cluster_config(
            request.cluster_config, check_ssh=check_ssh
        )
        license_validation = await self.validate_license_config(
            request.license_config, request.cluster_config
        )
        result = cluster_validation.merge(license_validation)
        pkg_err = self._validate_ivamail_package(request)
        if pkg_err:
            result.add_error(pkg_err)
        logger.info(
            f"Полная валидация: valid={result.valid}, "
            f"errors={len(result.errors)}, warnings={len(result.warnings)}"
        )
        return result


# Глобальный экземпляр
validator = ConfigValidator()
