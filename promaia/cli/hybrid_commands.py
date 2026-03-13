"""
CLI commands for managing hybrid storage architecture.

This module provides commands for:
- Getting status and statistics for hybrid architecture
- Migrating from legacy to hybrid architecture (one-time migration)
- Analyzing content structure
"""
import argparse
import json
import logging
import os
from datetime import datetime
from typing import Dict, Any

from promaia.storage.unified_query import get_query_interface
from promaia.storage.hybrid_storage import get_hybrid_registry

# Simple display functions for hybrid commands
def print_success(msg): print(f"✅ {msg}")
def print_error(msg): print(f"❌ {msg}")
def print_info(msg): print(f"ℹ️  {msg}")
def print_warning(msg): print(f"⚠️  {msg}")

logger = logging.getLogger(__name__)

def handle_hybrid_status(args):
    """Show hybrid architecture status and statistics."""
    try:
        query_interface = get_query_interface()
        stats = query_interface.get_statistics()
        
        print_info("📊 Hybrid Storage Architecture Status")
        print()
        
        print_success("✅ Using Hybrid Architecture")
        print("   Separate optimized tables for each content type")
        print("   Fast direct column access for queries")
        print()
        
        print("📈 Content Statistics:")
        for content_type, count in stats.items():
            if content_type != 'architecture':
                print(f"   {content_type}: {count} entries")
        
        print()
        print_info("Available Commands:")
        print("   maia hybrid migrate    - Migrate from legacy (one-time)")
        print("   maia hybrid analyze    - Analyze content structure")
        
    except Exception as e:
        print_error(f"Error getting status: {e}")

def handle_hybrid_migrate(args):
    """Migrate from legacy to hybrid architecture."""
    from promaia.utils.env_writer import get_db_path
    hybrid_path = str(get_db_path())
    
    print_info("🔄 Hybrid Database Status Check")
    print()
    
    # Check if hybrid already exists with data
    if os.path.exists(hybrid_path):
        try:
            query_interface = get_query_interface()
            stats = query_interface.get_statistics()
            total = sum(count for key, count in stats.items() if key != 'architecture')
            
            if total > 0:
                print_success(f"✅ Hybrid architecture is active with {total} entries")
                print("System is already using the hybrid database architecture.")
                return
            else:
                print_success("✅ Hybrid architecture is initialized but empty")
                print("System is ready to use the hybrid database architecture.")
                return
        except Exception as e:
            print_warning(f"⚠️  Hybrid database exists but couldn't read statistics: {e}")
            return
    else:
        print_info("Hybrid database not found. It will be created automatically when needed.")
        print("The system will initialize the hybrid architecture on first use.")
        return
def handle_hybrid_analyze(args):
    """Analyze hybrid content structure and provide insights."""
    try:
        print_info("🔍 Hybrid Architecture Analysis")
        print()
        
        query_interface = get_query_interface()
        stats = query_interface.get_statistics()
        
        total = sum(count for key, count in stats.items() if key != 'architecture')
        
        if total == 0:
            print_warning("No content found in hybrid database")
            return
        
        print("📊 Content Type Distribution:")
        for content_type, count in stats.items():
            if content_type != 'architecture':
                percentage = (count / total) * 100
                print(f"   {content_type}: {count} entries ({percentage:.1f}%)")
        
        print()
        print("🏗️ Architecture Benefits:")
        print("   ✅ Direct column access for fast queries")
        print("   ✅ Optimized schemas per content type")
        print("   ✅ Better natural language processing")
        print("   ✅ Scalable for new content types")
        
        print()
        print("🔧 Optimization Tips:")
        print("   • Use direct column filters: status = 'Done'")
        print("   • Avoid JSON extraction when possible")
        print("   • Leverage workspace filtering for performance")
        print("   • Use content_filters for text search")
        
    except Exception as e:
        print_error(f"Analysis error: {e}")

def add_hybrid_commands(subparsers):
    """Add hybrid architecture commands to the CLI."""
    hybrid_parser = subparsers.add_parser('hybrid', help='Manage hybrid storage architecture')
    hybrid_subparsers = hybrid_parser.add_subparsers(dest='hybrid_command', help='Hybrid commands')
    
    # Status command
    status_parser = hybrid_subparsers.add_parser('status', help='Show hybrid architecture status')
    status_parser.set_defaults(func=handle_hybrid_status)
    
    # Migrate command
    migrate_parser = hybrid_subparsers.add_parser('migrate', help='One-time migration from legacy to hybrid')
    migrate_parser.add_argument('--force', action='store_true', help='Skip confirmation prompt')
    migrate_parser.set_defaults(func=handle_hybrid_migrate)
    
    # Analyze command
    analyze_parser = hybrid_subparsers.add_parser('analyze', help='Analyze hybrid content structure')
    analyze_parser.set_defaults(func=handle_hybrid_analyze)

def add_hybrid_commands_to_existing_parser(parser, subparsers):
    """Add hybrid commands to an existing parser (for alias support)."""
    add_hybrid_commands(subparsers) 