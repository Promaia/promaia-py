#!/usr/bin/env python3
"""
Test what sqlite3 actually returns for different error scenarios.
"""
import sqlite3

db_path = "data/hybrid_metadata.db"

print("="*70)
print("Testing SQLite3 Error Messages")
print("="*70)

# Test 1: Valid SQL, no results
print("\n1. VALID SQL with WHERE 1=0 (no results):")
try:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        sql = "SELECT * FROM gmail_content WHERE 1=0 LIMIT 5"
        cursor.execute(sql)
        results = cursor.fetchall()
        print(f"   ✅ Execution succeeded")
        print(f"   📊 Returned {len(results)} rows")
        print(f"   🔍 Type: {type(results)}")
except Exception as e:
    print(f"   ❌ Error: {e}")
    print(f"   🔍 Error type: {type(e).__name__}")

# Test 2: Syntax error
print("\n2. SYNTAX ERROR (missing FROM):")
try:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        sql = "SELECT * WHERE 1=1"  # Invalid - no FROM
        cursor.execute(sql)
        results = cursor.fetchall()
        print(f"   ✅ Execution succeeded")
        print(f"   📊 Returned {len(results)} rows")
except sqlite3.OperationalError as e:
    print(f"   ❌ OperationalError: {e}")
    print(f"   🔍 Error type: {type(e).__name__}")
except Exception as e:
    print(f"   ❌ Other error: {e}")
    print(f"   🔍 Error type: {type(e).__name__}")

# Test 3: Invalid table name
print("\n3. INVALID TABLE NAME:")
try:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        sql = "SELECT * FROM nonexistent_table LIMIT 5"
        cursor.execute(sql)
        results = cursor.fetchall()
        print(f"   ✅ Execution succeeded")
        print(f"   📊 Returned {len(results)} rows")
except sqlite3.OperationalError as e:
    print(f"   ❌ OperationalError: {e}")
    print(f"   🔍 Error type: {type(e).__name__}")
except Exception as e:
    print(f"   ❌ Other error: {e}")
    print(f"   🔍 Error type: {type(e).__name__}")

# Test 4: Invalid column name
print("\n4. INVALID COLUMN NAME:")
try:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        sql = "SELECT nonexistent_column FROM gmail_content LIMIT 5"
        cursor.execute(sql)
        results = cursor.fetchall()
        print(f"   ✅ Execution succeeded")
        print(f"   📊 Returned {len(results)} rows")
except sqlite3.OperationalError as e:
    print(f"   ❌ OperationalError: {e}")
    print(f"   🔍 Error type: {type(e).__name__}")
except Exception as e:
    print(f"   ❌ Other error: {e}")
    print(f"   🔍 Error type: {type(e).__name__}")

# Test 5: Bad JOIN
print("\n5. BAD JOIN (missing ON clause):")
try:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        sql = "SELECT * FROM unified_content u LEFT JOIN gmail_content g LIMIT 5"
        cursor.execute(sql)
        results = cursor.fetchall()
        print(f"   ✅ Execution succeeded")
        print(f"   📊 Returned {len(results)} rows")
except sqlite3.OperationalError as e:
    print(f"   ❌ OperationalError: {e}")
    print(f"   🔍 Error type: {type(e).__name__}")
except Exception as e:
    print(f"   ❌ Other error: {e}")
    print(f"   🔍 Error type: {type(e).__name__}")

print("\n" + "="*70)
print("KEY INSIGHT:")
print("  • Valid SQL + 0 results → returns [] (empty list)")
print("  • SQL error → raises sqlite3.OperationalError with message")
print("  • We should pass the error message to the AI for retry!")
print("="*70)

