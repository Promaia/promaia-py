# Promaia Deployment Guide

## Git Workflow

### Branches

```
main (production)     - Deployed to Fateen's Mac Mini - stable, tested code
  ↑
staging (pre-prod)    - Pre-production testing - features ready for review
  ↑
dev (development)     - Active development - your daily work
  ↑
feature/* branches    - Experimental features - isolated work
```

### Daily Workflow

**Your Development:**
```bash
# Working on new feature
git checkout -b feature/new-awesome-thing dev
# ... make changes ...
git commit -m "✨ Add awesome feature"
git push origin feature/new-awesome-thing

# Merge to dev when ready
git checkout dev
git merge feature/new-awesome-thing
git push origin dev
```

**Preparing for Fateen:**
```bash
# When dev is stable and tested
git checkout staging
git merge dev
git push origin staging

# Triggers staging deployment (auto-tests)
# Review on staging Mac Mini

# When staging looks good
git checkout main
git merge staging
git push origin main

# ✨ Automatically deploys to production Mac Mini!
```

### Quick Fixes

For urgent production fixes:
```bash
git checkout main
git checkout -b hotfix/critical-bug
# ... fix ...
git commit -m "🐛 Fix critical bug"

# Merge to main
git checkout main
git merge hotfix/critical-bug
git push origin main  # Auto-deploys

# Backport to other branches
git checkout staging
git merge hotfix/critical-bug
git checkout dev
git merge hotfix/critical-bug
```

## CI/CD Setup

### GitHub Secrets Required

Add these in GitHub repo settings → Secrets and variables → Actions:

```
MAC_MINI_HOST             - IP or hostname of Mac Mini (e.g., 192.168.1.100)
MAC_MINI_USER             - SSH username (e.g., fateen)
MAC_MINI_SSH_KEY          - Private SSH key for authentication
STAGING_MAC_MINI_HOST     - Staging server if different
```

### Generating SSH Key for Deployment

On your machine:
```bash
# Generate deployment key
ssh-keygen -t ed25519 -C "github-actions-promaia" -f ~/.ssh/promaia_deploy

# Copy public key to Mac Mini
ssh-copy-id -i ~/.ssh/promaia_deploy.pub fateen@mac-mini-ip

# Add private key to GitHub Secrets
cat ~/.ssh/promaia_deploy
# Copy entire output to MAC_MINI_SSH_KEY secret
```

### What Happens on Push

**Push to `main`:**
1. GitHub Actions triggers
2. SSHs into Mac Mini
3. Pulls latest code
4. Installs dependencies
5. Restarts services
6. ✅ Done in ~30 seconds

**Push to `staging`:**
1. Runs tests
2. Deploys to staging server
3. No production impact

**Push to `dev`:**
1. Runs basic tests
2. No deployment

## Mac Mini Setup

### Initial Setup

On the Mac Mini (one-time):

```bash
# Clone repo
cd ~
git clone git@github.com:your-org/promaia.git
cd promaia

# Set up production environment
python3 -m venv venv
source venv/bin/activate
pip install -e .

# Copy production config
cp promaia.config.template.json promaia.config.json
# Edit with Fateen's credentials

# Set up PostgreSQL (if using)
docker-compose up -d postgres
```

### Service Management

Create systemd services for always-on components:

**`/etc/systemd/system/promaia-discord-bot.service`:**
```ini
[Unit]
Description=Promaia Discord Bot
After=network.target

[Service]
Type=simple
User=fateen
WorkingDirectory=/home/fateen/promaia
Environment="PATH=/home/fateen/promaia/venv/bin"
ExecStart=/home/fateen/promaia/venv/bin/python -m promaia.discord_bot.bot
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/promaia-scheduler.service`:**
```ini
[Unit]
Description=Promaia Agent Scheduler
After=network.target

[Service]
Type=simple
User=fateen
WorkingDirectory=/home/fateen/promaia
Environment="PATH=/home/fateen/promaia/venv/bin"
ExecStart=/home/fateen/promaia/venv/bin/maia agent scheduler-start
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable promaia-discord-bot
sudo systemctl enable promaia-scheduler
sudo systemctl start promaia-discord-bot
sudo systemctl start promaia-scheduler
```

### Manual Deployment

If you need to deploy manually (CI/CD down):
```bash
# SSH into Mac Mini
ssh fateen@mac-mini-ip

# Update
cd ~/promaia
git pull origin main
source venv/bin/activate
pip install -e .

# Restart services
sudo systemctl restart promaia-discord-bot
sudo systemctl restart promaia-scheduler
```

## Monitoring

### Check Deployment Status

On GitHub:
- Go to Actions tab
- See status of latest deployment
- View logs if something fails

On Mac Mini:
```bash
# Check service status
sudo systemctl status promaia-discord-bot
sudo systemctl status promaia-scheduler

# View logs
sudo journalctl -u promaia-discord-bot -f
sudo journalctl -u promaia-scheduler -f

# Check git status
cd ~/promaia
git log -1 --oneline
```

## Rollback

If a deployment breaks things:

```bash
# On Mac Mini
cd ~/promaia
git log --oneline -10  # Find good commit
git checkout <good-commit-hash>
sudo systemctl restart promaia-*
```

Or revert on GitHub and push:
```bash
git revert <bad-commit>
git push origin main  # Auto-deploys good version
```

## Database Migrations

If schema changes are needed:

```bash
# Add migration to deploy script in .github/workflows/deploy-production.yml
# Or run manually on Mac Mini after deploy:
cd ~/promaia
source venv/bin/activate
python -m promaia.storage.migrations.add_new_column
```

## Troubleshooting

### Deployment Fails

1. Check GitHub Actions logs
2. Verify SSH key is correct
3. Test SSH manually: `ssh fateen@mac-mini-ip`
4. Check Mac Mini disk space: `df -h`

### Services Won't Start

1. Check logs: `sudo journalctl -u promaia-discord-bot -n 50`
2. Test manually: `cd ~/promaia && source venv/bin/activate && python -m promaia.discord_bot.bot`
3. Check config: `cat promaia.config.json` (credentials present?)
4. Check database: `psql -U promaia -d promaia_fateen -c "SELECT 1"`

### Need to Debug on Mac Mini

```bash
# Stop services
sudo systemctl stop promaia-*

# Run in foreground to see errors
cd ~/promaia
source venv/bin/activate
python -m promaia.discord_bot.bot  # See live output

# When done
sudo systemctl start promaia-*
```
