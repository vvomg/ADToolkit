# AWX Deployment Guide

## Overview

This guide covers the deployment of AWX (Ansible Tower) version 23.9.0+ on the IVA Mail controller node (10.3.6.100).

AWX is a container-based automation platform that will manage the entire IVA Mail cluster deployment and configuration.

## Architecture

### Components

AWX deployment consists of 4 Docker containers orchestrated via docker-compose:

| Container | Image | Purpose | Port(s) |
|-----------|-------|---------|---------|
| awx_redis | redis:7-alpine | Task broker for AWX workers | Internal (6379) |
| awx_postgres | postgres:15-alpine | AWX internal database (NOT IVA Mail DB) | Internal (5432) |
| awx_web | quay.io/ansible/awx:23.9.0 | Web UI and REST API | 8080, 8443 |
| awx_task | quay.io/ansible/awx:23.9.0 | Celery worker / job executor | Internal |

### Network Architecture

- **awx_internal**: Private internal network for Redis, PostgreSQL, and Task worker communication
- **awx_external**: Bridge network for Web container to communicate with external systems
- **Data volumes**:
  - `awx_postgres_data`: PostgreSQL database persistence
  - `awx_redis_data`: Redis AOF (append-only file) persistence
  - `awx_projects`: Ansible project files (playbooks, roles)
  - `awx_media`: Web UI static files and uploads

## Prerequisites

### System Requirements

**Controller Node (10.3.6.100)**:
- Ubuntu 20.04 LTS or Ubuntu 22.04 LTS
- Minimum 4 CPU cores
- Minimum 8 GB RAM (16 GB recommended for production)
- Minimum 30 GB free disk space (50 GB recommended)
- Docker and Docker Compose installed
- Network connectivity to all cluster nodes

### Network Requirements

- **HTTP (8080)**: Required for Web UI and API access
- **HTTPS (8443)**: Optional for encrypted API access
- **Outbound 22/TCP**: SSH to all managed nodes
- All ports must be available (no conflicts with existing services)

### Credentials & Configuration

- Strong password for AWX admin user (minimum 12 characters)
- Strong database password for PostgreSQL (minimum 12 characters)
- SSH key for Ansible authentication to managed nodes

## Installation

### Step 1: Prepare Environment File

```bash
# Navigate to project root
cd /opt/iva-mail-ansible

# Copy template
cp .env.awx.example .env.awx

# Edit .env.awx with strong passwords (DO NOT commit to repository)
nano .env.awx
```

Example content for .env.awx:
```bash
AWX_VERSION=23.9.0
AWX_ADMIN_USER=admin
AWX_ADMIN_PASSWORD=YourStrongPassword123!@#
AWX_DB_NAME=awx
AWX_DB_USER=awx
AWX_DB_PASSWORD=YourDBPassword456$%^
```

**IMPORTANT**: 
- Change `AWX_ADMIN_PASSWORD` and `AWX_DB_PASSWORD` to strong, unique values
- Password requirements: minimum 12 characters, mix of uppercase, lowercase, numbers, special characters
- Never commit .env.awx to git (it is in .gitignore)

### Step 2: Verify Disk Space

```bash
# Check available space
df -h /

# Ensure at least 30 GB free (50 GB recommended)
# Docker volumes will expand as needed
```

### Step 3: Verify Ports

```bash
# Check if ports 8080 and 8443 are available
sudo netstat -tulpn | grep -E ':(8080|8443)'

# If ports are in use, either:
# - Stop the conflicting service, or
# - Modify docker-compose.awx.yml port mappings
```

### Step 4: Deploy AWX Containers

```bash
# Navigate to project root
cd /opt/iva-mail-ansible

# Pull latest images
docker compose --env-file .env.awx -f docker-compose.awx.yml pull

# Start containers (detached mode)
docker compose --env-file .env.awx -f docker-compose.awx.yml up -d

# Monitor startup logs
docker compose --env-file .env.awx -f docker-compose.awx.yml logs -f

# Stop with Ctrl+C
```

### Step 5: Wait for Services to Initialize

```bash
# Check container status
docker compose --env-file .env.awx -f docker-compose.awx.yml ps

# Expected output:
# NAME           STATUS
# awx_redis      Up (healthy)
# awx_postgres   Up (healthy)
# awx_web        Up (healthy)
# awx_task       Up (running)

# Wait for "healthy" status (typically 1-3 minutes)
# PostgreSQL usually takes 30-60 seconds
# AWX Web takes 1-2 minutes to initialize database
```

### Step 6: Verify Health Checks

```bash
# Test PostgreSQL health
docker compose --env-file .env.awx -f docker-compose.awx.yml exec awx_postgres \
  pg_isready -U awx

# Test AWX Web API
curl -s http://localhost:8080/api/v2/ping/ | jq '.'

# Expected response:
# {
#   "ha_enabled": false,
#   "version": "23.9.0",
#   "active_node": "awx_web"
# }

# Test AWX Task status (check logs)
docker compose --env-file .env.awx -f docker-compose.awx.yml logs awx_task | grep -i "ready"
```

## Accessing AWX

### Web User Interface

Open your browser and navigate to:
```
http://10.3.6.100:8080
```

**Default Credentials**:
- Username: `admin`
- Password: Value of `AWX_ADMIN_PASSWORD` from .env.awx

### REST API

AWX provides a RESTful API for programmatic access:
```bash
# API endpoint
http://10.3.6.100:8080/api/v2/

# Example: List organizations
curl -k -u admin:password http://10.3.6.100:8080/api/v2/organizations/

# API documentation available in Web UI:
# Settings → Documentation → API
```

## Initial Configuration

### Change Admin Password (Recommended)

```bash
# Via Web UI:
1. Click profile icon (top-right)
2. Select "User"
3. Click "Edit"
4. Change password
5. Save

# Via API:
curl -X PATCH \
  -u admin:old_password \
  -H "Content-Type: application/json" \
  -d '{"password": "new_password"}' \
  http://10.3.6.100:8080/api/v2/users/1/
```

### Create Additional Users (Optional)

```bash
# Via Web UI:
1. Administration → Users
2. Add
3. Enter username, email, password
4. Assign team (optional)
5. Save

# Users can then log in with their credentials
```

## Credential Management

See `docs/AWX-CREDENTIALS-SETUP.md` for detailed instructions on creating credentials in AWX:

1. IVA Mail SSH Key
2. PostgreSQL Admin
3. IVA Mail CMD Protocol
4-6. Custom credential types (reference)

## Troubleshooting

### Port Conflicts

**Problem**: Ports 8080 or 8443 already in use

**Solution**:
```bash
# Find what's using the port
sudo lsof -i :8080

# Option 1: Stop conflicting service
sudo systemctl stop <service_name>

# Option 2: Modify port mappings in docker-compose.awx.yml
# Change "8080:8052" to "8081:8052" for example
```

### Disk Space Issues

**Problem**: Docker containers fail to start, "no space left on device"

**Solution**:
```bash
# Check disk usage
df -h /

# Clean up Docker
docker system prune -a --volumes

# This removes unused containers, images, networks, and volumes
# Be careful: this will delete all unused images

# Restart containers
docker compose --env-file .env.awx -f docker-compose.awx.yml up -d
```

### PostgreSQL Connection Issues

**Problem**: awx_postgres or awx_web container repeatedly restarting

**Solution**:
```bash
# Check PostgreSQL logs
docker compose --env-file .env.awx -f docker-compose.awx.yml logs awx_postgres

# Verify PostgreSQL health
docker compose --env-file .env.awx -f docker-compose.awx.yml exec awx_postgres \
  pg_isready -U awx

# If unhealthy, reset PostgreSQL volume (WARNING: data loss)
docker compose --env-file .env.awx -f docker-compose.awx.yml down -v
docker compose --env-file .env.awx -f docker-compose.awx.yml up -d

# Wait for initialization to complete
```

### Redis Connection Issues

**Problem**: awx_web or awx_task logs show Redis connection errors

**Solution**:
```bash
# Check Redis health
docker compose --env-file .env.awx -f docker-compose.awx.yml exec awx_redis \
  redis-cli ping

# Expected response: PONG

# If PONG not returned, restart Redis
docker compose --env-file .env.awx -f docker-compose.awx.yml restart awx_redis
```

### Web UI Slow or Unresponsive

**Problem**: AWX Web UI loads slowly or times out

**Solution**:
```bash
# Check available resources
docker stats --no-stream

# If CPU or memory constrained:
# 1. Increase Docker resource limits (Docker Desktop settings)
# 2. Reduce other running containers
# 3. Add more RAM to system

# Restart AWX services
docker compose --env-file .env.awx -f docker-compose.awx.yml restart awx_web awx_task
```

### Cannot Access UI from Remote Host

**Problem**: Can access from localhost but not from 10.3.6.100

**Solution**:
```bash
# Verify containers are listening on all interfaces
docker compose --env-file .env.awx -f docker-compose.awx.yml ps

# Test connectivity
curl -v http://10.3.6.100:8080/api/v2/ping/

# Check firewall
sudo ufw status
sudo ufw allow 8080/tcp
sudo ufw allow 8443/tcp

# Check network connectivity
ping 10.3.6.100
telnet 10.3.6.100 8080
```

## Data Backup and Recovery

### Backup PostgreSQL

```bash
# Export database
docker compose --env-file .env.awx -f docker-compose.awx.yml exec awx_postgres \
  pg_dump -U awx awx > awx_backup_$(date +%Y%m%d_%H%M%S).sql

# Upload backup to secure location
scp awx_backup_*.sql backup_server:/backups/
```

### Restore PostgreSQL

```bash
# Stop AWX services
docker compose --env-file .env.awx -f docker-compose.awx.yml down

# Reset database volume
docker volume rm awx_postgres_data

# Start PostgreSQL only
docker compose --env-file .env.awx -f docker-compose.awx.yml up -d awx_postgres

# Wait for PostgreSQL to initialize (30-60 seconds)
sleep 60

# Restore database
docker compose --env-file .env.awx -f docker-compose.awx.yml exec -T awx_postgres \
  psql -U awx awx < awx_backup_YYYYMMDD_HHMMSS.sql

# Start remaining services
docker compose --env-file .env.awx -f docker-compose.awx.yml up -d
```

## Container Management

### View Logs

```bash
# View all logs (last 100 lines)
docker compose --env-file .env.awx -f docker-compose.awx.yml logs --tail=100

# View specific service logs
docker compose --env-file .env.awx -f docker-compose.awx.yml logs awx_web

# Follow logs in real-time
docker compose --env-file .env.awx -f docker-compose.awx.yml logs -f

# View logs since specific time
docker compose --env-file .env.awx -f docker-compose.awx.yml logs --since=10m

# Exit log follow: Ctrl+C
```

### Stop AWX

```bash
# Graceful shutdown (preserves data)
docker compose --env-file .env.awx -f docker-compose.awx.yml down

# All containers stop, volumes and data persist
```

### Start AWX

```bash
# Start all services
docker compose --env-file .env.awx -f docker-compose.awx.yml up -d

# Services will automatically restart on reboot (unless-stopped policy)
```

### Restart Services

```bash
# Restart all services
docker compose --env-file .env.awx -f docker-compose.awx.yml restart

# Restart specific service
docker compose --env-file .env.awx -f docker-compose.awx.yml restart awx_web

# Force restart (kill and recreate containers)
docker compose --env-file .env.awx -f docker-compose.awx.yml up -d --force-recreate
```

## System Updates

### Update AWX Version

```bash
# Edit .env.awx
nano .env.awx

# Change AWX_VERSION to desired version (e.g., 24.1.0)
# AWX_VERSION=24.1.0

# Pull new image
docker compose --env-file .env.awx -f docker-compose.awx.yml pull

# Restart services (will use new image)
docker compose --env-file .env.awx -f docker-compose.awx.yml up -d

# Monitor logs during upgrade
docker compose --env-file .env.awx -f docker-compose.awx.yml logs -f awx_web
```

## Next Steps

After successful AWX deployment:

1. **Create Credentials** - Follow `docs/AWX-CREDENTIALS-SETUP.md`
2. **Add Inventory** - Configure dynamic inventory sources for cluster nodes
3. **Create Job Templates** - Set up playbook execution for IVA Mail deployment
4. **Configure Workflows** - Build approval workflows for critical operations
5. **Run Playbooks** - Execute existing deployment playbooks (00-bootstrap, 01-postgres-nfs, etc.)
6. **Monitor Jobs** - Track job execution and collect logs

## Security Considerations

- Change default admin password immediately
- Use strong, unique passwords for all credentials
- Restrict network access to AWX UI (use firewall or reverse proxy)
- Regularly backup AWX database (PostgreSQL)
- Keep Docker and AWX images updated
- Use HTTPS (8443) for production deployments
- Store .env.awx securely (not in version control)
- Regularly rotate SSH keys used in AWX credentials
- Monitor AWX logs for unusual activity

## References

- [AWX Official Documentation](https://ansible-awx-oper-guide.readthedocs.io/)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [Ansible Documentation](https://docs.ansible.com/)
- [IVA Mail Deployment Guide](./DEPLOYMENT-IVAMAIL.md)

