#!/usr/bin/env python3
"""
Test showing difference between SQL errors vs 0 results.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from promaia.utils.config import load_environment
load_environment()

from promaia.ai.nl_orchestrator import AgenticNLQueryProcessor
from promaia.utils.display import print_text

class ErrorTestProcessor(AgenticNLQueryProcessor):
    """Inject different types of errors to test feedback."""
    
    def __init__(self, error_type="syntax", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.error_type = error_type
        self.attempt_num = 0
    
    def _generate_sql(self, intent, schema, retry_attempt=0):
        """Override to inject specific error types."""
        sql = super()._generate_sql(intent, schema, retry_attempt)
        
        self.attempt_num += 1
        
        # Only inject error on first attempt
        if self.attempt_num == 1:
            print_text(f"\n🐛 INJECTING {self.error_type.upper()} ERROR:", style="bold red")
            
            if self.error_type == "syntax":
                # Missing FROM clause
                sql = "SELECT page_id, title WHERE database_name = 'gmail'"
                print_text("   Removed FROM clause (syntax error)", style="red")
            
            elif self.error_type == "bad_table":
                # Nonexistent table
                sql = "SELECT * FROM nonexistent_table LIMIT 10"
                print_text("   Using non-existent table", style="red")
            
            elif self.error_type == "bad_column":
                # Nonexistent column
                sql = "SELECT nonexistent_column FROM gmail_content LIMIT 10"
                print_text("   Using non-existent column", style="red")
            
            elif self.error_type == "zero_results":
                # Valid SQL but no results
                sql = "SELECT * FROM gmail_content WHERE 1=0"
                print_text("   Valid SQL with WHERE 1=0 (returns 0 results)", style="red")
            
            print_text(f"\n💥 Injected SQL:\n{sql}\n", style="dim")
        
        return sql


def test_error_type(error_type, description):
    print_text("\n" + "="*80, style="bold cyan")
    print_text(f"TEST: {description}", style="bold cyan")
    print_text("="*80, style="bold cyan")
    
    processor = ErrorTestProcessor(error_type=error_type, debug=False)
    result = processor.process_query("gmail with term test", max_retries=1)
    
    print_text(f"\nResult: {'✅ Success' if result['success'] else '❌ Failed'}", 
               style="green" if result['success'] else "red")
    if not result['success']:
        print_text(f"Error: {result.get('error')}", style="dim")


def main():
    print_text("\n" + "="*80, style="bold magenta")
    print_text("🧪 SQL ERROR FEEDBACK TEST", style="bold magenta")
    print_text("="*80, style="bold magenta")
    
    print_text("\nThis test shows how different errors are reported:", style="white")
    print_text("  • SQL syntax errors → specific error message", style="dim")
    print_text("  • Invalid table → specific error message", style="dim")
    print_text("  • Invalid column → specific error message", style="dim")
    print_text("  • Valid SQL + 0 results → generic suggestion", style="dim")
    
    # Test each error type
    test_error_type("syntax", "Syntax Error (Missing FROM)")
    test_error_type("bad_table", "Invalid Table Name")
    test_error_type("bad_column", "Invalid Column Name")
    test_error_type("zero_results", "Valid SQL with 0 Results")
    
    print_text("\n" + "="*80, style="bold magenta")
    print_text("KEY DIFFERENCE:", style="bold yellow")
    print_text("="*80, style="yellow")
    print_text("\nSQL Errors (attempt 1):", style="white")
    print_text("  ❌ SQL Error: no tables specified", style="red")
    print_text("  → Retry feedback: 'SQL Error: no tables specified'", style="yellow")
    print_text("  → AI can fix specific problem!", style="green")
    
    print_text("\nValid SQL + 0 Results (attempt 1):", style="white")
    print_text("  ✅ SQL executed successfully (0 rows)", style="green")
    print_text("  → Validation feedback: 'No results found. Try broadening...'", style="yellow")
    print_text("  → AI tries different approach", style="green")
    
    print_text("\n" + "="*80 + "\n", style="dim")


if __name__ == "__main__":
    main()

