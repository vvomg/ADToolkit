"""
PHASE 1.2: Application Configuration

Environment-based settings через Pydantic BaseSettings.
"""

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # App
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    app_name: str = "IVA Mail Deploy Toolkit"
    app_version: str = "0.1.1"

    # Database
    database_url: str = "sqlite+aiosqlite:///./ivamail_deploy.db"

    # Security
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"

    # CORS
    cors_origins: List[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
    ]

    # Deployment settings
    deployment_timeout_seconds: int = 3600
    license_request_timeout_seconds: int = 86400
    ssh_connection_timeout: int = 30
    ssh_command_timeout: int = 300

    # SSH retry
    ssh_max_retries: int = 3
    ssh_retry_delay: int = 2

    # Paths
    templates_dir: str = "./app/templates"
    static_dir: str = "./static"
    upload_dir: str = "./uploads"

    # Logging
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # NFS
    nfs_version: str = "3"
    nfs_mount_options: str = "nfsvers=3,defaults"
    # Точка монтирования NFS на бэкендах (инструкция §5)
    nfs_client_mount_point: str = "/mnt/mailshare"
    # Симлинк IVA Mail → NFS (инструкция §5.1)
    nfs_ivamail_symlink: str = "/var/ivamail/Cluster"

    # PostgreSQL
    postgres_port: int = 5432
    postgres_init_sql: str = "/opt/ivamail/sql/init.sql"

    # IVA Mail
    ivamail_port: int = 106
    ivamail_systemd_service: str = "ivamail"
    ivamail_data_dir: str = "/var/ivamail"
    ivamail_config_dir: str = "/etc/ivamail"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    def get_database_sync_url(self) -> str:
        """Synchronous URL для SQLAlchemy (миграции)."""
        u = self.database_url.strip()
        if u.startswith("sqlite+aiosqlite:"):
            return "sqlite:" + u[len("sqlite+aiosqlite:") :]
        return u


# Глобальный singleton
settings = Settings()
