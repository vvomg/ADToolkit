"""
Cluster Configuration Schemas + Package Source Models
"""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from enum import Enum
from .license_models import LicenseRequestParams


# ─── Enums ────────────────────────────────────────────────────────

class ServerRole(str, Enum):
    BACKEND    = "backend"
    FRONTEND   = "frontend"
    DATABASE   = "database"
    NFS        = "nfs"
    HAPROXY    = "haproxy"
    MONITORING = "monitoring"


class PackageSourceType(str, Enum):
    LOCAL_FILE  = "local_file"
    URL         = "url"
    SERVER_PATH = "server_path"


class PackageFormat(str, Enum):
    DEB  = "deb"
    RPM  = "rpm"
    AUTO = "auto"


class DeploymentStatus(str, Enum):
    CONFIGURATION  = "configuration"
    PREFLIGHT      = "preflight"
    INFRA_SETUP    = "infra_setup"
    NODE_STARTUP   = "node_startup"
    CLUSTER_CONFIG = "cluster_config"
    LICENSE_REQUEST  = "license_request"
    WAITING_LICENSE  = "waiting_license"
    LICENSE_INSTALL  = "license_install"
    REMAINING_NODES  = "remaining_nodes"
    HAPROXY_SETUP    = "haproxy_setup"
    HEALTH_CHECKS    = "health_checks"
    MONITORING_SETUP = "monitoring_setup"
    REPORTING        = "reporting"
    SUCCESS        = "success"
    FAILED         = "failed"


# ─── Package Source ────────────────────────────────────────────────

class PackageSource(BaseModel):
    source_type:    PackageSourceType = Field(description="Тип источника")
    local_path:     Optional[str]     = Field(default=None)
    url:            Optional[str]     = Field(default=None)
    server_path:    Optional[str]     = Field(default=None)
    package_format: PackageFormat     = Field(default=PackageFormat.AUTO)
    filename:       Optional[str]     = Field(default=None)

    def get_filename(self) -> str:
        if self.filename:
            return self.filename
        if self.local_path:
            from pathlib import Path
            return Path(self.local_path).name
        if self.url:
            return self.url.split("/")[-1].split("?")[0] or "ivamail_package"
        if self.server_path:
            from pathlib import Path
            return Path(self.server_path).name
        return "ivamail_package"

    def detect_format(self) -> PackageFormat:
        if self.package_format != PackageFormat.AUTO:
            return self.package_format
        name = self.get_filename().lower()
        if name.endswith(".deb"):
            return PackageFormat.DEB
        if name.endswith(".rpm"):
            return PackageFormat.RPM
        return PackageFormat.DEB

    def is_install_ready(self) -> bool:
        """Достаточно ли данных для установки с этого источника."""
        if self.source_type == PackageSourceType.LOCAL_FILE:
            return bool(self.local_path and str(self.local_path).strip())
        if self.source_type == PackageSourceType.URL:
            return bool(
                (self.local_path and str(self.local_path).strip())
                or (self.url and str(self.url).strip())
            )
        if self.source_type == PackageSourceType.SERVER_PATH:
            return bool(self.server_path and str(self.server_path).strip())
        return False


class NodePackageSource(BaseModel):
    server_ip: str           = Field(description="IP сервера")
    source:    PackageSource = Field(description="Источник пакета")


class PackageConfig(BaseModel):
    default_source: Optional[PackageSource]         = Field(default=None)
    node_sources:   Optional[List[NodePackageSource]] = Field(default=None)
    per_node_mode:  bool                             = Field(default=False)

    def get_source_for(self, server_ip: str) -> Optional[PackageSource]:
        if self.per_node_mode:
            if self.node_sources:
                for ns in self.node_sources:
                    if ns.server_ip == server_ip:
                        return ns.source
                return None
            return self.default_source
        return self.default_source


# ─── Server & Cluster ─────────────────────────────────────────────

class ServerConfig(BaseModel):
    ip:           str           = Field(description="IP адрес сервера")
    hostname:     str           = Field(description="Hostname сервера")
    ssh_user:     str           = Field(default="root")
    ssh_password: Optional[str] = Field(default=None)
    ssh_key_path: Optional[str] = Field(default=None)
    ssh_port:     int           = Field(default=22, ge=1, le=65535)
    role:         ServerRole    = Field(description="Роль сервера")

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        parts = v.split(".")
        if len(parts) != 4:
            raise ValueError(f"Некорректный IP адрес: {v}")
        for part in parts:
            if not part.isdigit() or not 0 <= int(part) <= 255:
                raise ValueError(f"Некорректный IP адрес: {v}")
        return v

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, v: str) -> str:
        import re
        if not re.match(r"^[a-zA-Z0-9\-\.]+$", v):
            raise ValueError(f"Некорректный hostname: {v}")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "ip": "10.3.6.128",
                "hostname": "ivamail-backend-1",
                "ssh_user": "root",
                "ssh_port": 22,
                "role": "backend",
            }
        }


class ClusterInput(BaseModel):
    backends:       List[ServerConfig]           = Field(min_length=2)
    frontends:      Optional[List[ServerConfig]] = Field(default=None)
    database_server: ServerConfig                = Field()
    nfs_server:      Optional[ServerConfig]       = Field(default=None)
    haproxy_servers:    List[ServerConfig]         = Field(default_factory=list)
    monitoring_servers: List[ServerConfig]         = Field(default_factory=list)
    ivamail_version: Optional[str]               = Field(default='')
    nfs_share_path:  str                         = Field(default="/srv/nfs/nfsshared")
    nfs_mount_point: str                         = Field(default="/var/ivamail/nfsshared")
    node_startup_delay_seconds: int = Field(default=5, ge=0, le=120, description="Пауза (с) перед CMD:LicenseInstall на каждом запущенном узле")
    # Дублирование package_config внутри cluster (UI/совместимость); при сохранении в БД выносится в колонку package_config
    package_config: Optional[PackageConfig]    = Field(default=None)

    @field_validator("backends")
    @classmethod
    def validate_unique_ips(cls, backends: List[ServerConfig]) -> List[ServerConfig]:
        ips = [b.ip for b in backends]
        if len(ips) != len(set(ips)):
            raise ValueError("IP адреса бэкендов должны быть уникальными")
        hostnames = [b.hostname for b in backends]
        if len(hostnames) != len(set(hostnames)):
            raise ValueError("Hostnames бэкендов должны быть уникальными")
        return backends


# ─── Deployment Request ───────────────────────────────────────────

class ClusterDeploymentRequest(BaseModel):
    cluster_config:  ClusterInput          = Field()
    license_config:  LicenseRequestParams  = Field()
    package_config:  Optional[PackageConfig] = Field(default=None)

    def resolved_package_config(self) -> Optional[PackageConfig]:
        """Источник пакета: верхний уровень или вложенный в cluster_config."""
        return self.package_config or self.cluster_config.package_config

    class Config:
        json_schema_extra = {"description": "Complete deployment request"}


# ─── Responses ────────────────────────────────────────────────────

class DeploymentResponse(BaseModel):
    deployment_id: str              = Field()
    status:        DeploymentStatus = Field(default=DeploymentStatus.CONFIGURATION)
    message:       str              = Field()
