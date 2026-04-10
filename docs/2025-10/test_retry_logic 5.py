#!/usr/bin/env python3
"""
Test retry logic by injecting errors into SQL queries.
Shows full prompts and responses at each step.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from promaia.utils.config import load_environment
load_environment()

from promaia.ai.nl_orchestrator import AgenticNLQueryProcessor
from promaia.utils.display import print_text
import sqlite3

class RetryTestProcessor(AgenticNLQueryProcessor):
    """
    Extended processor that:
    1. Logs all prompts and responses
    2. Intentionally breaks first SQL query to test retry logic
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.attempt_count = 0
        self.logged_prompts = []
        self.logged_responses = []
    
    def _generate_sql(self, intent, schema, retry_attempt=0):
        """Override to inject errors and log everything."""
        
        # Get the original SQL
        sql = super()._generate_sql(intent, schema, retry_attempt)
        
        self.attempt_count += 1
        
        print_text(f"\n{'='*70}", style="cyan")
        print_text(f"🔧 SQL GENERATION - ATTEMPT {self.attempt_count}", style="bold cyan")
        print_text(f"{'='*70}", style="cyan")
        
        print_text(f"\n📋 Intent:", style="yellow")
        print_text(f"   Goal: {intent['goal']}", style="dim")
        print_text(f"   Databases: {intent.get('databases', [])}", style="dim")
        print_text(f"   Search terms: {intent.get('search_terms', [])}", style="dim")
        print_text(f"   Retry attempt: {retry_attempt}", style="dim")
        
        if intent.get('_validation_feedback'):
            print_text(f"\n⚠️  Validation feedback from previous attempt:", style="yellow")
            print_text(f"   {intent['_validation_feedback']}", style="red")
        
        print_text(f"\n✅ Generated SQL:", style="green")
        print_text(sql, style="dim")
        
        # Inject errors on first attempt to test retry
        if self.attempt_count == 1:
            print_text(f"\n🐛 INJECTING ERROR (for testing):", style="bold red")
            
            # Error type 1: Break the WHERE clause
            if "WHERE" in sql:
                broken_sql = sql.replace("WHERE", "WHERE 1=0 AND")  # Makes it return no results
                print_text("   - Breaking WHERE clause to return 0 results", style="red")
                print_text(f"\n💥 Modified SQL:", style="red")
                print_text(broken_sql, style="dim")
                return broken_sql
        
        elif self.attempt_count == 2:
            print_text(f"\n🔄 SECOND ATTEMPT:", style="yellow")
            print_text("   - Still generating potentially problematic SQL", style="yellow")
            # Let it try again naturally
        
        else:
            print_text(f"\n✅ FINAL ATTEMPT:", style="green")
            print_text("   - Should generate working SQL now", style="green")
        
        return sql
    
    def _execute_sql(self, sql):
        """Override to log execution details."""
        print_text(f"\n{'='*70}", style="cyan")
        print_text(f"⚡ SQL EXECUTION", style="bold cyan")
        print_text(f"{'='*70}", style="cyan")
        
        print_text(f"\n🔍 Executing against: {self.db_path}", style="white")
        print_text(f"\n📝 SQL Query:", style="yellow")
        print_text(sql, style="dim")
        
        results = super()._execute_sql(sql)
        
        if results is not None:
            print_text(f"\n✅ Execution successful", style="green")
            print_text(f"   Returned {len(results)} rows", style="dim")
            if results and len(results) > 0:
                print_text(f"   Sample columns: {list(results[0].keys())[:5]}", style="dim")
        else:
            print_text(f"\n❌ Execution failed", style="red")
        
        return results


def main():
    print_text("\n" + "="*70, style="bold cyan")
    print_text("🧪 RETRY LOGIC TEST SUITE", style="bold cyan")
    print_text("="*70, style="bold cyan")
    
    print_text("\nThis test will:", style="white")
    print_text("  1. Generate a query that intentionally fails", style="dim")
    print_text("  2. Show validation feedback", style="dim")
    print_text("  3. Retry with feedback incorporated", style="dim")
    print_text("  4. Show how the system learns from failures", style="dim")
    print_text("\n" + "="*70 + "\n", style="dim")
    
    # Test query
    test_query = "trass gmail with term mgm from last month"
    
    print_text(f"🔍 Test Query: '{test_query}'", style="bold white")
    print_text("="*70 + "\n", style="dim")
    
    # Create test processor
    processor = RetryTestProcessor(debug=False)  # We're doing our own logging
    
    # Process query
    result = processor.process_query(test_query, max_retries=2)
    
    # Summary
    print_text("\n" + "="*70, style="bold cyan")
    print_text("📊 TEST SUMMARY", style="bold cyan")
    print_text("="*70, style="bold cyan")
    
    print_text(f"\nTotal SQL generation attempts: {processor.attempt_count}", style="white")
    print_text(f"Query successful: {result['success']}", style="green" if result['success'] else "red")
    
    if result['success']:
        print_text(f"Final result count: {result['summary']['total_count']}", style="green")
        print_text(f"Databases: {', '.join(result['summary']['databases'])}", style="green")
    else:
        print_text(f"Error: {result.get('error', 'Unknown')}", style="red")
    
    print_text("\n" + "="*70 + "\n", style="dim")
    
    # Show how the validation feedback evolved
    print_text("🔄 VALIDATION FEEDBACK EVOLUTION:", style="bold yellow")
    print_text("="*70, style="yellow")
    print_text("\nAttempt 1 → Attempt 2:", style="white")
    print_text("  Initial SQL returned 0 results", style="dim")
    print_text("  → Validation: 'No results found. Try broadening...'", style="dim")
    print_text("  → Retry with modified WHERE clause", style="dim")
    print_text("\nAttempt 2 → Attempt 3:", style="white")
    print_text("  Modified SQL still had issues", style="dim")
    print_text("  → Validation: Updated feedback", style="dim")
    print_text("  → Final retry with better SQL", style="dim")


if __name__ == "__main__":
    main()

