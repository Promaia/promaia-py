"""
Gmail integration commands for the Maia CLI.
"""
import os
import json
import asyncio
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

async def handle_gmail_setup(args):
    """Handle 'maia workspace gmail-setup' command (deprecated)."""
    print("This command has been replaced by the unified auth system.")
    print()
    print("Run instead:  maia auth configure google")
    print()
    print("This supports both Promaia-hosted OAuth (no GCP project needed)")
    print("and user-owned OAuth (bring your own Google Cloud credentials).")

async def handle_gmail_test(args):
    """Handle 'maia gmail test' command."""
    workspace = getattr(args, 'workspace', None)
    email = getattr(args, 'email', None)
    
    if not workspace or not email:
        print("❌ Please specify --workspace and --email")
        return
    
    print(f"🧪 Testing Gmail connection for {email} in workspace '{workspace}'...")
    
    try:
        from promaia.connectors.gmail_connector import GmailConnector
        
        config = {
            "database_id": email,
            "workspace": workspace
        }
        
        connector = GmailConnector(config)
        
        if await connector.test_connection():
            print("✅ Gmail connection successful!")
            
            # Test a simple query
            print("📬 Testing email query...")
            threads = await connector.query_pages(limit=5)
            print(f"✅ Found {len(threads)} recent email threads")
            
            if threads:
                print("📨 Recent threads:")
                for i, thread in enumerate(threads[:3]):
                    subject = thread.get('subject', 'No Subject')[:50]
                    from_addr = thread.get('from', 'Unknown')
                    message_count = thread.get('message_count', 1)
                    print(f"  {i+1}. {subject}... (from: {from_addr}, messages: {message_count})")
        else:
            print("❌ Gmail connection failed")
            
    except ImportError:
        print("❌ Gmail dependencies not installed.")
        print("Install with: pip install google-auth google-auth-oauthlib google-api-python-client")
    except Exception as e:
        print(f"❌ Gmail test failed: {e}")

async def handle_gmail_labels(args):
    """Handle 'maia gmail labels' command."""
    workspace = getattr(args, 'workspace', None)
    email = getattr(args, 'email', None)
    
    if not workspace or not email:
        print("❌ Please specify --workspace and --email")
        return
    
    print(f"🏷️  Fetching Gmail labels for {email}...")
    
    try:
        from promaia.connectors.gmail_connector import GmailConnector
        
        config = {
            "database_id": email,
            "workspace": workspace
        }
        
        connector = GmailConnector(config)
        await connector.connect()
        
        # Get labels using Gmail API
        labels_result = connector.service.users().labels().list(userId='me').execute()
        labels = labels_result.get('labels', [])
        
        print(f"📋 Found {len(labels)} labels:")
        print()
        
        # Categorize labels
        system_labels = []
        user_labels = []
        
        for label in labels:
            label_name = label.get('name', '')
            label_type = label.get('type', 'user')
            
            if label_type == 'system':
                system_labels.append(label_name)
            else:
                user_labels.append(label_name)
        
        if system_labels:
            print("🔧 System Labels:")
            for label in sorted(system_labels):
                print(f"  - {label}")
            print()
        
        if user_labels:
            print("👤 User Labels:")
            for label in sorted(user_labels):
                print(f"  - {label}")
            print()
        
        if user_labels:
            print("💡 To sync only emails with a specific label, add this to your database config:")
            print('  "property_filters": {')
            print('    "label": "your-label-name"')
            print('  }')
        
    except ImportError:
        print("❌ Gmail dependencies not installed.")
        print("Install with: pip install google-auth google-auth-oauthlib google-api-python-client")
    except Exception as e:
        print(f"❌ Failed to fetch labels: {e}")

def add_gmail_commands(subparsers):
    """Add Gmail management commands to CLI."""
    gmail_parser = subparsers.add_parser('gmail', help='Gmail integration commands')
    gmail_subparsers = gmail_parser.add_subparsers(dest='gmail_command', required=True)
    
    # Gmail setup
    setup_parser = gmail_subparsers.add_parser('setup', help='Set up Gmail OAuth2 authentication')
    setup_parser.add_argument('workspace', help='Workspace name')
    setup_parser.add_argument('email', help='Gmail email address')
    setup_parser.set_defaults(func=handle_gmail_setup)
    
    # Gmail test
    test_parser = gmail_subparsers.add_parser('test', help='Test Gmail connection')
    test_parser.add_argument('--workspace', required=True, help='Workspace name')
    test_parser.add_argument('--email', required=True, help='Gmail email address')
    test_parser.set_defaults(func=handle_gmail_test)
    
    # Gmail labels
    labels_parser = gmail_subparsers.add_parser('labels', help='List Gmail labels')
    labels_parser.add_argument('--workspace', required=True, help='Workspace name')
    labels_parser.add_argument('--email', required=True, help='Gmail email address')
    labels_parser.set_defaults(func=handle_gmail_labels)

# Also add to workspace commands for backwards compatibility
def add_workspace_gmail_commands(workspace_subparsers):
    """Add Gmail setup to workspace commands."""
    gmail_setup_parser = workspace_subparsers.add_parser('gmail-setup', help='Set up Gmail OAuth2 for workspace')
    gmail_setup_parser.add_argument('workspace', help='Workspace name')
    gmail_setup_parser.add_argument('email', help='Gmail email address')
    gmail_setup_parser.set_defaults(func=handle_gmail_setup) 