from fastapi import APIRouter, HTTPException, File, UploadFile, Form
from fastapi.responses import JSONResponse
from promaia.web.models import ChatMessageInput, ChatMessageOutput, InitialMessageOutput, ImageData, MessageContent
from promaia.utils.env_writer import get_prompts_dir

from promaia.ai.prompts import create_system_prompt
from promaia.utils.ai import debug_print, call_anthropic_with_retry
from promaia.utils.image_processing import (
    encode_image_from_bytes, format_image_for_openai, format_image_for_anthropic, 
    format_image_for_gemini, format_image_for_llama, is_vision_supported, get_model_image_limits
)
from promaia.ai.models import GOOGLE_MODELS, ANTHROPIC_MODELS
from promaia.config.databases import get_database_manager
from promaia.storage.files import load_database_pages_with_filters

import os
import traceback
from google import genai
import asyncio
import uuid
import random
from datetime import datetime
from typing import List, Optional
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
import requests
import httpx

router = APIRouter()

# Initialize AI Clients
from promaia.ai.models import get_current_google_model
gemini_model_name = get_current_google_model()
gemini_genai_client = None
anthropix_client = None
openai_client = None
llama_base_url = None

# Initialize Gemini
if os.getenv("GOOGLE_API_KEY"):
    try:
        gemini_genai_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        debug_print(f"Gemini API configured. Model to be used: {gemini_model_name}")
    except Exception as e:
        debug_print(f"Error configuring Gemini API: {e}. Gemini features will be unavailable.")
else:
    debug_print("GOOGLE_API_KEY not found. Gemini features will be unavailable.")

# Initialize Anthropic
if os.getenv("ANTHROPIC_API_KEY"):
    try:
        anthropix_client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        debug_print("Anthropic API configured.")
    except Exception as e:
        debug_print(f"Error configuring Anthropic API: {e}. Anthropic features will be unavailable.")
else:
    debug_print("ANTHROPIC_API_KEY not found. Anthropic features will be unavailable.")

# Initialize OpenAI
if os.getenv("OPENAI_API_KEY"):
    try:
        openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        debug_print("OpenAI API configured.")
    except Exception as e:
        debug_print(f"Error configuring OpenAI API: {e}. OpenAI features will be unavailable.")
else:
    debug_print("OPENAI_API_KEY not found. OpenAI features will be unavailable.")

# Initialize Llama (local)
if os.getenv("LLAMA_BASE_URL"):
    llama_base_url = os.getenv("LLAMA_BASE_URL")
    debug_print(f"Llama local server configured at: {llama_base_url}")
else:
    debug_print("LLAMA_BASE_URL not found. Local Llama features will be unavailable.")

def load_initial_message_prompt():
    """Load the initial message prompt from the markdown file and add randomness for variety."""
    prompt_file_path = str(get_prompts_dir() / "initial-message-prompt.md")
    
    try:
        with open(prompt_file_path, 'r', encoding='utf-8') as f:
            base_prompt = f.read().strip()
        debug_print(f"Loaded initial message prompt from {prompt_file_path}")
    except FileNotFoundError:
        debug_print(f"Initial message prompt file not found at {prompt_file_path}")
        base_prompt = "Based on the context you have, create an engaging opening question or comment to start a conversation. This should be specific to the content in my journals/blog, reflect my voice, and be phrased as a first-person statement or question a human would naturally ask. Keep it under 100 characters if possible."
    except Exception as e:
        debug_print(f"Error loading initial message prompt: {e}")
        base_prompt = "Based on the context you have, create an engaging opening question or comment to start a conversation."
    
    # Add subtle variety elements that encourage different types of responses
    # These don't change the core instruction but give the AI different creative angles
    variety_elements = [
        "Focus on something that might spark curiosity or introspection.",
        "Consider drawing from a recent insight or reflection.",
        "Think about what might resonate most with someone seeking growth.",
        "Choose something that invites deeper conversation.",
        "Pick something that feels authentic and personally meaningful.",
        "Consider what would genuinely interest someone exploring these ideas.",
        "Focus on an element that might inspire or motivate reflection.",
        "Choose something that feels conversational and inviting.",
    ]
    
    # Randomly select one variety element
    selected_element = random.choice(variety_elements)
    
    # Combine the base prompt with the variety element
    enhanced_prompt = f"{base_prompt} {selected_element}"
    
    debug_print(f"Enhanced prompt with variety element: {selected_element}")
    return enhanced_prompt

@router.get("/initial-message", response_model=InitialMessageOutput)
async def get_initial_message():
    debug_print("--- get_initial_message invoked (Gemini) ---")
    
    conversation_id = str(uuid.uuid4())
    debug_print(f"Generated conversation ID: {conversation_id}")
    
    multi_source_data = {}
    try:
        db_manager = get_database_manager()
        cms_db_config = db_manager.get_database("cms") # Assuming 'cms' is the nickname
        if cms_db_config:
            cms_data = load_database_pages_with_filters(cms_db_config)
            multi_source_data['cms'] = cms_data
            debug_print(f"Loaded {len(cms_data)} CMS entries.")
        else:
            debug_print("CMS database config not found.")
    except Exception as e:
        debug_print(f"Error reading content files: {e}\\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to read content entries.")

    system_prompt_str = create_system_prompt(multi_source_data)
    if not system_prompt_str:
        debug_print("Warning: create_system_prompt returned an empty string. Using fallback.")
        system_prompt_str = "You are a helpful AI."

    initial_message = "Welcome to KOii's journal! How can I help you today?"

    if not gemini_genai_client:
        debug_print("Gemini client not available (check GOOGLE_API_KEY and configuration).")
        return InitialMessageOutput(message=initial_message, conversation_id=conversation_id)  # Return default message as fallback

    try:
        instruction = load_initial_message_prompt()
        conversation_starters = [
            "with fresh curiosity", "with genuine interest",
            "with thoughtful reflection", "with warm engagement",
            "with open wonder", "with authentic connection",
            "with mindful presence", "with gentle inquiry"
        ]
        random_starter = random.choice(conversation_starters)
        enhanced_instruction = f"{instruction} Approach this {random_starter}."

        debug_print(f"Attempting to generate initial message with Gemini. Starter: {random_starter}")

        from google.genai import types as genai_types
        response = await asyncio.to_thread(
            gemini_genai_client.models.generate_content,
            model=gemini_model_name,
            contents=[{'role': 'user', 'parts': [enhanced_instruction]}],
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt_str,
                temperature=1.0, top_p=0.95, top_k=40,
                max_output_tokens=150,
            ),
        )
        
        if response and response.text:
            initial_message = response.text.strip()
            debug_print(f"Generated initial message: {initial_message}")
            
            # Log token usage for initial message generation
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage = response.usage_metadata
                prompt_tokens = getattr(usage, 'prompt_token_count', 0)
                response_tokens = getattr(usage, 'candidates_token_count', 0)
                total_tokens = getattr(usage, 'total_token_count', 0)
                debug_print(f"Initial message token usage: {prompt_tokens:,} prompt + {response_tokens:,} response = {total_tokens:,} total")

    except Exception as e:
        debug_print(f"Error generating initial message: {e}\\n{traceback.format_exc()}")
        # Fall back to default message on error, don't raise exception

    return InitialMessageOutput(message=initial_message, conversation_id=conversation_id)

@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...)):
    """Handle image upload and return base64 encoded data."""
    try:
        # Validate file type
        if not file.content_type or not file.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="File must be an image")
        
        # Read file data
        image_data = await file.read()
        
        # Process and encode image
        encoded_data = encode_image_from_bytes(
            image_data, 
            filename=file.filename,
            media_type=file.content_type
        )
        
        return JSONResponse(content={
            "success": True,
            "data": encoded_data['data'],
            "media_type": encoded_data['media_type'],
            "filename": file.filename
        })
        
    except Exception as e:
        debug_print(f"Error uploading image: {e}")
        raise HTTPException(status_code=400, detail=str(e))

def get_provider_from_model_id(model_id: str) -> str:
    """Determine the provider type from a model ID."""
    if not model_id:
        return "gemini"  # Default

    # Check if it's a provider type (for backwards compatibility)
    if model_id in ["gemini", "anthropic", "openai", "llama"]:
        return model_id

    # Detect provider from model ID
    if "claude" in model_id.lower():
        return "anthropic"
    elif "gemini" in model_id.lower():
        return "gemini"
    elif "gpt" in model_id.lower():
        return "openai"
    elif "llama" in model_id.lower() or "mistral" in model_id.lower() or "mixtral" in model_id.lower() or "codellama" in model_id.lower():
        return "llama"

    # Default to gemini if unknown
    return "gemini"

@router.post("/message", response_model=ChatMessageOutput)
async def handle_chat_message(chat_input: ChatMessageInput):
    debug_print("--- handle_chat_message invoked ---")
    user_message = chat_input.message
    conversation_id = chat_input.conversation_id or str(uuid.uuid4())
    message_history = chat_input.history or []

    # Support both specific model IDs and provider types
    model_id = chat_input.preferred_model or "gemini-3-flash-preview"
    provider_type = get_provider_from_model_id(model_id)

    # If it's just a provider type, get the default model ID for that provider
    if model_id in ["gemini", "anthropic", "openai", "llama"]:
        if model_id == "gemini":
            model_id = GOOGLE_MODELS.get("flash", "gemini-3-flash-preview")
        elif model_id == "anthropic":
            model_id = ANTHROPIC_MODELS.get("sonnet", "claude-sonnet-4-5")
        elif model_id == "openai":
            model_id = "gpt-4o"
        # llama stays as is (will be handled by env var)

    images = chat_input.images or []

    debug_print(f"User message: {user_message}")
    debug_print(f"Conversation ID: {conversation_id}")
    debug_print(f"Message history length: {len(message_history)}")
    debug_print(f"Model ID: {model_id}")
    debug_print(f"Provider type: {provider_type}")
    debug_print(f"Image attachments: {len(images)}")
    
    multi_source_data = {}
    try:
        db_manager = get_database_manager()
        cms_db_config = db_manager.get_database("cms") # Assuming 'cms' is the nickname
        if cms_db_config:
            cms_data = load_database_pages_with_filters(cms_db_config)
            multi_source_data['cms'] = cms_data
            debug_print(f"Loaded {len(cms_data)} CMS entries.")
        else:
            debug_print("CMS database config not found.")
    except Exception as e:
        debug_print(f"Error reading content files: {e}\\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to read content entries.")

    system_prompt_str = create_system_prompt(multi_source_data)
    if not system_prompt_str:
        debug_print("Warning: create_system_prompt returned an empty string. Using fallback.")
        system_prompt_str = "You are a helpful AI."

    # Validate model availability
    model_clients = {
        "gemini": gemini_genai_client,
        "anthropic": anthropix_client is not None,
        "openai": openai_client is not None,
        "llama": llama_base_url is not None
    }

    if provider_type not in model_clients or not model_clients[provider_type]:
        available_providers = [k for k, v in model_clients.items() if v]
        if not available_providers:
            raise HTTPException(status_code=503, detail="No AI models are configured.")

        # Fall back to first available provider
        provider_type = available_providers[0]
        if provider_type == "gemini":
            model_id = GOOGLE_MODELS.get("flash", "gemini-3-flash-preview")
        elif provider_type == "anthropic":
            model_id = ANTHROPIC_MODELS.get("sonnet", "claude-sonnet-4-5")
        elif provider_type == "openai":
            model_id = "gpt-4o"
        debug_print(f"Requested provider not available, falling back to: {provider_type} (model: {model_id})")

    # Check image support
    if images and not is_vision_supported(provider_type):
        raise HTTPException(status_code=400, detail=f"Model '{model_id}' does not support image inputs.")

    # Validate image limits
    if images:
        limits = get_model_image_limits(provider_type)
        if len(images) > limits['max_images']:
            raise HTTPException(
                status_code=400,
                detail=f"Model '{model_id}' supports maximum {limits['max_images']} images per message."
            )

    ai_reply_content = f"Sorry, I couldn't process that with {model_id}."

    try:
        # Route to appropriate model handler
        if provider_type == "gemini":
            ai_reply_content, token_usage_data = await _handle_gemini(
                user_message, images, message_history, system_prompt_str, model_id
            )
        elif provider_type == "anthropic":
            ai_reply_content, token_usage_data = await _handle_anthropic(
                user_message, images, message_history, system_prompt_str, model_id
            )
        elif provider_type == "openai":
            ai_reply_content, token_usage_data = await _handle_openai(
                user_message, images, message_history, system_prompt_str, model_id
            )
        elif provider_type == "llama":
            ai_reply_content, token_usage_data = await _handle_llama(
                user_message, images, message_history, system_prompt_str, model_id
            )
        else:
            raise ValueError(f"Unknown provider: {provider_type}")

    except HTTPException as e:
        raise e
    except Exception as e:
        error_detail_msg = f"Error calling AI model '{model_id}': {e}"
        debug_print(error_detail_msg)
        debug_print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_detail_msg)

    if not ai_reply_content:
        debug_print(f"AI model '{model_id}' returned empty content.")
        raise HTTPException(status_code=500, detail=f"AI service ({model_id}) failed to generate a response.")

    return ChatMessageOutput(
        reply=ai_reply_content,
        conversation_id=conversation_id,
        model_used=model_id,
        token_usage=token_usage_data
    )

async def _handle_gemini(user_message: str, images: List[ImageData], message_history: list, system_prompt: str, model_id: str = None):
    """Handle Gemini model requests with image support."""
    # Use provided model_id or fall back to default
    if not model_id:
        model_id = gemini_model_name

    # Build conversation history for Gemini
    gemini_messages = []
    for msg in message_history:
        role = 'user' if msg.role == 'user' else 'model'
        if hasattr(msg, 'get_text_content'):
            text_content = msg.get_text_content()
            msg_images = msg.get_images()
        else:
            text_content = str(msg.content)
            msg_images = []
        parts = []
        if text_content:
            parts.append(text_content)
        for img in msg_images:
            parts.append(format_image_for_gemini(img.data, img.media_type))
        gemini_messages.append({'role': role, 'parts': parts})

    current_parts = []
    if user_message:
        current_parts.append(user_message)
    for img in images:
        current_parts.append(format_image_for_gemini(img.data, img.media_type))
    gemini_messages.append({'role': 'user', 'parts': current_parts})

    debug_print(f"Calling Gemini with {len(gemini_messages)} messages and {len(images)} images")

    from google.genai import types as genai_types
    response = await asyncio.to_thread(
        gemini_genai_client.models.generate_content,
        model=model_id,
        contents=gemini_messages,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.7,
        ),
    )
    
    ai_reply_content = response.text
    debug_print(f"Got reply from Gemini: {ai_reply_content[:100]}...")
    
    # Extract token usage
    token_usage_data = None
    if hasattr(response, 'usage_metadata') and response.usage_metadata:
        usage = response.usage_metadata
        prompt_tokens = getattr(usage, 'prompt_token_count', 0)
        response_tokens = getattr(usage, 'candidates_token_count', 0)
        total_tokens = getattr(usage, 'total_token_count', 0)
        
        from promaia.utils.ai import calculate_ai_cost
        from promaia.ai.models import get_current_google_model
        # Use Gemini 3 Flash for cost calculation
        model_tier = get_current_google_model()
        cost_data = calculate_ai_cost(prompt_tokens, response_tokens, model_tier)
        total_cost = cost_data["total_cost"]
        
        from promaia.web.models import TokenUsage
        token_usage_data = TokenUsage(
            prompt_tokens=prompt_tokens,
            response_tokens=response_tokens,
            total_tokens=total_tokens,
            cost=total_cost,
            model="Gemini 2.5 Pro"
        )
        debug_print(f"Token usage: {prompt_tokens:,} prompt + {response_tokens:,} response = {total_tokens:,} total, cost: ${total_cost:.6f}")
    
    return ai_reply_content, token_usage_data

async def _handle_anthropic(user_message: str, images: List[ImageData], message_history: list, system_prompt: str, model_id: str = None):
    """Handle Anthropic Claude model requests with image support."""
    # Build message history for Anthropic
    anthropic_messages = []

    for msg in message_history:
        # Handle message content
        if hasattr(msg, 'get_text_content'):
            text_content = msg.get_text_content()
            msg_images = msg.get_images()
        else:
            text_content = str(msg.content)
            msg_images = []

        content_parts = []
        if text_content:
            content_parts.append({"type": "text", "text": text_content})

        # Add images to message
        for img in msg_images:
            content_parts.append(format_image_for_anthropic(img.data, img.media_type))

        anthropic_messages.append({
            "role": msg.role,
            "content": content_parts
        })

    # Add current user message
    current_content = []
    if user_message:
        current_content.append({"type": "text", "text": user_message})

    for img in images:
        current_content.append(format_image_for_anthropic(img.data, img.media_type))

    anthropic_messages.append({
        "role": "user",
        "content": current_content
    })

    debug_print(f"Calling Anthropic with {len(anthropic_messages)} messages and {len(images)} images")

    # Use provided model_id or fall back to Sonnet 4.5
    if not model_id:
        model_id = ANTHROPIC_MODELS.get("sonnet", "claude-sonnet-4-5")

    debug_print(f"Using Anthropic model: {model_id}")
    response_content = await call_anthropic_with_retry(
        anthropix_client,
        system_prompt,
        anthropic_messages,
        model_name=model_id,
        max_tokens=4096
    )
    
    debug_print(f"Got reply from Anthropic: {response_content[:100]}...")
    
    # Token usage estimation (Anthropic doesn't provide detailed usage in response)
    from promaia.utils.ai import _improved_token_estimate, calculate_ai_cost
    estimated_prompt = _improved_token_estimate(system_prompt + str(anthropic_messages))
    estimated_response = _improved_token_estimate(response_content)
    
    cost_data = calculate_ai_cost(estimated_prompt, estimated_response, model_id)

    from promaia.web.models import TokenUsage
    token_usage_data = TokenUsage(
        prompt_tokens=estimated_prompt,
        response_tokens=estimated_response,
        total_tokens=estimated_prompt + estimated_response,
        cost=cost_data["total_cost"],
        model=get_model_display_name(model_id, "anthropic")
    )
    
    return response_content, token_usage_data

async def _handle_openai(user_message: str, images: List[ImageData], message_history: list, system_prompt: str, model_id: str = None):
    """Handle OpenAI GPT-4o model requests with image support."""
    # Build message history for OpenAI
    openai_messages = [{"role": "system", "content": system_prompt}]
    
    for msg in message_history:
        # Handle message content
        if hasattr(msg, 'get_text_content'):
            text_content = msg.get_text_content()
            msg_images = msg.get_images()
        else:
            text_content = str(msg.content)
            msg_images = []
        
        content_parts = []
        if text_content:
            content_parts.append({"type": "text", "text": text_content})
        
        # Add images to message
        for img in msg_images:
            content_parts.append(format_image_for_openai(img.data, img.media_type))
        
        openai_messages.append({
            "role": msg.role,
            "content": content_parts if content_parts else text_content
        })
    
    # Add current user message
    current_content = []
    if user_message:
        current_content.append({"type": "text", "text": user_message})
    
    for img in images:
        current_content.append(format_image_for_openai(img.data, img.media_type))
    
    openai_messages.append({
        "role": "user", 
        "content": current_content if current_content else user_message
    })
    
    # Use provided model_id or fall back to gpt-4o
    if not model_id:
        model_id = "gpt-4o"

    debug_print(f"Calling OpenAI with model {model_id}, {len(openai_messages)} messages and {len(images)} images")

    response = await openai_client.chat.completions.create(
        model=model_id,
        messages=openai_messages,
        max_tokens=4096,
        temperature=0.7
    )
    
    ai_reply_content = response.choices[0].message.content
    debug_print(f"Got reply from OpenAI: {ai_reply_content[:100]}...")
    
    # Extract token usage
    token_usage_data = None
    if response.usage:
        usage = response.usage
        from promaia.utils.ai import calculate_ai_cost
        cost_data = calculate_ai_cost(usage.prompt_tokens, usage.completion_tokens, "gpt-4o")
        
        from promaia.web.models import TokenUsage
        token_usage_data = TokenUsage(
            prompt_tokens=usage.prompt_tokens,
            response_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            cost=cost_data["total_cost"],
            model="GPT-4o"
        )
        debug_print(f"Token usage: {usage.prompt_tokens:,} prompt + {usage.completion_tokens:,} response = {usage.total_tokens:,} total, cost: ${cost_data['total_cost']:.6f}")
    
    return ai_reply_content, token_usage_data

async def _handle_llama(user_message: str, images: List[ImageData], message_history: list, system_prompt: str, model_id: str = None):
    """Handle local Llama model requests with image support."""
    # Build message history for Llama (OpenAI-compatible format)
    llama_messages = [{"role": "system", "content": system_prompt}]
    
    for msg in message_history:
        # Handle message content
        if hasattr(msg, 'get_text_content'):
            text_content = msg.get_text_content()
            msg_images = msg.get_images()
        else:
            text_content = str(msg.content)
            msg_images = []
        
        content_parts = []
        if text_content:
            content_parts.append({"type": "text", "text": text_content})
        
        # Add images (only first one for most local vision models)
        if msg_images:
            content_parts.append(format_image_for_llama(msg_images[0].data, msg_images[0].media_type))
        
        llama_messages.append({
            "role": msg.role,
            "content": content_parts if content_parts else text_content
        })
    
    # Add current user message (only first image for most local models)
    current_content = []
    if user_message:
        current_content.append({"type": "text", "text": user_message})
    
    if images:
        current_content.append(format_image_for_llama(images[0].data, images[0].media_type))
    
    llama_messages.append({
        "role": "user",
        "content": current_content if current_content else user_message
    })
    
    # Use provided model_id or fall back to env var or default
    if not model_id:
        model_id = os.getenv("LLAMA_DEFAULT_MODEL", "llama3:latest")

    debug_print(f"Calling local Llama with model {model_id}, {len(llama_messages)} messages and {min(len(images), 1)} images")

    # Call local Llama server (OpenAI-compatible)
    payload = {
        "model": model_id,
        "messages": llama_messages,
        "max_tokens": 4096,
        "temperature": 0.7
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{llama_base_url.rstrip('/')}/v1/chat/completions",
            json=payload,
            timeout=120.0
        )
        response.raise_for_status()
        result = response.json()
    
    ai_reply_content = result["choices"][0]["message"]["content"]
    debug_print(f"Got reply from Llama: {ai_reply_content[:100]}...")
    
    # Token usage estimation (most local servers don't provide detailed usage)
    from promaia.utils.ai import _improved_token_estimate
    estimated_prompt = _improved_token_estimate(system_prompt + str(llama_messages))
    estimated_response = _improved_token_estimate(ai_reply_content)
    
    from promaia.web.models import TokenUsage
    token_usage_data = TokenUsage(
        prompt_tokens=estimated_prompt,
        response_tokens=estimated_response,
        total_tokens=estimated_prompt + estimated_response,
        cost=0.0,  # Local models have no cost
        model="Local Llama"
    )
    
    return ai_reply_content, token_usage_data

@router.get("/models")
async def get_available_models():
    """Get list of available AI models with their capabilities."""
    model_clients = {
        "gemini": gemini_genai_client,
        "anthropic": anthropix_client is not None,
        "openai": openai_client is not None,
        "llama": llama_base_url is not None
    }

    available_models = []
    from promaia.ai.models import get_model_display_name, ANTHROPIC_MODELS, GOOGLE_MODELS, LLAMA_MODELS
    import os

    # Add all Anthropic models if client is available
    if model_clients["anthropic"]:
        limits = get_model_image_limits("anthropic")
        for key, model_id in ANTHROPIC_MODELS.items():
            model_info = {
                "type": "anthropic",
                "model_id": model_id,
                "name": get_model_display_name(model_id, "anthropic"),
                "vision_supported": is_vision_supported("anthropic"),
                "max_images": limits["max_images"],
                "supported_formats": limits["supported_formats"]
            }
            available_models.append(model_info)

    # Add all Google models if client is available
    if model_clients["gemini"]:
        limits = get_model_image_limits("gemini")
        # Only add main models (flash and pro)
        for key in ["flash", "pro"]:
            if key in GOOGLE_MODELS:
                model_id = GOOGLE_MODELS[key]
                model_info = {
                    "type": "gemini",
                    "model_id": model_id,
                    "name": get_model_display_name(model_id, "gemini"),
                    "vision_supported": is_vision_supported("gemini"),
                    "max_images": limits["max_images"],
                    "supported_formats": limits["supported_formats"]
                }
                available_models.append(model_info)

    # Add OpenAI models if client is available
    if model_clients["openai"]:
        limits = get_model_image_limits("openai")
        for model_id in ["gpt-4o", "gpt-4o-mini"]:
            model_info = {
                "type": "openai",
                "model_id": model_id,
                "name": get_model_display_name(model_id, "openai"),
                "vision_supported": is_vision_supported("openai"),
                "max_images": limits["max_images"],
                "supported_formats": limits["supported_formats"]
            }
            available_models.append(model_info)

    # Add Llama models if client is available
    if model_clients["llama"]:
        limits = get_model_image_limits("llama")
        llama_model_id = os.getenv('LLAMA_DEFAULT_MODEL', 'llama3:latest')
        model_info = {
            "type": "llama",
            "model_id": llama_model_id,
            "name": get_model_display_name(llama_model_id, "llama"),
            "vision_supported": is_vision_supported("llama"),
            "max_images": limits["max_images"],
            "supported_formats": limits["supported_formats"]
        }
        available_models.append(model_info)

    # Determine default model (prefer Gemini Flash, then first available)
    default_model = None
    if gemini_genai_client:
        default_model = "gemini-3-flash-preview"
    elif available_models:
        default_model = available_models[0]["model_id"]

    return {
        "available_models": available_models,
        "default_model": default_model
    }

# You can add other chat-related endpoints here if needed. 