# AWX Deployment for IVA Mail Controller Node

This directory contains all necessary files for deploying AWX (Ansible Tower) on the controller node (10.3.6.100).

## Quick Start

### 1. Review Documentation

Start by reading the deployment guide:

```bash
cat docs/DEPLOYMENT-AWX.md
```

This covers:
- System architecture
- Prerequisites
- Step-by-step deployment
- Troubleshooting
- Security considerations

### 2. Deploy AWX

Run the main playbook with required passwords:

```bash
cd /opt/iva-mail-ansible

ansible-playbook playbooks/10-awx-setup.yml \
  -e "awx_admin_password=YourStrongPassword123!@# \
      awx_db_password=YourDBPassword456$%^"
```

**Important**: Replace passwords with strong, unique values.

### 3. Access AWX Web UI

After successful deployment:

1. Open browser: `http://10.3.6.100:8080`
2. Login: `admin` / `<AWX_ADMIN_PASSWORD from step 2>`
3. Change password immediately (recommended)

### 4. Configure Credentials

Follow the step-by-step guide to create credentials:

```bash
cat docs/AWX-CREDENTIALS-SETUP.md
```

Creates three credentials:
1. **IVA Mail SSH Key** - For backend node authentication
2. **PostgreSQL Admin** - For database management
3. **IVA Mail CMD** - For port 106 protocol access

## File Structure

### Documentation

- **docs/DEPLOYMENT-AWX.md** (484 lines)
  - Comprehensive deployment and management guide
  - Architecture overview
  - Installation steps
  - Troubleshooting guide
  - Data backup/recovery

- **docs/AWX-CREDENTIALS-SETUP.md** (385 lines)
  - Step-by-step credential creation in Web UI
  - Testing procedures for each credential
  - Security best practices
  - Troubleshooting credential issues

### Ansible Infrastructure

- **roles/docker_setup/**
  - Reusable Ansible role for Docker/Docker Compose installation
  - `tasks/main.yml` - Installation tasks (idempotent)
  - `defaults/main.yml` - Configuration variables
  - `meta/main.yml` - Galaxy metadata

- **playbooks/10-awx-setup.yml** (448 lines)
  - Main deployment orchestration
  - Play 1: Docker setup on controller
  - Play 2: AWX deployment and configuration
  - 17 tasks with health checks and validation

- **inventory/controllers.yml** (53 lines)
  - Controller node inventory
  - Connection parameters
  - Group variables for all controllers

## Prerequisites

Before running the playbook, ensure:

### System Requirements

- **Controller node**: 10.3.6.100
- **OS**: Ubuntu 20.04 LTS or Ubuntu 22.04 LTS
- **CPU**: 4+ cores
- **RAM**: 8 GB minimum (16 GB recommended)
- **Disk**: 30 GB minimum (50 GB recommended)

### Network Requirements

- SSH access to controller as root
- Ports 8080, 8443 available
- Outbound connectivity to Docker Hub
- Connectivity to all cluster nodes (for credentials testing)

### Credentials

- Strong password for AWX admin user (12+ characters)
- Strong database password (12+ characters)
- SSH key for Ansible authentication to cluster nodes

## Deployment Options

### Option 1: Interactive Password Prompt

```bash
# Start deployment, prompted for passwords
ansible-playbook playbooks/10-awx-setup.yml
```

### Option 2: Command-line Arguments

```bash
# Provide passwords via -e flag
ansible-playbook playbooks/10-awx-setup.yml \
  -e "awx_admin_password=Pass@123 awx_db_password=DBPass@123"
```

### Option 3: Variable File (Recommended for Production)

Create `group_vars/controllers/vars.yml`:

```yaml
awx_version: "23.9.0"
awx_admin_user: "admin"
```

Create encrypted `group_vars/controllers/vault.yml`:

```bash
ansible-vault create group_vars/controllers/vault.yml
```

Contents:
```yaml
awx_admin_password: "YourStrongPassword123!@#"
awx_db_password: "YourDBPassword456$%^"
```

Then run:
```bash
ansible-playbook playbooks/10-awx-setup.yml \
  --vault-password-file ~/.vault-password
```

### Option 4: Docker Setup Only

If Docker already installed on controller:

```bash
ansible-playbook playbooks/10-awx-setup.yml --tags docker
```

### Option 5: AWX Setup Only

If Docker already installed:

```bash
ansible-playbook playbooks/10-awx-setup.yml --skip-tags docker
```

## Post-Deployment Verification

### 1. Check Container Status

```bash
cd /opt/iva-mail-ansible
docker compose -f docker-compose.awx.yml ps

# Expected output:
# NAME           STATUS
# awx_redis      Up
# awx_postgres   Up (healthy)
# awx_web        Up (healthy)
# awx_task       Up
```

### 2. Test API Endpoint

```bash
curl -s http://10.3.6.100:8080/api/v2/ping/ | jq '.'

# Expected response:
# {
#   "ha_enabled": false,
#   "version": "23.9.0",
#   "active_node": "awx_web"
# }
```

### 3. Login to Web UI

1. Open `http://10.3.6.100:8080`
2. Username: `admin`
3. Password: `<AWX_ADMIN_PASSWORD>`
4. Click "Organizations" to verify it's working

### 4. Create Test Credential

Follow `docs/AWX-CREDENTIALS-SETUP.md` to create and test one credential.

## Troubleshooting

### Deployment Fails

1. Check system prerequisites:
   ```bash
   docker --version
   docker compose version
   ```

2. Verify passwords were passed correctly:
   ```bash
   cat .env.awx | grep PASSWORD
   ```

3. Check container logs:
   ```bash
   docker compose -f docker-compose.awx.yml logs awx_web
   ```

### AWX Web UI Not Responding

1. Verify containers are running:
   ```bash
   docker compose -f docker-compose.awx.yml ps
   ```

2. Check port availability:
   ```bash
   netstat -tulpn | grep 8080
   ```

3. Restart AWX:
   ```bash
   docker compose -f docker-compose.awx.yml restart awx_web
   ```

### Credential Test Fails

See "Troubleshooting" section in `docs/AWX-CREDENTIALS-SETUP.md` for detailed solutions.

## Managing AWX

### View Logs

```bash
docker compose -f docker-compose.awx.yml logs -f awx_web
```

### Stop AWX

```bash
docker compose -f docker-compose.awx.yml down
```

### Start AWX

```bash
docker compose -f docker-compose.awx.yml up -d
```

### Restart Services

```bash
docker compose -f docker-compose.awx.yml restart
```

### Update AWX Version

1. Edit `.env.awx`:
   ```bash
   AWX_VERSION=24.1.0  # Change to desired version
   ```

2. Restart:
   ```bash
   docker compose -f docker-compose.awx.yml pull
   docker compose -f docker-compose.awx.yml up -d
   ```

## Security Considerations

- Change default admin password immediately after deployment
- Use strong passwords (12+ characters, mixed case, numbers, symbols)
- Store .env.awx securely (0600 permissions, not in version control)
- Use ansible-vault for production credential storage
- Regularly rotate SSH keys used in AWX credentials
- Restrict network access to Web UI (firewall rules)
- Keep Docker and AWX images updated

## Data Backup

### Backup PostgreSQL Database

```bash
docker compose -f docker-compose.awx.yml exec awx_postgres \
  pg_dump -U awx awx > awx_backup_$(date +%Y%m%d_%H%M%S).sql
```

### Restore from Backup

```bash
# Stop services
docker compose -f docker-compose.awx.yml down

# Reset database
docker volume rm awx_postgres_data

# Start PostgreSQL
docker compose -f docker-compose.awx.yml up -d awx_postgres
sleep 60

# Restore backup
docker compose -f docker-compose.awx.yml exec -T awx_postgres \
  psql -U awx awx < awx_backup_YYYYMMDD_HHMMSS.sql

# Start all services
docker compose -f docker-compose.awx.yml up -d
```

## Integration with IVA Mail Deployment

Once AWX is deployed and configured, use it to manage the complete IVA Mail cluster:

1. Create credentials for all nodes (SSH keys, database, CMD protocol)
2. Add dynamic inventory for cluster nodes
3. Create job templates for existing playbooks:
   - 00-bootstrap.yml - Bootstrap all nodes
   - 01-postgres-nfs.yml - Setup database and storage
   - 02-backends-install.yml - Deploy backend services
   - 03-frontends.yml - Deploy frontend services
   - 04-haproxy.yml - Configure load balancer
   - 05-monitoring.yml - Setup monitoring
4. Create workflows combining multiple playbooks
5. Set up approval nodes for critical operations
6. Monitor execution and collect logs

## Support and References

- **AWX Documentation**: https://ansible-awx-oper-guide.readthedocs.io/
- **Docker Compose**: https://docs.docker.com/compose/
- **Ansible**: https://docs.ansible.com/
- **PostgreSQL**: https://www.postgresql.org/docs/
- **IVA Mail**: [Internal documentation](../DEPLOYMENT-IVAMAIL.md)

## File Modification Notes

These new files were created WITHOUT modifying any existing files:

- `docker-compose.awx.yml` - NOT modified
- `.env.awx.example` - NOT modified
- `awx/as-code/apply-awx-config.yml` - NOT modified
- Existing playbooks (00-bootstrap.yml, etc.) - NOT modified
- Existing roles - NOT modified

All new files integrate seamlessly with existing infrastructure.

---

**Last Updated**: 2026-05-22  
**AWX Version**: 23.9.0  
**Target Platform**: Ubuntu 20.04 LTS / 22.04 LTS
