#!/usr/bin/env python3
"""
Test the agentic NL query system.
Demonstrates all the new features:
- Dynamic schema exploration
- Result validation
- Learning from successful queries
- Context logging
"""
import sys
import os

# Ensure promaia is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

from promaia.ai.nl_orchestrator import AgenticNLQueryProcessor
from promaia.utils.display import print_text


def test_schema_exploration():
    """Test dynamic schema exploration."""
    print("=" * 80)
    print("TEST 1: Dynamic Schema Exploration")
    print("=" * 80)
    
    from promaia.ai.nl_utilities import SchemaExplorer
    
    explorer = SchemaExplorer("data/hybrid_metadata.db")
    schema = explorer.explore_schema()
    
    print(f"\n✅ Discovered {len(schema['tables'])} tables:")
    for table, info in schema['tables'].items():
        print(f"   • {table}: {info['row_count']} rows, {len(info['columns'])} columns")
    
    print(f"\n✅ Found {len(schema['available_databases'])} databases:")
    for db in schema['available_databases'][:10]:
        stats = schema['database_stats'].get(db, {})
        print(f"   • {db}: {stats.get('count', 0)} entries")
    
    print("\n" + "=" * 80 + "\n")


def test_learning_system():
    """Test the learning system."""
    print("=" * 80)
    print("TEST 2: Learning System")
    print("=" * 80)
    
    from promaia.ai.nl_utilities import QueryLearningSystem
    
    learning = QueryLearningSystem()
    patterns = learning.load_successful_patterns()
    
    print(f"\n✅ Loaded {len(patterns)} successful patterns from history")
    
    if patterns:
        print("\nMost recent pattern:")
        latest = patterns[0]
        print(f"   Query: \"{latest.get('user_query', 'N/A')}\"")
        print(f"   Results: {latest.get('result_count', 0)} entries")
        print(f"   Timestamp: {latest.get('timestamp', 'N/A')}")
    else:
        print("\n   No patterns yet - this is the first run!")
    
    print("\n" + "=" * 80 + "\n")


def test_full_agentic_query():
    """Test a full agentic query with all features."""
    print("=" * 80)
    print("TEST 3: Full Agentic Query")
    print("=" * 80)
    
    processor = AgenticNLQueryProcessor()
    
    # Test query
    test_query = "trass gmail from last month with term avask"
    
    print(f"\n🔍 Testing query: \"{test_query}\"\n")
    
    result = processor.process_query(test_query, workspace="trass", max_retries=2)
    
    if result['success']:
        print_text("\n✅ Query completed successfully!", style="bold green")
        print(f"   • SQL Generated: {result['sql'][:100]}...")
        print(f"   • Results: {result['summary']['total_count']} entries")
        print(f"   • Databases: {', '.join(result['summary']['databases'])}")
        print(f"   • Learned: {result.get('learned', False)}")
    else:
        print_text(f"\n❌ Query failed: {result.get('error', 'Unknown error')}", style="bold red")
    
    print("\n" + "=" * 80 + "\n")


def main():
    print("\n🚀 AGENTIC NL QUERY SYSTEM TEST SUITE\n")
    
    # Run tests
    try:
        test_schema_exploration()
        test_learning_system()
        
        # Ask if user wants to run full test
        print("\n⚠️  The full test will run an actual query against your database.")
        response = input("Run full agentic query test? (y/N): ").strip().lower()
        
        if response in ['y', 'yes']:
            test_full_agentic_query()
        else:
            print("\nSkipped full query test.")
    
    except KeyboardInterrupt:
        print("\n\n❌ Tests interrupted by user.")
        return 1
    except Exception as e:
        print(f"\n\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    print("\n✅ All tests completed!\n")
    print("📁 Check these directories for outputs:")
    print("   • context_logs/nl_context_logs/ - Query context logs")
    print("   • data/nl_query_patterns/ - Learned query patterns")
    return 0


if __name__ == "__main__":
    sys.exit(main())

