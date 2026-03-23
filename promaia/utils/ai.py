"""
Utilities for AI model interactions.
"""
import os
import time
import random
import asyncio
import tiktoken # For token counting
from typing import Optional # Import Optional
from anthropic import AsyncAnthropic
from fastapi import HTTPException # Keep for now, consider custom errors later
import json # For structured logging
from datetime import datetime # For timestamping log files
import logging # New import
from promaia.ai.models import ANTHROPIC_MODELS

logger = logging.getLogger(__name__) # New logger instance

# Global variables for rate limiting
ANTHROPIC_RATE_LIMIT_TOKENS = int(os.getenv("ANTHROPIC_RATE_LIMIT_TOKENS", 40000))
ANTHROPIC_LAST_REQUEST_TIME = 0
ANTHROPIC_TOKEN_USAGE = 0
ANTHROPIC_TOKEN_USAGE_RESET_TIME = 0
TOKEN_BUDGET_PERCENTAGE = float(os.getenv("TOKEN_BUDGET_PERCENTAGE", 0.8))
DEBUG_MODE = os.getenv("MAIA_DEBUG", "0") == "1"

# Initialize Anthropic client if API key is available
anthropic_client: Optional[AsyncAnthropic] = None
anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
if anthropic_api_key:
    anthropic_client = AsyncAnthropic(api_key=anthropic_api_key)
else:
    logger.warning("ANTHROPIC_API_KEY not found. Anthropic AI calls will fail.")

from promaia.utils.env_writer import get_data_dir
DEBUG_LOGS_DIR = str(get_data_dir() / "debug_logs" / "ai_calls")
if DEBUG_MODE:
    try:
        os.makedirs(DEBUG_LOGS_DIR, exist_ok=True)
        print(f"DEBUG: Created debug logs directory at {os.path.abspath(DEBUG_LOGS_DIR)}")
    except Exception as e:
        logger.error(f"Failed to create debug logs directory: {e}")

def debug_print(message: str):
    """Print debug messages if debug mode is enabled."""
    if DEBUG_MODE:
        prefix = "DEBUG (maia.utils.ai)"
        try:
            # Try to get current running loop and task to add more context
            loop = asyncio.get_running_loop()
            task = asyncio.current_task(loop)
            if task:
                prefix = f"{prefix} [{task.get_name()}]"
        except RuntimeError: # No running event loop
            pass
        logger.debug(f"{prefix}: {message}")

def estimate_token_count(text: str, model_type: str = "claude") -> int:
    """
    Estimate the number of tokens in a given text for a specific model type.
    Uses tiktoken for OpenAI models and improved estimation for Claude.
    """
    if not text:
        return 0
    
    if model_type == "openai":
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except ImportError:
            debug_print("Tiktoken not available. Install with: pip install tiktoken")
            return _improved_token_estimate(text)
        except Exception as e:
            debug_print(f"Tiktoken error: {e}. Falling back to improved estimation.")
            return _improved_token_estimate(text)
    elif model_type == "claude":
        return _improved_token_estimate(text)
    elif model_type == "gemini":
        # For Gemini, we should prefer API response token counts when available
        # This is just for estimation when no API response is available
        return _improved_token_estimate(text)
    else:
        debug_print(f"Unknown model type for token estimation: {model_type}. Using improved estimation.")
        return _improved_token_estimate(text)

def _improved_token_estimate(text: str) -> int:
    """
    Improved token estimation that considers word boundaries and common patterns.
    More accurate than simple character division for most text.
    """
    import re
    
    # Split on whitespace and common punctuation to get rough word count
    words = re.findall(r'\b\w+\b', text)
    word_count = len(words)
    
    # Count special characters and punctuation separately
    special_chars = len(re.findall(r'[^\w\s]', text))
    
    # Estimate tokens: roughly 0.75 tokens per word + punctuation
    # This is more accurate than len(text) // 3 for most text
    estimated_tokens = int(word_count * 0.75 + special_chars * 0.5)
    
    # Ensure we have a reasonable minimum
    return max(estimated_tokens, len(text) // 4)

def calculate_ai_cost(prompt_tokens: int, response_tokens: int, model_name: str = "claude-sonnet-4") -> dict:
    """
    Calculate the cost of AI API usage for various models.
    
    Args:
        prompt_tokens: Number of input tokens
        response_tokens: Number of output tokens  
        model_name: Name of the model used
    
    Returns:
        Dictionary with cost breakdown
    """
    # Pricing per 1M tokens (as of 2025)
    pricing = {
        "claude-sonnet-4": {
            "input_cost_per_million": 3.00,
            "output_cost_per_million": 15.00
        },
        "claude-opus-4.5": {
            "input_cost_per_million": 5.00,
            "output_cost_per_million": 25.00
        },
        "claude-opus-4": {
            "input_cost_per_million": 15.00,
            "output_cost_per_million": 75.00
        },
        "claude-3.5-sonnet": {
            "input_cost_per_million": 3.00,
            "output_cost_per_million": 15.00
        },
        "gpt-4o": {
            "input_cost_per_million": 2.50,
            "output_cost_per_million": 10.00
        },
        "gemini-3-flash": {
            "input_cost_per_million": 0.50,
            "output_cost_per_million": 3.00
        },
        "gemini-2.5-pro-short": {
            "input_cost_per_million": 1.25,
            "output_cost_per_million": 5.00
        },
        "gemini-2.5-pro-long": {
            "input_cost_per_million": 2.50,
            "output_cost_per_million": 10.00
        },
        "local-llama": {
            "input_cost_per_million": 0.00,
            "output_cost_per_million": 0.00
        }
    }
    
    # Map model names to pricing keys
    model_mapping = {
        "claude-opus-4-5-20251101": "claude-opus-4.5",
        "claude-opus-4-5-20250514": "claude-opus-4.5",
        "claude-opus-4-1-20250805": "claude-opus-4",
        "claude-opus-4-20250514": "claude-opus-4",
        "claude-sonnet-4-6": "claude-sonnet-4",
        "claude-sonnet-4-20250514": "claude-sonnet-4",
        "claude-sonnet-4-5-20250929": "claude-sonnet-4",
        "claude-3-5-sonnet-20241022": "claude-3.5-sonnet",
    }
    
    # Use mapping if available, otherwise use the model name as-is
    pricing_key = model_mapping.get(model_name, model_name)
    
    # Default to Claude Sonnet 4 if model not found
    if pricing_key not in pricing:
        pricing_key = "claude-sonnet-4"
        
    model_pricing = pricing[pricing_key]
    
    input_cost = (prompt_tokens / 1_000_000) * model_pricing["input_cost_per_million"]
    output_cost = (response_tokens / 1_000_000) * model_pricing["output_cost_per_million"]
    total_cost = input_cost + output_cost
    
    return {
        "input_cost": input_cost,
        "output_cost": output_cost, 
        "total_cost": total_cost,
        "model": model_name,
        "prompt_tokens": prompt_tokens,
        "response_tokens": response_tokens,
        "total_tokens": prompt_tokens + response_tokens
    }

def handle_rate_limit_basic():
    """
    Basic rate limit handling: resets token usage every minute.
    """
    global ANTHROPIC_TOKEN_USAGE, ANTHROPIC_TOKEN_USAGE_RESET_TIME
    now = time.time()
    if now >= ANTHROPIC_TOKEN_USAGE_RESET_TIME:
        debug_print(f"Resetting Anthropic token usage. Old usage: {ANTHROPIC_TOKEN_USAGE}, Reset time was: {ANTHROPIC_TOKEN_USAGE_RESET_TIME}")
        ANTHROPIC_TOKEN_USAGE = 0
        ANTHROPIC_TOKEN_USAGE_RESET_TIME = now + 60
        debug_print(f"New Anthropic token usage: {ANTHROPIC_TOKEN_USAGE}, New reset time: {ANTHROPIC_TOKEN_USAGE_RESET_TIME}")

async def call_anthropic_with_retry(
    client: AsyncAnthropic,
    system_prompt: str, 
    messages: list, 
    model_name: str = ANTHROPIC_MODELS.get("sonnet", "claude-sonnet-4-6"),
    max_tokens: int = 1024,
    temperature: float = 0.7, 
    max_retries: int = 3
) -> str:
    """Call Anthropic API with exponential backoff retry logic and debug logging."""
    global ANTHROPIC_TOKEN_USAGE, ANTHROPIC_LAST_REQUEST_TIME, ANTHROPIC_TOKEN_USAGE_RESET_TIME

    if not client:
        debug_print("Anthropic client not initialized. Cannot make API call.")
        raise HTTPException(status_code=503, detail="Anthropic client is not initialized. Check ANTHROPIC_API_KEY.")

    system_tokens = estimate_token_count(system_prompt, "claude")
    message_tokens = sum(estimate_token_count(msg.get("content", ""), "claude") for msg in messages)
    estimated_request_tokens = system_tokens + message_tokens
    
    debug_print(f"Estimated tokens for request: {estimated_request_tokens} (system: {system_tokens}, messages: {message_tokens}) to model {model_name}")
    
    handle_rate_limit_basic() 

    token_budget = ANTHROPIC_RATE_LIMIT_TOKENS * TOKEN_BUDGET_PERCENTAGE
    if ANTHROPIC_TOKEN_USAGE + estimated_request_tokens > token_budget:
        now = time.time()
        wait_time = 0
        if ANTHROPIC_TOKEN_USAGE_RESET_TIME > now:
            wait_time = (ANTHROPIC_TOKEN_USAGE_RESET_TIME - now)
            over_budget_factor = (ANTHROPIC_TOKEN_USAGE + estimated_request_tokens) / token_budget
            wait_time *= over_budget_factor 
        
        wait_time = max(wait_time, 1.0) 
        debug_print(f"🔄 Rate limit: Budget check. Usage {ANTHROPIC_TOKEN_USAGE} + Est. {estimated_request_tokens} > Budget {token_budget}. Throttling for {wait_time:.1f}s...")
        await asyncio.sleep(wait_time)
        handle_rate_limit_basic() 

    now = time.time()
    time_since_last_request = now - ANTHROPIC_LAST_REQUEST_TIME
    min_request_spacing = 0.5 
    if ANTHROPIC_LAST_REQUEST_TIME > 0 and time_since_last_request < min_request_spacing:
        wait_time = min_request_spacing - time_since_last_request
        debug_print(f"Spacing out requests, waiting {wait_time:.1f}s")
        await asyncio.sleep(wait_time)

    request_data_to_log = {
        "timestamp_utc": datetime.utcnow().isoformat(),
        "model_name": model_name,
        "max_tokens_requested": max_tokens,
        "temperature": temperature,
        "system_prompt": system_prompt,
        "messages": messages,
        "estimated_request_tokens": estimated_request_tokens
    }

    # Define a consistent timestamp format for filenames as per user request
    filename_timestamp_format = "%Y-%m-%d-%H%M%S_%f"

    for attempt in range(max_retries):
        try:
            ANTHROPIC_LAST_REQUEST_TIME = time.time()
            
            if DEBUG_MODE:
                debug_print(f"Attempt {attempt+1}/{max_retries} sending to model: {model_name}")
                debug_print(f"System Prompt (first 200 chars): {system_prompt[:200]}...")
                # Log each message separately for clarity, especially if there are multiple user/assistant turns
                for i, msg in enumerate(messages):
                    role = msg.get("role", "unknown")
                    content_preview = msg.get("content", "")[:200] # Preview first 200 chars
                    debug_print(f"Message {i+1} - Role: {role}, Content (first 200 chars): {content_preview}...")

            response = await client.messages.create(
                model=model_name,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=messages,
                temperature=temperature,
            )
            
            if not response.content or not response.content[0].text:
                if DEBUG_MODE:
                    failure_log_data = {
                        "request": request_data_to_log,
                        "error_type": "EmptyResponseContent",
                        "attempt": attempt + 1,
                        "raw_response_object": response.model_dump_json(indent=2)
                    }
                    # Use the new timestamp format for the filename
                    log_filename = f"{DEBUG_LOGS_DIR}/{datetime.utcnow().strftime(filename_timestamp_format)}_attempt{attempt+1}_empty_response.json"
                    with open(log_filename, 'w', encoding='utf-8') as f_log:
                        json.dump(failure_log_data, f_log, indent=2)
                    debug_print(f"Logged empty response details to {log_filename}")
                raise Exception("Empty response content from Anthropic API")

            assistant_message = response.content[0].text
            
            # Use actual token count from API response if available, otherwise estimate
            if hasattr(response, 'usage') and hasattr(response.usage, 'output_tokens'):
                response_tokens = response.usage.output_tokens
            else:
                response_tokens = estimate_token_count(assistant_message, "claude")
            total_tokens_for_call = estimated_request_tokens + response_tokens
            ANTHROPIC_TOKEN_USAGE += total_tokens_for_call
            now = time.time()
            if ANTHROPIC_TOKEN_USAGE_RESET_TIME <= now:
                 ANTHROPIC_TOKEN_USAGE_RESET_TIME = now + 60
            debug_print(f"API call successful. Response tokens: {response_tokens}, Total call tokens: {total_tokens_for_call}, Cumulative usage: {ANTHROPIC_TOKEN_USAGE}/{ANTHROPIC_RATE_LIMIT_TOKENS}")

            if DEBUG_MODE:
                success_log_data = {
                    "request": request_data_to_log,
                    "response": {
                        "assistant_message": assistant_message,
                        "response_tokens": response_tokens,
                        "total_tokens_for_call": total_tokens_for_call
                    },
                    "attempt": attempt + 1
                }
                # Use the new timestamp format for the filename
                log_filename = f"{DEBUG_LOGS_DIR}/{datetime.utcnow().strftime(filename_timestamp_format)}_attempt{attempt+1}_success.json"
                with open(log_filename, 'w', encoding='utf-8') as f_log:
                    json.dump(success_log_data, f_log, indent=2)
                debug_print(f"Logged successful AI call details to {log_filename}")
            
            return assistant_message
            
        except Exception as e:
            error_details = str(e)
            debug_print(f"Anthropic API error (attempt {attempt+1}/{max_retries}): {type(e).__name__} - {error_details}")
            
            if attempt == max_retries - 1 and DEBUG_MODE:
                final_failure_log_data = {
                    "request": request_data_to_log,
                    "error_type": type(e).__name__,
                    "error_details": error_details,
                    "final_attempt": attempt + 1
                }
                # Use the new timestamp format for the filename
                log_filename = f"{DEBUG_LOGS_DIR}/{datetime.utcnow().strftime(filename_timestamp_format)}_final_failure.json"
                with open(log_filename, 'w', encoding='utf-8') as f_log:
                    json.dump(final_failure_log_data, f_log, indent=2)
                debug_print(f"Logged final AI call failure details to {log_filename}")
            
            is_rate_limit = "rate_limit_error" in error_details.lower() or "429" in error_details
            is_auth_error = "authentication_error" in error_details.lower() or "permission_denied" in error_details.lower() or "401" in error_details or "403" in error_details
            is_api_error = "api_error" in error_details.lower() or "invalid_request_error" in error_details.lower() or "500" in error_details

            if is_rate_limit:
                wait_seconds = (2 ** attempt) + random.uniform(0, 1)
                seconds_to_next_minute = 60 - (time.time() % 60)
                if ANTHROPIC_TOKEN_USAGE_RESET_TIME > time.time():
                    actual_wait = (ANTHROPIC_TOKEN_USAGE_RESET_TIME - time.time()) + random.uniform(1,3)
                elif seconds_to_next_minute < wait_seconds + 5 :
                    actual_wait = seconds_to_next_minute + random.uniform(1,3)
                else:
                    actual_wait = wait_seconds
                debug_print(f"Rate limit hit. Waiting {actual_wait:.1f}s before retry {attempt+2}/{max_retries}...")
                if attempt < max_retries - 1:
                    await asyncio.sleep(actual_wait)
                    handle_rate_limit_basic()
                else:
                    debug_print(f"Rate limit reached and max retries ({max_retries}) exceeded.")
                    raise HTTPException(status_code=429, detail="AI service rate limit exceeded. Please try again later.") from e
            elif is_auth_error:
                 debug_print(f"Authentication error with Anthropic API: {error_details}")
                 raise HTTPException(status_code=500, detail="AI service authentication error. Check API key.") from e
            elif is_api_error and "invalid_request_error" in error_details.lower() and "invalid model" in error_details.lower():
                debug_print(f"Invalid model name specified: {model_name}. Error: {error_details}")
                raise HTTPException(status_code=400, detail=f"Invalid model for AI service: {model_name}") from e
            elif is_api_error:
                if attempt < max_retries - 1:
                    wait_seconds = (2 ** attempt) + random.uniform(0, 1)
                    debug_print(f"Retriable API error. Waiting {wait_seconds:.1f}s before retry {attempt+2}/{max_retries}...")
                    await asyncio.sleep(wait_seconds)
                else:
                    debug_print(f"API error after {max_retries} retries: {error_details}")
                    raise HTTPException(status_code=500, detail=f"AI service API error after multiple retries: {error_details}") from e
            else:
                if attempt < max_retries - 1:
                    wait_seconds = (2 ** attempt) + random.uniform(0, 1)
                    debug_print(f"Other error. Waiting {wait_seconds:.1f}s before retry {attempt+2}/{max_retries}...")
                    await asyncio.sleep(wait_seconds)
                else:
                    debug_print(f"Unhandled API error after {max_retries} retries: {error_details}")
                    raise HTTPException(status_code=500, detail=f"AI service unavailable after multiple retries: {error_details}") from e
    
    raise HTTPException(status_code=500, detail="Failed to get AI response after all retries.")

async def test_anthropic_call():
    """A simple test function for the Anthropic call."""
    if not anthropic_client:
        print("Anthropic client not configured. Skipping test.")
        return

    print("Testing Anthropic API call...")
    try:
        system_prompt = "You are a helpful assistant."
        messages = [{"role": "user", "content": "Hello, Claude! What is 2+2?"}]
        response = await call_anthropic_with_retry(
            client=anthropic_client,
            system_prompt=system_prompt,
            messages=messages,
            max_tokens=50
        )
        print(f"Test call successful. Response: {response}")
    except HTTPException as e:
        print(f"HTTPException during test call: {e.detail} (Status: {e.status_code})")
    except Exception as e:
        print(f"Error during Anthropic test call: {str(e)}")

if __name__ == "__main__":
    # This allows testing the call_anthropic_with_retry function directly
    # Ensure ANTHROPIC_API_KEY is in your .env file or environment
    # You might need to load .env if running this file directly and it's not auto-loaded
    from dotenv import load_dotenv
    load_dotenv() # Make sure .env is loaded if running this script directly

    # Re-initialize client here if running as script, as global might not be set if .env wasn't loaded initially
    api_key_main = os.getenv("ANTHROPIC_API_KEY")
    if api_key_main:
        anthropic_client_main = AsyncAnthropic(api_key=api_key_main)
        
        # Monkey patch the global client for the test function if it wasn't set
        if anthropic_client is None:
            anthropic_client = anthropic_client_main

        asyncio.run(test_anthropic_call())
    else:
        print("ANTHROPIC_API_KEY not found in environment. Cannot run test.") 