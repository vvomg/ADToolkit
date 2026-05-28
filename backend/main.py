"""
ADToolKit — FastAPI entry point
Запуск: uvicorn backend.main:app --reload --port 8000
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import deployment, license_config, license_upload, ssh_key, stream, credentials, config_store, package, cluster, monitor, profiles, config_history

app = FastAPI(
    title="ADToolKit API",
    description="IVA Mail cluster deployment orchestrator",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite dev
        "http://localhost:3000",
        "http://10.3.6.100",
        "http://10.3.6.100:80",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(deployment.router,      prefix="/api/deployment",     tags=["deployment"])
app.include_router(license_config.router,  prefix="/api/license-config", tags=["license"])
app.include_router(license_upload.router,  prefix="/api/deployment",     tags=["license"])
app.include_router(ssh_key.router,         prefix="/api/ssh-key",        tags=["ssh"])
app.include_router(stream.router,          prefix="/api/deployment",     tags=["stream"])
app.include_router(credentials.router,     prefix="/api/credentials",    tags=["credentials"])
app.include_router(config_store.router,                                      tags=["config"])
app.include_router(profiles.router,                                          tags=["profiles"])
app.include_router(package.router,         prefix="/api/package",          tags=["package"])
app.include_router(cluster.router,         prefix="/api/cluster",           tags=["cluster"])
app.include_router(monitor.router,         prefix="/api/monitor",           tags=["monitor"])
app.include_router(config_history.router,                                    tags=["config-history"])


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "version": "0.1.0"}
