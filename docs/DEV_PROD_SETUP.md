# Development & Production Environment Setup

## Overview

This guide covers setting up separate development and production environments for Promaia to ensure you always have a stable working version.

## Architecture

```
/your/dev/directory/
├── promaia/                  # Production environment
│   ├── data/                 # Production data
│   ├── .env                  # Production environment variables
│   └── maia.sh              # Production shortcut
└── promaia-dev/             # Development environment  
    ├── data-dev/            # Development data (isolated)
    ├── .env.development     # Development environment variables
    └── maiadev.sh          # Development shortcut
```

## Quick Setup

1. **Run the setup script** (from your current promaia directory):
   ```bash
   chmod +x setup-dev-environment.sh
   ./setup-dev-environment.sh
   ```

2. **Add the dev shortcut to your shell profile**:
   ```bash
   echo 'alias maiadev="/path/to/promaia-dev/maiadev.sh"' >> ~/.zshrc
   source ~/.zshrc
   ```

## Critical Configuration Changes

### 1. Database Path Separation

You MUST update your development config to use separate database paths:

**In `promaia-dev/promaia.config.dev.json`**, change:
```json
{
  "global": {
    "registry_db": "data-dev/hybrid_metadata.db",
    "markdown_base_directory": "data-dev"
  },
  "databases": {
    "journal": {
      "markdown_directory": "data-dev/md/notion/acme/journal"
    }
    // ... update ALL markdown_directory paths to use data-dev/
  }
}
```

### 2. Environment Variable Strategy

**Development (.env.development):**
```env
# Core settings
MAIA_ENVIRONMENT=development
MAIA_DATA_DIR=data-dev
MAIA_DEBUG=1

# Use the same API keys but separate data
NOTION_ACME_API_KEY=your_key_here

# Optional: Use different endpoints for testing
# OPENAI_API_KEY=your_dev_key_here
```

**Production (.env):**
```env
# Core settings  
MAIA_ENVIRONMENT=production
MAIA_DATA_DIR=data
MAIA_DEBUG=0

# Production API keys
NOTION_ACME_API_KEY=your_production_key_here
```

## Workflow Best Practices

### Development Workflow
1. **Always develop in dev environment**:
   ```bash
   maiadev  # Switches to dev environment
   ```

2. **Test thoroughly in dev before promoting**:
   - Run all critical functions
   - Test with sample data
   - Verify no breaking changes

3. **Promote stable changes**:
   ```bash
   git add .
   git commit -m "Feature: description"
   git push origin development
   
   # When ready for production:
   git checkout main
   git merge development
   git push origin main
   ```

### Production Updates
1. **Pull latest stable changes**:
   ```bash
   maia  # Switch to production
   git pull origin main
   ```

2. **Always backup before updates**:
   ```bash
   cp -r data/ data.backup.$(date +%Y%m%d_%H%M%S)
   ```

## Additional Best Practices

### 1. Backup Strategy
```bash
# Automated production backup (add to cron)
#!/bin/bash
cd /path/to/promaia
tar -czf "backups/promaia_$(date +%Y%m%d_%H%M%S).tar.gz" data/
find backups/ -name "*.tar.gz" -mtime +30 -delete  # Keep 30 days
```

### 2. Configuration Management

**Use environment-aware config loading** (future enhancement):
```python
# In your config loader
import os
env = os.getenv('MAIA_ENVIRONMENT', 'production')
config_file = f'promaia.config.{env}.json'
```

### 3. Testing Strategy

**Before promoting to production:**
- [ ] Chat functionality works
- [ ] Database sync functions properly  
- [ ] All configured integrations respond
- [ ] No critical error logs
- [ ] Performance is acceptable

### 4. Monitoring

**Production monitoring:**
- Check logs regularly: `tail -f data/logs/maia.log`
- Monitor disk space: `df -h`
- Verify scheduled syncs are running

## Common Pitfalls to Avoid

❌ **Don't:**
- Use the same database files for dev/prod
- Test in production environment
- Skip backups before updates
- Mix up environment shortcuts
- Commit sensitive API keys

✅ **Do:**
- Always test in dev first
- Keep environments completely separate
- Use version control for all changes
- Backup before any production changes
- Document configuration changes

## Troubleshooting

### Environment Confusion
```bash
# Check which environment you're in
echo $MAIA_ENVIRONMENT
pwd  # Should show promaia/ or promaia-dev/
```

### Database Issues
```bash
# If you accidentally mixed databases:
cd promaia-dev
rm -rf data/  # Remove any production data
mkdir data-dev  # Recreate dev data directory
```

### Sync Issues
```bash
# Reset development database
rm data-dev/hybrid_metadata.db
maiadev sync --rebuild
```

## Security Considerations

1. **API Key Management**: Use same keys for both environments initially, but consider separate keys for production
2. **Data Isolation**: Never let dev operations touch production data
3. **Access Control**: Ensure dev environment doesn't have production deployment access
4. **Backup Security**: Encrypt sensitive backups

## Next Steps

After setup:
1. Test the development environment thoroughly
2. Set up automated backups for production
3. Document your specific workflow preferences
4. Consider CI/CD pipeline for automatic testing

---

This setup ensures you'll never break your production environment while developing new features! 