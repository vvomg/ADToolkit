# ADToolKit — Architecture

## Overview

Automated deployment and configuration management for **IVA Mail** corporate email cluster.

## Stack

| Layer | Technology | Purpose |
|---|---|---|
| Web UI | React 18 + TypeScript + Vite | Deployment wizard, job monitor, config UI |
| API | FastAPI (Python) | REST + WebSocket, orchestration entrypoint |
| Primary install | Python (backend/) | SSH-based cluster installation, 13 phases |
| Config management | Ansible + ansible-runner | Day-2 config apply/rollback |
| Monitoring install | Python (backend/) | Prometheus, Grafana, Loki, Promtail, node_exporter |
| Monitoring config | Ansible | prometheus.yml, grafana datasources/dashboards, loki config |

## Architecture Diagram

```
[ React SPA ]  ──HTTP/WebSocket──►  [ FastAPI (backend/) ]  ──SSH──►  [ Cluster ]
  port 80                              port 8000
  controller                           orchestrator.py
  static build                         state machine
                                             │
                                   ┌─────────┴─────────┐
                                   │                   │
                              Python SSH          ansible-runner
                              (install)           (config mgmt)
```

## Cluster Topology

| Role | IP | SSH user |
|---|---|---|
| Controller (UI + API) | 10.3.6.100 | user |
| HAProxy | 10.3.6.101 | root |
| Frontend 1 | 10.3.6.102 | root |
| Frontend 2 | 10.3.6.103 | root |
| Backend 1 (license) | 10.3.6.206 | root |
| Backend 2 | 10.3.6.207 | root |
| PostgreSQL + NFS v3 | 10.3.6.208 | root |
| Monitoring | 10.3.6.108 | root |

## Deployment Phases (Python orchestrator)

```
CONFIGURATION → PREFLIGHT → INFRA_SETUP →
NODE_STARTUP → CLUSTER_CONFIG →
LICENSE_REQUEST → WAITING_LICENSE →
LICENSE_INSTALL → REMAINING_NODES →
HEALTH_CHECKS → MONITORING_SETUP →
REPORTING → SUCCESS
```

### MONITORING_SETUP phase (new)
1. Install `node_exporter` on all 7 nodes (parallel)
2. Install `Promtail` on all 7 nodes (parallel)
3. Install `Prometheus` on monitoring host (10.3.6.108)
4. Install `Grafana` on monitoring host
5. Install `Grafana Loki` on monitoring host
6. Apply base config (datasources, scrape targets)

## Ansible Roles (config management only)

| Role | Manages |
|---|---|
| `ivamail_config` | /etc/ivamail/*, parameters.conf — dump/apply/rollback |
| `prometheus_config` | prometheus.yml, alert rules |
| `grafana_config` | datasources.yml, dashboard JSON provisioning |
| `loki_config` | loki-config.yaml, retention |
| `promtail_config` | promtail-config.yaml per host group |

## Config Management Playbooks

| Playbook | Purpose |
|---|---|
| `07-config-dump.yml` | Save current configs to git |
| `08-config-apply.yml` | Apply configs from git |
| `09-config-rollback.yml` | Rollback to previous version |
| `10-monitoring-config.yml` | Apply Prometheus/Grafana/Loki configs |

## Monitoring Stack

| Component | Host | Role |
|---|---|---|
| Prometheus | 10.3.6.108 | Metrics collection, time-series storage |
| Grafana | 10.3.6.108 | Dashboards (Prometheus + Loki datasources) |
| Grafana Loki | 10.3.6.108 | Log aggregation |
| node_exporter | all 7 nodes | OS metrics → Prometheus |
| Promtail | all 7 nodes | /var/log/ivamail/*.log → Loki |

## React UI Screens

1. **Dashboard** — cluster topology, node status (online/offline/unknown)
2. **Deploy Wizard** — multi-step: topology → package → secrets → confirm → run
3. **Job Monitor** — real-time phase output via WebSocket, progress bar
4. **License Approval** — pause at WAITING_LICENSE: upload license.txt, Approve button
5. **Config Management** — trigger Ansible playbooks 07-10 from UI
6. **History** — deployment list, statuses, HTML report links

## Design System

- **Theme**: dark, terminal-inspired (Catppuccin Macchiato / Tokyo Night)
- **Typography**: Plus Jakarta Sans (UI) + JetBrains Mono (terminal/logs)
- **Colors**: deep blue-gray dominant, cyan/teal accent, semantic status colors
- **Components**: shadcn/ui (Card, Badge, Progress, Dialog, Tabs)
- **Animations**: Framer Motion — staggered reveals, terminal effects
