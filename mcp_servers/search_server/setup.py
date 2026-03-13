#!/usr/bin/env python3
"""
Setup script for Perplexity Search MCP Server

This script helps configure the Perplexity API key and test the search server.
"""

import os
import sys
import subprocess
import json

def check_api_key():
    """Check if Perplexity API key is configured."""
    api_key = os.getenv('PERPLEXITY_API_KEY')
    if api_key:
        print("✅ PERPLEXITY_API_KEY is configured")
        return True
    else:
        print("❌ PERPLEXITY_API_KEY is not configured")
        return False

def setup_api_key():
    """Help user set up the Perplexity API key."""
    print("\n🔧 Setting up Perplexity API Key")
    print("="*50)
    print("1. Go to https://www.perplexity.ai/settings/api")
    print("2. Create an API key")
    print("3. Add it to your environment:")
    print("   export PERPLEXITY_API_KEY='your-api-key-here'")
    print("\nOr add it to your shell profile (~/.bashrc, ~/.zshrc, etc.)")
    print("For this session only, you can run:")
    print("   export PERPLEXITY_API_KEY='your-api-key-here'")

def test_search_server():
    """Test the search server with a simple query."""
    print("\n🧪 Testing Search Server")
    print("="*50)
    
    if not check_api_key():
        print("Cannot test without API key. Please configure PERPLEXITY_API_KEY first.")
        return False
    
    try:
        # Import and test the search server
        from search_server import SearchServer
        
        server = SearchServer()
        
        # Test with a simple query
        test_query = "current time"
        print(f"Testing with query: '{test_query}'")
        
        result = server.search_perplexity(test_query)
        
        if 'error' in result:
            print(f"❌ Test failed: {result['error']}")
            return False
        else:
            print("✅ Test successful!")
            print(f"Response preview: {result.get('content', '')[:100]}...")
            if result.get('citations'):
                print(f"Citations found: {len(result['citations'])}")
            return True
            
    except Exception as e:
        print(f"❌ Test failed with exception: {e}")
        return False

def main():
    """Main setup function."""
    print("🔍 Perplexity Search MCP Server Setup")
    print("="*50)
    
    # Check current status
    api_configured = check_api_key()
    
    if not api_configured:
        setup_api_key()
        return
    
    # If API key is configured, run test
    print("\nAPI key is configured. Running test...")
    success = test_search_server()
    
    if success:
        print("\n✅ Setup complete! Your search server is ready to use.")
        print("\nTo use with MCP:")
        print("1. Make sure the server is listed in your mcp_servers.json")
        print("2. Use the /mcp search command in your chat")
        print("3. Call web_search with your queries")
    else:
        print("\n❌ Setup incomplete. Please check your API key and try again.")

if __name__ == "__main__":
    main()
