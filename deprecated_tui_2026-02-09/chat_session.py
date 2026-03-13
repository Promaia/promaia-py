"""
ChatSession - Encapsulates all state and AI interaction for a chat session.

Extracts the core chat loop state from interface.py into a reusable class
that can be used by both the CLI chat and TUI chat mode.
"""
import os
import logging
import time
from typing import List, Dict, Any, Optional

from promaia.utils.config import load_environment

logger = logging.getLogger(__name__)

# Ensure environment is loaded
load_environment()


class ChatSession:
    """
    Manages a single chat session's state and AI interactions.

    Encapsulates: messages, context, API clients, model selection,
    temperature, images, system prompt, artifacts, and query tools.
    """

    def __init__(
        self,
        sources: Optional[List[str]] = None,
        filters: Optional[List[str]] = None,
        workspace: Optional[str] = None,
        mcp_servers: Optional[List[str]] = None,
        mode=None,
        mode_config: Optional[Dict[str, Any]] = None,
        top_k: int = 60,
        threshold: float = 0.2,
    ):
        # Conversation history
        self.messages: List[Dict[str, str]] = []

        # Context state - mirrors interface.py context_state dict
        self.context_state: Dict[str, Any] = {
            'sources': sources or [],
            'filters': filters or [],
            'workspace': workspace,
            'initial_multi_source_data': {},
            'total_pages_loaded': 0,
            'system_prompt': None,
            'sql_query_content': None,
            'vector_search_content': None,
            'vector_search_per_query_cache': {},
            'vector_search_queries': [],
            'browse_selections': [],
            'mcp_servers': mcp_servers or [],
            'mcp_tools_info': None,
            'mode': mode,
            'mode_config': mode_config or {},
            'top_k': top_k,
            'threshold': threshold,
            'ai_queries': [],
            'query_iteration_count': 0,
            'artifact_manager': None,
            'context_muted': False,
            'enable_search': False,
            'enable_email_send': False,
            'loaded_image_paths': [],
        }

        # API state
        self.current_api: str = self._get_api_preference()
        self.current_model_id: Optional[str] = os.getenv("SELECTED_MODEL_ID")
        self.current_temperature: float = 0.7
        self.current_images: List[Dict[str, Any]] = []

        # Clients (lazy-initialized)
        self._anthropic_client = None
        self._openai_client = None
        self._gemini_client = None
        self._llama_client = None
        self._clients_initialized = False

        # Multi-source data (loaded context)
        self.combined_multi_source_data: Dict[str, Any] = {}
        self.system_prompt: str = ""

        # Initialize artifact manager
        try:
            from promaia.chat.artifacts import ArtifactManager
            self.context_state['artifact_manager'] = ArtifactManager()
        except ImportError:
            logger.debug("ArtifactManager not available")

    def _get_api_preference(self) -> str:
        """Get the preferred API from saved preferences or environment."""
        try:
            pref_file = os.path.join(os.path.expanduser("~"), ".maia_api_preference")
            if os.path.exists(pref_file):
                with open(pref_file, 'r') as f:
                    import json
                    prefs = json.load(f)
                    return prefs.get('api_type', 'anthropic')
        except Exception:
            pass

        # Default based on available keys
        if os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"
        elif os.getenv("OPENAI_API_KEY"):
            return "openai"
        elif os.getenv("GOOGLE_API_KEY"):
            return "gemini"
        return "anthropic"

    def _ensure_clients(self):
        """Initialize API clients if not already done."""
        if self._clients_initialized:
            return

        # Anthropic
        if os.getenv("ANTHROPIC_API_KEY"):
            try:
                from anthropic import Anthropic
                self._anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            except Exception as e:
                logger.error(f"Failed to initialize Anthropic client: {e}")

        # OpenAI
        if os.getenv("OPENAI_API_KEY"):
            try:
                from openai import OpenAI
                self._openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            except Exception as e:
                logger.error(f"Failed to initialize OpenAI client: {e}")

        # Gemini
        if os.getenv("GOOGLE_API_KEY"):
            try:
                import google.generativeai as genai
                genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
                from promaia.ai.models import get_current_google_model
                selected = os.getenv("SELECTED_MODEL_ID")
                if selected and "gemini" in selected.lower():
                    self._gemini_client = genai.GenerativeModel(selected)
                else:
                    self._gemini_client = genai.GenerativeModel(get_current_google_model())
            except Exception as e:
                logger.error(f"Failed to initialize Gemini client: {e}")

        # Llama (local)
        llama_base_url = os.getenv("LLAMA_BASE_URL")
        if llama_base_url:
            try:
                import requests
                test_url = f"{llama_base_url.rstrip('/')}/api/tags" if ":11434" in llama_base_url else f"{llama_base_url.rstrip('/')}/v1/models"
                response = requests.get(test_url, timeout=2)
                if response.status_code == 200:
                    from openai import OpenAI
                    self._llama_client = OpenAI(
                        base_url=f"{llama_base_url.rstrip('/')}/v1",
                        api_key=os.getenv("LLAMA_API_KEY", "local-llama"),
                    )
            except Exception as e:
                logger.debug(f"Llama server not available: {e}")

        self._clients_initialized = True

    def get_model_name(self) -> str:
        """Get the display name of the current model."""
        from promaia.ai.models import get_model_display_name, ANTHROPIC_MODELS, GOOGLE_MODELS

        selected = os.getenv("SELECTED_MODEL_ID")
        if selected:
            return get_model_display_name(selected, self.current_api)

        if self.current_api == "anthropic":
            return get_model_display_name(ANTHROPIC_MODELS.get("sonnet", "claude-sonnet-4-5"), "anthropic")
        elif self.current_api == "openai":
            return get_model_display_name("gpt-4o", "openai")
        elif self.current_api == "gemini":
            from promaia.ai.models import get_current_google_model
            return get_model_display_name(get_current_google_model(), "gemini")
        elif self.current_api == "llama":
            model_id = os.getenv("LLAMA_DEFAULT_MODEL", "llama3:latest")
            return get_model_display_name(model_id, "llama")
        return "Unknown Model"

    def build_system_prompt(self) -> str:
        """Build the system prompt from current context state."""
        from promaia.ai.prompts import create_system_prompt

        mcp_tools_info = self.context_state.get('mcp_tools_info')
        workspace = self.context_state.get('workspace')
        context_data = {} if self.context_state.get('context_muted') else self.combined_multi_source_data

        self.system_prompt = create_system_prompt(
            context_data,
            mcp_tools_info,
            include_query_tools=True,
            workspace=workspace,
        )
        self.context_state['system_prompt'] = self.system_prompt
        return self.system_prompt

    async def send_message(self, text: str, images: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        Send a message and get AI response.

        Args:
            text: User message text
            images: Optional list of image dicts with 'data' and 'media_type'

        Returns:
            Dict with 'text' (response), 'tokens' (usage info or None), 'error' (if any)
        """
        self._ensure_clients()

        # Add user message to history
        user_msg = {"role": "user", "content": text}
        if images:
            user_msg["images"] = images
        self.messages.append(user_msg)

        # Build system prompt if not yet built
        if not self.system_prompt:
            self.build_system_prompt()

        # Call the appropriate API
        try:
            result = await self._call_api(images)
        except Exception as e:
            logger.error(f"API call failed: {e}")
            error_text = f"Error calling {self.current_api} API: {str(e)}"
            return {'text': error_text, 'tokens': None, 'error': str(e)}

        if result and result.get('text'):
            # Process artifacts if present
            artifact_manager = self.context_state.get('artifact_manager')
            if artifact_manager and artifact_manager.should_create_artifact(text, result['text']):
                content, commentary = artifact_manager.extract_artifact_content(result['text'])
                if content:
                    artifact_id = artifact_manager.create_artifact(content)
                    logger.debug(f"Created artifact #{artifact_id}")

            # Add assistant response to history
            self.messages.append({"role": "assistant", "content": result['text']})

        return result

    async def _call_api(self, images: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Dispatch to the correct API based on current_api setting."""
        import asyncio

        if self.current_api == "anthropic" and self._anthropic_client:
            return await self._call_anthropic(images)
        elif self.current_api == "openai" and self._openai_client:
            return await self._call_openai(images)
        elif self.current_api == "gemini" and self._gemini_client:
            return await self._call_gemini(images)
        elif self.current_api == "llama" and self._llama_client:
            return await self._call_llama(images)
        else:
            return {
                'text': f"No {self.current_api} client available. Check your API keys.",
                'tokens': None,
                'error': 'no_client',
            }

    async def _call_anthropic(self, images: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Call Anthropic API."""
        import asyncio
        from promaia.ai.models import ANTHROPIC_MODELS

        # Determine model
        selected = os.getenv("SELECTED_MODEL_ID")
        if selected and "claude" in selected.lower():
            model = selected
        else:
            model = ANTHROPIC_MODELS.get("sonnet", "claude-sonnet-4-5")

        # Build clean messages
        clean_messages = []
        for msg in self.messages:
            clean_messages.append({"role": msg["role"], "content": msg["content"]})

        # Make the API call in a thread to avoid blocking
        def _do_call():
            return self._anthropic_client.messages.create(
                model=model,
                system=self.system_prompt,
                messages=clean_messages,
                max_tokens=4096,
                temperature=self.current_temperature,
            )

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _do_call)

        if response and response.content:
            response_text = response.content[0].text

            # Extract token usage
            tokens = None
            if hasattr(response, 'usage'):
                try:
                    from promaia.utils.ai import calculate_ai_cost
                    cost_data = calculate_ai_cost(
                        response.usage.input_tokens,
                        response.usage.output_tokens,
                        model,
                    )
                    tokens = {
                        'prompt_tokens': response.usage.input_tokens,
                        'response_tokens': response.usage.output_tokens,
                        'total_tokens': response.usage.input_tokens + response.usage.output_tokens,
                        'cost': cost_data.get("total_cost", 0),
                        'model': self.get_model_name(),
                    }
                except Exception:
                    tokens = {
                        'prompt_tokens': response.usage.input_tokens,
                        'response_tokens': response.usage.output_tokens,
                        'total_tokens': response.usage.input_tokens + response.usage.output_tokens,
                        'cost': 0,
                        'model': self.get_model_name(),
                    }

            return {'text': response_text, 'tokens': tokens}

        return {'text': "No response from Anthropic API.", 'tokens': None, 'error': 'empty_response'}

    async def _call_openai(self, images: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Call OpenAI API."""
        import asyncio

        formatted_messages = [{"role": "system", "content": self.system_prompt}]
        for msg in self.messages:
            formatted_messages.append({"role": msg["role"], "content": msg["content"]})

        selected = os.getenv("SELECTED_MODEL_ID")
        model = selected if (selected and "gpt" in selected.lower()) else "gpt-4o"

        def _do_call():
            return self._openai_client.chat.completions.create(
                model=model,
                messages=formatted_messages,
                max_tokens=4096,
                temperature=self.current_temperature,
            )

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _do_call)

        if response and response.choices:
            response_text = response.choices[0].message.content
            tokens = None
            if hasattr(response, 'usage') and response.usage:
                try:
                    from promaia.utils.ai import calculate_ai_cost
                    cost_data = calculate_ai_cost(
                        response.usage.prompt_tokens,
                        response.usage.completion_tokens,
                        model,
                    )
                    tokens = {
                        'prompt_tokens': response.usage.prompt_tokens,
                        'response_tokens': response.usage.completion_tokens,
                        'total_tokens': response.usage.total_tokens,
                        'cost': cost_data.get("total_cost", 0),
                        'model': self.get_model_name(),
                    }
                except Exception:
                    tokens = {
                        'prompt_tokens': response.usage.prompt_tokens,
                        'response_tokens': response.usage.completion_tokens,
                        'total_tokens': response.usage.total_tokens,
                        'cost': 0,
                        'model': self.get_model_name(),
                    }
            return {'text': response_text, 'tokens': tokens}

        return {'text': "No response from OpenAI API.", 'tokens': None, 'error': 'empty_response'}

    async def _call_gemini(self, images: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Call Gemini API."""
        import asyncio

        formatted_prompt = f"System: {self.system_prompt}\n\nConversation:\n"
        for msg in self.messages:
            formatted_prompt += f"{msg['role'].title()}: {msg['content']}\n"

        def _do_call():
            return self._gemini_client.generate_content(formatted_prompt)

        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(None, _do_call)
            if response.text:
                return {'text': response.text, 'tokens': None}
            return {'text': "No response from Gemini API.", 'tokens': None, 'error': 'empty_response'}
        except Exception as e:
            return {'text': f"Gemini error: {str(e)}", 'tokens': None, 'error': str(e)}

    async def _call_llama(self, images: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Call local Llama API."""
        import asyncio

        formatted_messages = [{"role": "system", "content": self.system_prompt}]
        for msg in self.messages:
            formatted_messages.append({"role": msg["role"], "content": msg["content"]})

        model_name = os.getenv("LLAMA_DEFAULT_MODEL", "llama3:latest")

        def _do_call():
            return self._llama_client.chat.completions.create(
                model=model_name,
                messages=formatted_messages,
                max_tokens=4096,
                temperature=self.current_temperature,
            )

        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(None, _do_call)
            if response and response.choices:
                return {'text': response.choices[0].message.content, 'tokens': None}
            return {'text': "No response from Llama.", 'tokens': None, 'error': 'empty_response'}
        except Exception as e:
            return {'text': f"Llama error: {str(e)}", 'tokens': None, 'error': str(e)}

    def switch_model(self, target: Optional[str] = None) -> Optional[str]:
        """
        Switch to a different AI model.

        Args:
            target: Model name shorthand (e.g. "opus", "gpt", "gemini", "llama")

        Returns:
            New model display name if switched, None if failed
        """
        import json
        from promaia.ai.models import get_model_display_name, ANTHROPIC_MODELS, GOOGLE_MODELS

        self._ensure_clients()

        model_map = {
            "claude": "anthropic", "anthropic": "anthropic",
            "opus": "anthropic", "sonnet": "anthropic",
            "gpt": "openai", "openai": "openai",
            "gemini": "gemini", "google": "gemini",
            "flash": "gemini", "pro": "gemini",
            "llama": "llama", "local": "llama",
        }

        if not target:
            return None

        target_lower = target.lower().strip()
        new_api = model_map.get(target_lower)
        if not new_api:
            return None

        # Verify client exists
        client_map = {
            "anthropic": self._anthropic_client,
            "openai": self._openai_client,
            "gemini": self._gemini_client,
            "llama": self._llama_client,
        }
        if not client_map.get(new_api):
            return None

        # Find model ID for the new API
        if new_api == "anthropic":
            # Check if target specifies a specific model
            if target_lower == "opus":
                model_id = ANTHROPIC_MODELS.get("opus", "claude-opus-4-5")
            else:
                model_id = ANTHROPIC_MODELS.get("sonnet", "claude-sonnet-4-5")
        elif new_api == "openai":
            model_id = "gpt-4o"
        elif new_api == "gemini":
            if target_lower in ("flash", "gemini"):
                model_id = GOOGLE_MODELS.get("flash", "gemini-3-flash-preview")
            else:
                model_id = GOOGLE_MODELS.get("pro", "gemini-3-pro-preview")
        elif new_api == "llama":
            model_id = os.getenv("LLAMA_DEFAULT_MODEL", "llama3:latest")
        else:
            return None

        self.current_api = new_api
        self.current_model_id = model_id
        os.environ["API_TYPE"] = new_api
        os.environ["SELECTED_MODEL_ID"] = model_id

        # Save preference
        try:
            pref_file = os.path.join(os.path.expanduser("~"), ".maia_api_preference")
            with open(pref_file, 'w') as f:
                json.dump({'api_type': new_api, 'model_id': model_id}, f)
        except Exception:
            pass

        # Rebuild system prompt with new model context
        self.build_system_prompt()

        return self.get_model_name()

    def set_temperature(self, temp: float) -> bool:
        """Set the temperature. Returns True if valid."""
        if 0.0 <= temp <= 2.0:
            self.current_temperature = temp
            return True
        return False

    def get_temperature_label(self) -> str:
        """Get a human-readable label for the current temperature."""
        t = self.current_temperature
        if t < 0.3:
            return "very focused"
        elif t < 0.6:
            return "focused"
        elif t < 1.0:
            return "balanced"
        elif t < 1.5:
            return "creative"
        return "very creative"

    def clear_context(self):
        """Clear all loaded context data."""
        self.combined_multi_source_data = {}
        self.context_state['sources'] = []
        self.context_state['browse_selections'] = []
        self.context_state['total_pages_loaded'] = 0
        self.context_state['initial_multi_source_data'] = {}
        self.build_system_prompt()

    def clear_messages(self):
        """Clear conversation history."""
        self.messages = []

    def mute_context(self) -> bool:
        """Mute context (hide from AI but preserve). Returns True if newly muted."""
        if self.context_state.get('context_muted'):
            return False
        self.context_state['context_muted'] = True
        self.context_state['muted_sources'] = self.context_state.get('sources', []).copy()
        self.context_state['muted_data'] = self.combined_multi_source_data.copy()
        self.build_system_prompt()
        return True

    def unmute_context(self) -> bool:
        """Unmute context (restore hidden data). Returns True if newly unmuted."""
        if not self.context_state.get('context_muted'):
            return False
        self.context_state['context_muted'] = False
        self.context_state['sources'] = self.context_state.get('muted_sources', [])
        self.combined_multi_source_data = self.context_state.get('muted_data', {})
        self.context_state['total_pages_loaded'] = sum(
            len(pages) for pages in self.combined_multi_source_data.values() if pages is not None
        )
        self.build_system_prompt()
        return True

    def save_conversation(self, custom_name: Optional[str] = None) -> Optional[str]:
        """
        Save the current conversation to history.

        Args:
            custom_name: Optional custom thread name

        Returns:
            Thread name if saved, None if nothing to save
        """
        if not self.messages:
            return None

        try:
            from promaia.storage.chat_history import ChatHistoryManager
            history_manager = ChatHistoryManager()

            thread_context = {
                'sources': self.context_state.get('sources'),
                'filters': self.context_state.get('filters'),
                'workspace': self.context_state.get('workspace'),
            }

            thread_name = custom_name or history_manager._generate_thread_name(self.messages)
            history_manager.save_thread(self.messages, thread_context, thread_name)
            return thread_name
        except Exception as e:
            logger.error(f"Failed to save conversation: {e}")
            return None

    def get_available_apis(self) -> List[str]:
        """Get list of available API names."""
        self._ensure_clients()
        apis = []
        if self._anthropic_client:
            apis.append("anthropic")
        if self._openai_client:
            apis.append("openai")
        if self._gemini_client:
            apis.append("gemini")
        if self._llama_client:
            apis.append("llama")
        return apis
