"""
API package — FastAPI роутеры Phase 1.5 + 3.5
"""

from .license_config import router as license_config_router
from .deployment import router as deployment_router
from .license_upload import router as license_upload_router
from .ssh_key import router as ssh_key_router
from .package import router as package_router

__all__ = [
    "license_config_router",
    "deployment_router",
    "license_upload_router",
    "ssh_key_router",
    "package_router",
]
