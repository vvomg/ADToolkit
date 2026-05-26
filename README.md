# ADToolKit

Automated deployment and configuration management for **IVA Mail** corporate email cluster.

## Quick Start

```bash
# 1. Copy and fill secrets
cp .env.example .env
nano .env

# 2. Start API
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000

# 3. Start UI (dev)
cd frontend
npm install && npm run dev
```

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Project Structure

```
backend/          — FastAPI + Python orchestrator (primary install)
iva-mail-ansible/ — Ansible roles for config management (day-2)
frontend/         — React SPA (to be developed)
docs/             — Architecture, guides
```

## Cluster

8 nodes: 2 backends + 2 frontends + PostgreSQL/NFS + HAProxy + Monitoring + Controller

Full topology: see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
