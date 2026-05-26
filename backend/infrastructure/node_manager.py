"""
Node Manager — управление запуском и кластерной конфигурацией узлов IVA Mail.

Отвечает за:
  1. NODE_STARTUP  — последовательный запуск всех нод без аргументов
  2. CLUSTER_CONFIG — параллельная кластерная конфигурация через CMD
  3. REMAINING_NODES — создание parameters.conf + перезапуск нод в кластерном режиме
"""

import asyncio
import logging
from typing import List

from ..infrastructure.ssh_manager import SSHManager
from ..infrastructure.cmd_client import CMDClient, CMDConnectionError
from ..models.schemas import ClusterInput, ServerConfig
from ..core.config import settings

logger = logging.getLogger(__name__)

CMD_USER = "admin"
CMD_PASS = "admin"


class NodeManager:
    def __init__(self, ssh: SSHManager):
        self._ssh = ssh

    async def start_all_nodes(self, cluster: ClusterInput) -> List[str]:
        logs: List[str] = []

        for server in cluster.backends:
            result = await self._ssh.execute_command(server, "systemctl start ivamail")
            if not result.success:
                raise RuntimeError(
                    f"[NodeMgr] Не удалось запустить ivamail на {server.hostname}: {result.stderr}"
                )
            logs.append(f"[{server.hostname}] ivamail запущен")
            await self._wait_for_cmd_port(server)
            await asyncio.sleep(cluster.node_startup_delay_seconds)

        for server in (cluster.frontends or []):
            result = await self._ssh.execute_command(server, "systemctl start ivamail")
            if not result.success:
                raise RuntimeError(
                    f"[NodeMgr] Не удалось запустить ivamail на {server.hostname}: {result.stderr}"
                )
            logs.append(f"[{server.hostname}] ivamail запущен")
            await self._wait_for_cmd_port(server)
            await asyncio.sleep(cluster.node_startup_delay_seconds)

        return logs

    async def configure_cluster_nodes(
        self,
        cluster: ClusterInput,
    ) -> List[str]:
        backend_addrs = [f"/I [{b.ip}]" for b in cluster.backends]
        frontend_addrs = [f"/I [{f.ip}]" for f in (cluster.frontends or [])]

        all_nodes = list(cluster.backends) + list(cluster.frontends or [])
        logs: List[str] = []

        async def _configure_one(server: ServerConfig) -> str:
            key_val_pairs = [
                "BackendList", backend_addrs,
                "FrontendList", frontend_addrs,
                "OwnAddress", f"/I [{server.ip}]",
                "Password", CMD_PASS,
            ]
            async with CMDClient(server.ip, settings.ivamail_port) as cmd:
                await cmd.authenticate(CMD_USER, CMD_PASS)
                response = await cmd.module_update_config("Cluster", [], key_val_pairs)
                if not response.ok:
                    raise RuntimeError(
                        f"[NodeMgr] ModuleUpdateConfig failed on {server.hostname}: {response.text}"
                    )
            return (
                f"[{server.hostname}] Кластерная конфигурация применена "
                f"(BackendList: {len(backend_addrs)} бэкендов)"
            )

        tasks = [_configure_one(server) for server in all_nodes]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        errors = []
        for server, result in zip(all_nodes, results):
            if isinstance(result, Exception):
                errors.append(f"{server.hostname}: {result}")
            else:
                logs.append(result)

        if errors:
            raise RuntimeError(
                "[NodeMgr] Ошибки кластерной конфигурации:\n" +
                "\n".join(f"  • {e}" for e in errors)
            )

        return logs

    async def restart_in_cluster_mode(
        self,
        cluster: ClusterInput,
    ) -> List[str]:
        logs: List[str] = []

        for server in cluster.backends:
            result = await self._ssh.execute_command(
                server,
                "printf 'ARGS=\"--backend\"\\n' > /var/ivamail/parameters.conf",
            )
            if not result.success:
                raise RuntimeError(
                    f"[NodeMgr] Не удалось создать parameters.conf на {server.hostname}: {result.stderr}"
                )
            logs.append(f"[{server.hostname}] parameters.conf создан (--backend)")

        for server in (cluster.frontends or []):
            result = await self._ssh.execute_command(
                server,
                "printf 'ARGS=\"--frontend\"\\n' > /var/ivamail/parameters.conf",
            )
            if not result.success:
                raise RuntimeError(
                    f"[NodeMgr] Не удалось создать parameters.conf на {server.hostname}: {result.stderr}"
                )
            logs.append(f"[{server.hostname}] parameters.conf создан (--frontend)")

        for i, server in enumerate(cluster.backends):
            restart = await self._ssh.execute_command(server, "systemctl restart ivamail")
            await self._wait_for_cmd_port(server)
            await self._verify_node_ready(server)
            logs.append(f"[{server.hostname}] перезапущен в кластерном режиме (--backend) ✓")
            await asyncio.sleep(cluster.node_startup_delay_seconds)

        for server in (cluster.frontends or []):
            restart = await self._ssh.execute_command(server, "systemctl restart ivamail")
            await self._wait_for_cmd_port(server)
            logs.append(f"[{server.hostname}] перезапущен в кластерном режиме (--frontend) ✓")
            await asyncio.sleep(cluster.node_startup_delay_seconds)

        return logs

    async def _wait_for_cmd_port(
        self,
        server: ServerConfig,
        max_wait: int = 60,
        interval: int = 3,
    ) -> None:
        for attempt in range(max_wait // interval):
            result = await self._ssh.execute_command(
                server,
                f"bash -c 'echo > /dev/tcp/127.0.0.1/{settings.ivamail_port}' 2>/dev/null && echo open || echo closed"
            )
            if "open" in result.stdout:
                logger.info(
                    f"[NodeMgr] CMD-порт {settings.ivamail_port} на {server.hostname} "
                    f"доступен (попытка {attempt + 1})"
                )
                return
            await asyncio.sleep(interval)

        raise RuntimeError(
            f"[NodeMgr] CMD-порт {settings.ivamail_port} на {server.hostname} "
            f"не открылся за {max_wait}с"
        )

    async def _verify_node_ready(
        self,
        server: ServerConfig,
        retries: int = 5,
        retry_interval: float = 3.0,
    ) -> None:
        for attempt in range(1, retries + 1):
            try:
                async with CMDClient(server.ip, settings.ivamail_port) as cmd:
                    await cmd.authenticate(CMD_USER, CMD_PASS)
                    if await cmd.ping():
                        logger.info(
                            f"[NodeMgr] CMD PING OK: {server.hostname} "
                            f"(попытка {attempt}/{retries})"
                        )
                        return
            except Exception as e:
                logger.debug(
                    f"[NodeMgr] CMD PING неудача на {server.hostname} "
                    f"(попытка {attempt}/{retries}): {e}"
                )
            if attempt < retries:
                await asyncio.sleep(retry_interval)

        raise RuntimeError(
            f"[NodeMgr] CMD-модуль на {server.hostname} не ответил на PING "
            f"за {retries} попыток"
        )
