# AWX Credentials Setup Guide

This guide provides step-by-step instructions for creating and testing credentials in AWX for IVA Mail cluster management.

## Overview

AWX credentials are secure containers for sensitive information used by job templates:
- SSH keys for remote host authentication
- Database connection details
- API tokens and custom protocol credentials

All credentials are encrypted in AWX database and never exposed in logs or API responses.

## Prerequisites

- AWX is deployed and running (http://10.3.6.100:8080)
- You are logged in as admin user
- Gather required information:
  - SSH private key for Ansible authentication
  - PostgreSQL admin password (from .env.awx)
  - IVA Mail admin credentials

## Credential #1: IVA Mail SSH Key

Used by all job templates for authentication to managed nodes via SSH.

### Steps

1. **Navigate to Credentials**
   - Click "Administration" (top-right menu)
   - Select "Credentials"
   - Click "Create" button

2. **Configure Basic Properties**
   - Name: `IVA Mail SSH Key`
   - Description: `SSH key for Ansible authentication to all cluster nodes`
   - Organization: `IVA Mail`
   - Credential Type: `Machine` (dropdown)

3. **Provide SSH Private Key**
   - Username: `root`
   - SSH Private Key: [Paste contents of Ansible SSH private key]
     - Command to get key: `cat ~/.ssh/id_rsa`
     - Or: `cat /opt/iva-mail-ansible/.ssh/id_rsa`
     - Copy entire key including BEGIN and END lines
   - Private Key Passphrase: (leave empty if key has no passphrase)

4. **Test Connection**
   - Do NOT save yet
   - Instead, click "Test" button (appears when Credential Type is selected)
   - Select a backend node for testing: `10.3.6.126`
   - AWX will attempt SSH connection to verify key works
   - Expected result: "Connection successful"

5. **Save Credential**
   - After successful test, click "Save"

### Verification

Test SSH connectivity from AWX:

```bash
# From controller node
ssh -i /path/to/private/key root@10.3.6.126 "hostname"

# Should return: be1 or backend1
```

## Credential #2: PostgreSQL Admin

Used by database initialization and management job templates.

### Steps

1. **Navigate to Credentials**
   - Click "Administration" → "Credentials"
   - Click "Create" button

2. **Configure Basic Properties**
   - Name: `PostgreSQL Admin`
   - Description: `PostgreSQL administrative access for IVA Mail database on 10.3.6.128`
   - Organization: `IVA Mail`
   - Credential Type: `PostgreSQL` (dropdown)

3. **Provide Database Connection Details**
   - Host: `10.3.6.128`
   - Port: `5432`
   - Username: `postgres`
   - Password: [Value of AWX_DB_PASSWORD from .env.awx — THIS IS THE AWX DATABASE PASSWORD, not the IVA Mail PostgreSQL password]
   
   Actually, for IVA Mail PostgreSQL, use:
   - Host: `10.3.6.128`
   - Port: `5432`
   - Username: `postgres`
   - Password: [PostgreSQL root password from your deployment]
   - Database: `ivamail` (optional — only needed if testing specific DB)

4. **Test Connection**
   - Click "Test" button
   - Expected result: "Connection successful"

5. **Save Credential**
   - Click "Save"

### Verification

Test database connectivity from controller:

```bash
# Install PostgreSQL client (if not present)
sudo apt-get install -y postgresql-client

# Test connection
psql -h 10.3.6.128 -U postgres -d ivamail -c "SELECT version();"

# Should return PostgreSQL version information
```

## Credential #3: IVA Mail CMD

Used by license management and direct backend control operations via CMD protocol (port 106).

### Steps

1. **Navigate to Credentials**
   - Click "Administration" → "Credentials"
   - Click "Create" button

2. **Configure Basic Properties**
   - Name: `IVA Mail CMD`
   - Description: `CMD protocol access for IVA Mail backend control (port 106)`
   - Organization: `IVA Mail`
   - Credential Type: `IVA Mail CMD` (custom type — should already exist if apply-awx-config.yml was run)

3. **Provide CMD Protocol Credentials**
   - Username: `admin` (or value of MAIL_ADMIN_USER from deployment)
   - Password: [Value of MAIL_ADMIN_PASSWORD from deployment]

4. **Test Connection**
   - Click "Test" button
   - Select a backend node: `10.3.6.126`
   - AWX will attempt CMD protocol connection (port 106)
   - Expected result: "Connection successful"

5. **Save Credential**
   - Click "Save"

### Verification

Test CMD protocol from controller (if cmd_client.py available):

```bash
# Test command-line access to backend
python3 /opt/ivamail/cmd_client.py \
  --host 10.3.6.126 \
  --port 106 \
  --username admin \
  --password <password> \
  --command "GetVersion"

# Should return backend version information
```

## Credential #4: IVA Mail Custom Type (Reference)

This credential type is pre-defined by apply-awx-config.yml and used for custom IVA Mail operations.

**Do not create manually** — already configured via AWX as-code.

For reference only:
- Name: `IVA Mail CMD`
- Type: `Custom`
- Fields: username, password
- Environment injection: Sets environment variables for playbooks

## Credential #5: IVA Mail License (Reference)

This credential type would be used for license file management.

**Do not create manually** — already configured via AWX as-code.

For reference only:
- Name: `IVA Mail License`
- Type: `Custom`
- Fields: license_file_content, license_file_path

## Credential #6: IVA Mail Config (Reference)

This credential type would be used for configuration management operations.

**Do not create manually** — already configured via AWX as-code.

For reference only:
- Name: `IVA Mail Config`
- Type: `Custom`
- Fields: config_content, target_path

## Credentials Management

### View All Credentials

1. Click "Administration" → "Credentials"
2. Table shows all credentials with:
   - Name
   - Type
   - Organization
   - Created/Modified dates

### Edit Credential

1. Find credential in list
2. Click credential name
3. Click "Edit" button
4. Modify fields as needed
5. Click "Save"

### Delete Credential

1. Find credential in list
2. Click credential name
3. Click "Delete" button
4. Confirm deletion

**Warning**: Deleting a credential will break any job templates that use it.

### View Credential Usage

1. Find credential in list
2. Click credential name
3. Scroll to "Credentials using this" section
4. Shows which job templates and projects use this credential

## Credential Scoping

### Organization-Level Credentials

Credentials created with Organization = "IVA Mail" are visible only to users in that organization.

### Global Credentials

Credentials created without organization are visible to all users (not recommended for production).

### User Credentials

Credentials can be marked as personal (only visible to creator).

## Security Best Practices

1. **Use Strong Passwords**
   - Minimum 12 characters
   - Mix of uppercase, lowercase, numbers, special characters
   - Never use dictionary words or predictable patterns

2. **Rotate Credentials Regularly**
   - Update SSH keys quarterly
   - Change passwords semi-annually
   - Update license credentials as needed

3. **Limit Credential Visibility**
   - Use organization scoping to restrict access
   - Only grant credentials to necessary users
   - Audit credential access regularly

4. **Never Expose Credentials**
   - Do not hardcode in playbooks
   - Do not include in log output
   - Do not share via email or unencrypted channels
   - Always use AWX credential injection

5. **Audit Credential Usage**
   - Review which job templates use each credential
   - Monitor credential changes
   - Delete unused credentials

6. **Secure SSH Keys**
   - Use at least 4096-bit RSA keys
   - Prefer ED25519 keys for newer systems
   - Never use key with empty passphrase in production
   - Store keys in secure location with proper permissions (chmod 600)

## Troubleshooting

### "Connection Failed" on SSH Key Test

**Problem**: SSH test fails with "Connection refused" or "Permission denied"

**Solutions**:
```bash
# 1. Verify key is correct
ssh-keygen -y -f /path/to/private/key  # Should print public key

# 2. Verify SSH server is running on target
ssh -vvv root@10.3.6.126 -i /path/to/private/key

# 3. Check SSH key permissions
chmod 600 /path/to/private/key
chmod 700 ~/.ssh

# 4. Verify key is in authorized_keys on target
ssh root@10.3.6.126 "cat ~/.ssh/authorized_keys | grep $(ssh-keygen -y -f /path/to/private/key)"

# 5. Test from controller directly
python3 -c "import paramiko; k = paramiko.RSAKey.from_private_key_file('/path/to/key'); print('Key loaded successfully')"
```

### "Connection Failed" on PostgreSQL Test

**Problem**: Database test fails with "cannot connect" or "authentication failed"

**Solutions**:
```bash
# 1. Verify PostgreSQL is running
psql -h 10.3.6.128 -U postgres -c "SELECT 1;" 2>&1

# 2. Verify pg_hba.conf allows connections
ssh root@10.3.6.128 "cat /etc/postgresql/*/main/pg_hba.conf | grep 10.3.6"

# Should show entry: host ivamail postgres 10.3.6.0/24 md5 (or similar)

# 3. Verify password is correct
psql -h 10.3.6.128 -U postgres -c "SELECT 1;" 
# If prompted for password, you know SSH works but password might be wrong

# 4. Check PostgreSQL logs
ssh root@10.3.6.128 "tail -50 /var/log/postgresql/*.log"
```

### "Connection Failed" on CMD Protocol Test

**Problem**: CMD test fails with "Connection refused" or "Cannot connect to port 106"

**Solutions**:
```bash
# 1. Verify backend is running
ssh root@10.3.6.126 "systemctl status ivamail"

# 2. Verify port 106 is listening
ssh root@10.3.6.126 "netstat -tulpn | grep 106"

# Should show something like: tcp  0 0 0.0.0.0:106  0.0.0.0:*  LISTEN  1234/ivamail

# 3. Test connectivity directly
telnet 10.3.6.126 106

# 4. Check backend logs
ssh root@10.3.6.126 "tail -50 /var/log/ivamail/*.log"
```

### Credential Not Available in Job Template

**Problem**: When creating job template, credential doesn't appear in dropdown

**Solutions**:
1. Verify credential Organization matches job template Organization
2. Verify credential Type matches required type in job template
3. Refresh browser page (Ctrl+F5)
4. Check if credential was successfully saved (click into it to verify)

## Using Credentials in Playbooks

When you assign a credential to a job template, AWX injects the credential data as environment variables:

```yaml
# Example playbook using SSH key (injected by Ansible Machine credential)
- name: Run command on backend
  hosts: backends
  tasks:
    - name: Execute command
      ansible.builtin.command: /opt/ivamail/cmd_client.py --command "GetVersion"
      register: version
      changed_when: false

# AWX automatically provides:
# - SSH key: injected as ansible_ssh_private_key_file
# - Ansible user: injected as ansible_user
# - SSH port: defaults to 22 (can be overridden)
```

## References

- [AWX Credentials Documentation](https://docs.ansible.com/ansible-tower/latest/html/userguide/credentials.html)
- [Ansible Machine Credential Type](https://docs.ansible.com/ansible-tower/latest/html/userguide/credentials.html#machine)
- [PostgreSQL Credential Type](https://docs.ansible.com/ansible-tower/latest/html/userguide/credentials.html#postgresql)
- [Custom Credential Types](https://docs.ansible.com/ansible-tower/latest/html/userguide/credential_types.html)

