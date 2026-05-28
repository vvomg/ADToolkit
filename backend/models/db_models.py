"""
PHASE 0.3: SQLAlchemy Database Models

Модели для хранения в SQLite информации о развертываниях и лицензиях.
"""

from pathlib import Path

from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    Text,
    Boolean,
    JSON,
    ForeignKey,
    Enum as SAEnum,
    inspect,
    text,
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from datetime import datetime
import uuid

Base = declarative_base()


def _gen_uuid() -> str:
    return str(uuid.uuid4())


class DeploymentRun(Base):
    """
    Основная запись о запуске развертывания.
    """
    __tablename__ = "deployment_runs"

    id = Column(String(36), primary_key=True, default=_gen_uuid)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = Column(String(50), nullable=False, default="configuration")
    cluster_config = Column(JSON, nullable=True)   # ClusterInput as JSON
    license_config = Column(JSON, nullable=True)   # LicenseRequestParams as JSON
    package_config = Column(JSON, nullable=True)   # PackageConfig as JSON
    error_message = Column(Text, nullable=True)
    report_html = Column(Text, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    logs = relationship("DeploymentLog", back_populates="deployment", cascade="all, delete-orphan")
    health_checks = relationship("HealthCheckResult", back_populates="deployment", cascade="all, delete-orphan")
    license_requests = relationship("LicenseRequest", back_populates="deployment", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<DeploymentRun id={self.id} status={self.status}>"


class DeploymentLog(Base):
    """
    Лог отдельного события в процессе развертывания.
    """
    __tablename__ = "deployment_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    deployment_id = Column(String(36), ForeignKey("deployment_runs.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    level = Column(String(10), default="INFO")  # DEBUG, INFO, WARNING, ERROR
    phase = Column(String(50), nullable=True)
    message = Column(Text, nullable=False)
    server_ip = Column(String(45), nullable=True)
    command = Column(Text, nullable=True)
    output = Column(Text, nullable=True)

    # Relationship
    deployment = relationship("DeploymentRun", back_populates="logs")

    def __repr__(self) -> str:
        return f"<DeploymentLog id={self.id} level={self.level} phase={self.phase}>"


class HealthCheckResult(Base):
    """
    Результат проверки здоровья узла кластера.
    """
    __tablename__ = "health_check_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    deployment_id = Column(String(36), ForeignKey("deployment_runs.id"), nullable=False)
    checked_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    server_ip = Column(String(45), nullable=False)
    server_hostname = Column(String(255), nullable=True)
    check_type = Column(String(50), nullable=False)  # ivamail, postgresql, nfs, ssh
    success = Column(Boolean, nullable=False, default=False)
    details = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    # Relationship
    deployment = relationship("DeploymentRun", back_populates="health_checks")

    def __repr__(self) -> str:
        return f"<HealthCheckResult server={self.server_ip} type={self.check_type} ok={self.success}>"


class LicenseRequest(Base):
    """
    Запись о запросе лицензии (CMD-протокол).
    """
    __tablename__ = "license_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    deployment_id = Column(String(36), ForeignKey("deployment_runs.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    server_ip = Column(String(45), nullable=False)
    request_data = Column(JSON, nullable=True)    # Raw CMD response
    license_file_path = Column(String(500), nullable=True)
    installed = Column(Boolean, default=False)
    installed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    # Relationship
    deployment = relationship("DeploymentRun", back_populates="license_requests")

    def __repr__(self) -> str:
        return f"<LicenseRequest id={self.id} server={self.server_ip} installed={self.installed}>"


class MonitorCluster(Base):
    """Кластер — логическая группа нод мониторинга."""
    __tablename__ = "monitor_clusters"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(String(100), nullable=False)
    color       = Column(String(20), default="blue")   # blue|green|yellow|red|mauve|peach|teal|sapphire|lavender
    description = Column(Text, nullable=True)
    sort_order  = Column(Integer, default=0)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    nodes = relationship("MonitorNode", back_populates="cluster")

    def __repr__(self) -> str:
        return f"<MonitorCluster id={self.id} name={self.name}>"


class ConfigApplyHistory(Base):
    """История применения конфигурационных профилей на ноды."""
    __tablename__ = "config_apply_history"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    applied_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    profile_slug    = Column(String(100), nullable=False)
    profile_name    = Column(String(200), nullable=False)
    # 'cmd' | 'ansible'
    apply_mode      = Column(String(20), nullable=False)
    playbook_path   = Column(Text, nullable=True)
    # JSON lists / dicts
    target_hosts    = Column(JSON, nullable=False, default=list)
    modules_applied = Column(JSON, nullable=False, default=list)
    # {"10.3.6.206": "3.2.1", ...}
    node_versions   = Column(JSON, nullable=True)
    # 'ok' | 'partial' | 'failed'
    status          = Column(String(20), nullable=False, default="ok")
    # ["10.3.6.207: SMTP → FAILED: ..."]
    errors          = Column(JSON, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ConfigApplyHistory id={self.id} profile={self.profile_slug!r} "
            f"status={self.status} hosts={self.target_hosts}>"
        )


class MonitorNode(Base):
    """Реестр нод для мониторинга на дашборде."""
    __tablename__ = "monitor_nodes"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    ip            = Column(String(45), nullable=False, unique=True)
    hostname      = Column(String(255), nullable=True)    # авто: "be-10-3-6-206"
    display_name  = Column(String(100), nullable=True)    # произвольное имя
    # node_type: ivamail_backend|ivamail_frontend|nfs|nfs_backup|haproxy|load_balancer
    #            |monitoring|monitoring_prometheus|monitoring_grafana|monitoring_graylog
    node_type     = Column(String(40), nullable=False)
    cluster_id    = Column(Integer, ForeignKey("monitor_clusters.id", ondelete="SET NULL"), nullable=True)
    ssh_user      = Column(String(100), default="user")
    ssh_auth_mode = Column(String(10), default="password")   # "password"|"key"
    ssh_password  = Column(Text, nullable=True)
    ssh_key_path  = Column(Text, nullable=True)
    ssh_port      = Column(Integer, default=22)
    cmd_user      = Column(String(100), default="admin")     # только для ivamail_*
    cmd_password  = Column(Text, default="admin")            # только для ivamail_*
    sort_order           = Column(Integer, default=0)
    cmd_commands         = Column(Text,        nullable=True)    # JSON: [{name, syntax, section, description, available, documented}]
    cmd_help_fetched_at  = Column(DateTime,    nullable=True)    # когда последний раз вызывали HELP
    cmd_server_version   = Column(String(100), nullable=True)    # из SystemInfo["Server version"]
    cmd_cluster_status   = Column(String(50),  nullable=True)    # из SystemInfo["Cluster Status"]
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    cluster = relationship("MonitorCluster", back_populates="nodes")

    def __repr__(self) -> str:
        return f"<MonitorNode id={self.id} ip={self.ip} type={self.node_type}>"


def _normalize_sync_sqlite_url(database_url: str) -> str:
    """Полный sync-URL для create_engine (без sqlite+aiosqlite)."""
    u = database_url.strip()
    if "://" not in u:
        return f"sqlite:///{u}"
    if u.startswith("sqlite+aiosqlite:"):
        return "sqlite:" + u[len("sqlite+aiosqlite:") :]
    return u


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    """Создать родительскую директорию для файла SQLite, если нужно."""
    url = make_url(database_url)
    if url.drivername != "sqlite":
        return
    dbname = url.database
    if not dbname or dbname == ":memory:" or dbname.startswith("file::memory"):
        return
    path = Path(dbname)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)


_engine_cache: dict = {}


def get_db_engine(database_url: str = "sqlite:///./ivamail_deploy.db"):
    """Синхронный движок SQLAlchemy — singleton per URL."""
    sync_url = _normalize_sync_sqlite_url(database_url)
    if sync_url in _engine_cache:
        return _engine_cache[sync_url]
    _ensure_sqlite_parent_dir(sync_url)
    engine = create_engine(
        sync_url,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    _engine_cache[sync_url] = engine
    return engine


def _upgrade_sqlite_schema(engine) -> None:
    """
    Добавить в SQLite недостающие столбцы.
    create_all() не меняет уже существующие таблицы — после смены моделей нужен ADD COLUMN.
    """
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if column.primary_key:
                    continue
                if not column.nullable and column.server_default is None:
                    # SQLite: ADD COLUMN NOT NULL без DEFAULT несовместим со старыми строками
                    continue
                coltype = column.type.compile(dialect=engine.dialect)
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {coltype}'
                conn.execute(text(ddl))
                existing.add(column.name)


def init_db(engine):
    """Инициализировать БД — создать все таблицы и подтянуть схему SQLite."""
    Base.metadata.create_all(bind=engine)
    _upgrade_sqlite_schema(engine)
