#!/usr/bin/env python3
"""
Deep test of retry logic showing EXACT prompts sent to AI at each step.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from promaia.utils.config import load_environment
load_environment()

from promaia.ai.nl_orchestrator import AgenticNLQueryProcessor
from promaia.utils.display import print_text

class DeepLoggingProcessor(AgenticNLQueryProcessor):
    """Processor that logs EVERY prompt and response in detail."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.call_log = []
    
    def _parse_intent(self, user_query, schema, workspace=None):
        """Log intent parsing prompt."""
        print_text("\n" + "="*80, style="bold cyan")
        print_text("🧠 STEP 1: INTENT PARSING", style="bold cyan")
        print_text("="*80, style="bold cyan")
        
        print_text("\n📤 Input to AI:", style="yellow")
        print_text(f"   User Query: '{user_query}'", style="white")
        print_text(f"   Available Databases: {schema.get('available_databases', [])[:5]}...", style="dim")
        
        # Call parent
        intent = super()._parse_intent(user_query, schema, workspace)
        
        print_text("\n📥 AI Response:", style="green")
        if intent:
            print_text(f"   Goal: {intent.get('goal')}", style="white")
            print_text(f"   Databases: {intent.get('databases')}", style="white")
            print_text(f"   Search Terms: {intent.get('search_terms')}", style="white")
            print_text(f"   Date Filter: {intent.get('date_filter')}", style="white")
        else:
            print_text("   ❌ Parsing failed", style="red")
        
        return intent
    
    def _generate_sql(self, intent, schema, retry_attempt=0):
        """Log SQL generation prompt in detail."""
        print_text("\n" + "="*80, style="bold cyan")
        print_text(f"⚙️  STEP 2: SQL GENERATION (Attempt {retry_attempt + 1})", style="bold cyan")
        print_text("="*80, style="bold cyan")
        
        # Show what's being sent to the AI
        print_text("\n📤 Context sent to AI:", style="yellow")
        print_text(f"   Intent Goal: {intent['goal']}", style="white")
        print_text(f"   Target Databases: {intent['databases']}", style="white")
        print_text(f"   Search Terms: {intent.get('search_terms', [])}", style="white")
        
        # Show learned patterns being used
        patterns = self.learning_system.load_successful_patterns()
        print_text(f"\n📚 Using {len(patterns)} learned patterns:", style="yellow")
        for i, pattern in enumerate(patterns[:2], 1):  # Show first 2
            print_text(f"   {i}. '{pattern['user_query']}' → {pattern['result_count']} results", style="dim")
        
        # Show validation feedback if retry
        if retry_attempt > 0 and intent.get('_validation_feedback'):
            print_text(f"\n⚠️  Validation Feedback from Attempt {retry_attempt}:", style="red")
            print_text(f"   {intent['_validation_feedback']}", style="white")
            print_text("\n💡 The AI will now adjust the SQL based on this feedback", style="yellow")
        
        # Show schema samples being provided
        print_text(f"\n🗄️  Schema info provided:", style="yellow")
        schema_text = self._format_schema_for_prompt(schema)
        # Show first 500 chars of schema
        print_text(schema_text[:500] + "..." if len(schema_text) > 500 else schema_text, style="dim")
        
        # Generate SQL
        sql = super()._generate_sql(intent, schema, retry_attempt)
        
        # Show generated SQL
        print_text("\n📥 AI Generated SQL:", style="green")
        if sql:
            print_text(sql, style="white")
            
            # Analyze the SQL
            print_text("\n🔍 SQL Analysis:", style="yellow")
            if "message_content" in sql.lower():
                print_text("   ✅ Searches message_content (email body)", style="green")
            else:
                print_text("   ⚠️  Doesn't search message_content", style="yellow")
            
            if "LEFT JOIN gmail_content" in sql:
                print_text("   ✅ Joins with gmail_content", style="green")
            else:
                print_text("   ⚠️  No JOIN with gmail_content", style="yellow")
            
            # Count search fields
            like_count = sql.upper().count("LIKE")
            print_text(f"   📊 Searching {like_count} fields with LIKE", style="dim")
        else:
            print_text("   ❌ SQL generation failed", style="red")
        
        # Inject error on first attempt
        if retry_attempt == 0 and sql:
            print_text("\n🐛 INJECTING TEST ERROR:", style="bold red")
            print_text("   Breaking SQL to force retry...", style="red")
            sql = sql.replace("WHERE", "WHERE 1=0 AND")
            print_text("\n💥 Broken SQL:", style="red")
            print_text(sql, style="dim")
        
        return sql
    
    def _execute_sql(self, sql):
        """Log execution."""
        print_text("\n" + "="*80, style="bold cyan")
        print_text("⚡ STEP 3: SQL EXECUTION", style="bold cyan")
        print_text("="*80, style="bold cyan")
        
        print_text(f"\n🔍 Executing:", style="yellow")
        print_text(sql[:200] + "..." if len(sql) > 200 else sql, style="dim")
        
        results = super()._execute_sql(sql)
        
        print_text(f"\n📊 Result: {len(results) if results else 0} rows", style="green" if results else "red")
        
        return results


def main():
    print_text("\n" + "="*80, style="bold magenta")
    print_text("🔬 DEEP RETRY LOGIC TEST - FULL PROMPT LOGGING", style="bold magenta")
    print_text("="*80, style="bold magenta")
    
    print_text("\nThis test shows:", style="white")
    print_text("  • Exact data sent to AI at each step", style="dim")
    print_text("  • How validation feedback is incorporated into retries", style="dim")
    print_text("  • What learned patterns are being used", style="dim")
    print_text("  • How the AI adjusts SQL based on failures", style="dim")
    print_text("\n" + "="*80 + "\n", style="dim")
    
    test_query = "trass gmail with term mgm from last month"
    
    print_text(f"🎯 Test Query: '{test_query}'", style="bold white")
    print_text("\nThis will:", style="yellow")
    print_text("  1. Parse intent from natural language", style="dim")
    print_text("  2. Generate SQL (with injected error)", style="dim")
    print_text("  3. Execute and validate (will fail)", style="dim")
    print_text("  4. Retry with validation feedback", style="dim")
    print_text("  5. Show how AI adapts", style="dim")
    
    input("\n⏸️  Press Enter to start the test...")
    
    # Create processor
    processor = DeepLoggingProcessor(debug=False)
    
    # Run query
    result = processor.process_query(test_query, max_retries=2)
    
    # Final summary
    print_text("\n" + "="*80, style="bold magenta")
    print_text("📊 FINAL RESULTS", style="bold magenta")
    print_text("="*80, style="bold magenta")
    
    print_text(f"\nSuccess: {result['success']}", style="green" if result['success'] else "red")
    if result['success']:
        summary = result['summary']
        print_text(f"Found: {summary['total_count']} entries", style="green")
        print_text(f"Databases: {', '.join(summary['databases'])}", style="green")
        print_text(f"\nSample results:", style="white")
        for i, item in enumerate(summary['sample_results'][:3], 1):
            print_text(f"  {i}. {item.get('title', 'N/A')[:60]}", style="dim")
    else:
        print_text(f"Error: {result.get('error')}", style="red")
    
    print_text("\n" + "="*80 + "\n", style="dim")


if __name__ == "__main__":
    main()

