from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Tuple
import logging
import os

# AI model clients
from anthropic import Anthropic
from openai import OpenAI
from google import genai

# Maia specific imports
from promaia.storage.unified_reader import read_database_content
from promaia.ai.prompts import create_system_prompt
from promaia.ai.models import ANTHROPIC_MODELS, GOOGLE_MODELS
from promaia.config.workspaces import get_workspace_manager
from promaia.config.databases import get_database_manager

# Load environment variables for API keys
from promaia.utils.config import load_environment
load_environment()

router = APIRouter()
logger = logging.getLogger(__name__)

# --- Pydantic Models ---

class Node(BaseModel):
    id: str
    type: str
    data: Dict[str, Any]
    position: Dict[str, float]

class Edge(BaseModel):
    id: str
    source: str
    target: str

class WorkflowRequest(BaseModel):
    nodes: List[Node]
    edges: List[Edge]
    message: str

class WorkflowResponse(BaseModel):
    success: bool
    output: Optional[str] = None
    error: Optional[str] = None
    sources_summary: Optional[Dict[str, int]] = None

# --- AI Client Initialization ---

anthropic_client = None
if os.getenv("ANTHROPIC_API_KEY"):
    anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

openai_client = None
if os.getenv("OPENAI_API_KEY"):
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

gemini_client = None
if os.getenv("GOOGLE_API_KEY"):
    gemini_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

# Local Llama client (using OpenAI-compatible endpoint)
llama_client = None
llama_base_url = os.getenv("LLAMA_BASE_URL", "http://localhost:11434")
if llama_base_url:
    try:
        import requests
        test_url = f"{llama_base_url.rstrip('/')}/api/tags" if "ollama" in llama_base_url or ":11434" in llama_base_url else f"{llama_base_url.rstrip('/')}/v1/models"
        response = requests.get(test_url, timeout=2)
        if response.status_code == 200:
            llama_client = OpenAI(
                base_url=f"{llama_base_url.rstrip('/')}/v1",
                api_key=os.getenv("LLAMA_API_KEY", "local-llama")
            )
            logger.info(f"Local Llama client initialized at {llama_base_url}")
        else:
            logger.warning(f"Local Llama server not responding at {llama_base_url}")
    except Exception as e:
        logger.warning(f"Could not connect to local Llama server: {e}")


# --- Helper Functions ---

def _parse_workflow(workflow: WorkflowRequest) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[Node]]:
    """
    Parses the workflow to extract connected data sources, workspace, and the AI model node.
    """
    adj: Dict[str, List[str]] = {}
    for edge in workflow.edges:
        if edge.target not in adj:
            adj[edge.target] = []
        adj[edge.target].append(edge.source)

    output_nodes = [node for node in workflow.nodes if node.type == 'chat']
    if not output_nodes:
        raise ValueError("Workflow must have a chat output node")
    
    chat_node = output_nodes[0]

    q = [chat_node.id]
    visited = {chat_node.id}
    
    sources = []
    model_node: Optional[Node] = None
    workspace: Optional[str] = None

    while q:
        current_id = q.pop(0)
        current_node = next((n for n in workflow.nodes if n.id == current_id), None)
        if not current_node:
            continue

        if 'workspace' in current_node.data and current_node.data['workspace']:
            workspace = current_node.data['workspace']

        if current_node.type in ['journal', 'cms', 'gmail']:
            source_info = {
                "name": current_node.data.get('database', current_node.type),
                "days": current_node.data.get('days', 30)
            }
            sources.append(source_info)
        
        elif current_node.type in ['gemini', 'claude', 'openai', 'llama']:
            model_node = current_node
        
        for parent_id in adj.get(current_id, []):
            if parent_id not in visited:
                q.append(parent_id)
                visited.add(parent_id)
                
    return sources, workspace, model_node

def _call_anthropic(system_prompt: str, user_message: str, model_data: Dict) -> str:
    """Call Anthropic API with given prompts and model configuration."""
    from anthropic import Anthropic
    from promaia.ai.models import ANTHROPIC_MODELS
    import os
    
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=ANTHROPIC_MODELS.get("sonnet", "claude-sonnet-4-6"),
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=model_data.get("max_tokens", 4000),
        temperature=model_data.get("temperature", 0.7)
    )
    return response.content[0].text

def _call_openai(system_prompt: str, user_message: str, model_data: Dict) -> str:
    """Calls the OpenAI API."""
    if not openai_client:
        raise ValueError("OpenAI client not initialized. Check OPENAI_API_KEY.")
        
    response = openai_client.chat.completions.create(
        model=model_data.get("model", "gpt-4-turbo"),
        max_tokens=model_data.get("max_tokens", 4096),
        temperature=model_data.get("temperature", 0.7),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
    )
    return response.choices[0].message.content

def _call_gemini(system_prompt: str, user_message: str, model_data: Dict) -> str:
    """Calls the Google Gemini API."""
    if not gemini_client:
        raise ValueError("Gemini client not initialized. Check GOOGLE_API_KEY.")

    generation_config = genai.types.GenerationConfig(
        max_output_tokens=model_data.get("max_tokens", 2048),
        temperature=model_data.get("temperature", 0.7),
    )
    
    from google.genai import types as genai_types
    response = gemini_client.models.generate_content(
        model=model_data.get("model", "gemini-1.5-pro-latest"),
        contents=user_message,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=generation_config.get("temperature", 0.7),
            max_output_tokens=generation_config.get("max_output_tokens", 4096),
        ),
    )
    return response.text

def _call_llama(system_prompt: str, user_message: str, model_data: Dict) -> str:
    """Calls the Local Llama API using OpenAI-compatible interface."""
    if not llama_client:
        raise ValueError("Local Llama client not initialized. Check LLAMA_BASE_URL.")
        
    response = llama_client.chat.completions.create(
        model=model_data.get("model", os.getenv("LLAMA_DEFAULT_MODEL", "llama3:latest")),
        max_tokens=model_data.get("max_tokens", 4096),
        temperature=model_data.get("temperature", 0.7),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
    )
    return response.choices[0].message.content


# --- API Endpoints ---

@router.post("/execute-workflow", response_model=WorkflowResponse)
async def execute_workflow(workflow: WorkflowRequest):
    """
    Executes a node-based workflow by fetching data from sources,
    building a system prompt, and querying the specified AI model.
    """
    try:
        sources, workspace, model_node = _parse_workflow(workflow)
        user_message = workflow.message

        if not sources:
            raise ValueError("Workflow must have at least one data source connected to the chat output.")
        if not model_node:
            raise ValueError("Workflow must have exactly one AI model node connected to the chat output.")

        # Set workspace if not explicitly defined
        if not workspace:
            workspace = get_workspace_manager().get_default_workspace()
            if not workspace:
                raise ValueError("No workspace specified and no default is configured.")

        # Load data from all connected sources
        all_pages: Dict[str, List[Dict[str, Any]]] = {}
        sources_summary: Dict[str, int] = {}
        
        for source in sources:
            source_name = source["name"]
            days = source["days"]
            try:
                pages = read_database_content(
                    database_name=source_name,
                    days=days,
                    target_data_source=workspace  # Pass workspace here
                )
                all_pages[source_name] = pages
                sources_summary[source_name] = len(pages)
                logger.info(f"Read {len(pages)} pages from database '{source_name}' for workspace '{workspace}'")
            except Exception as e:
                logger.error(f"Failed to read from source {source_name}: {e}", exc_info=True)
                sources_summary[source_name] = 0

        # Create system prompt
        system_prompt = create_system_prompt(
            multi_source_data=all_pages
        )

        # Call the appropriate AI model
        ai_response: str = ""
        model_type = model_node.type
        model_data = model_node.data
        
        if model_type == 'claude':
            ai_response = _call_anthropic(system_prompt, user_message, model_data)
        elif model_type == 'openai':
            ai_response = _call_openai(system_prompt, user_message, model_data)
        elif model_type == 'gemini':
            ai_response = _call_gemini(system_prompt, user_message, model_data)
        elif model_type == 'llama':
            ai_response = _call_llama(system_prompt, user_message, model_data)
        else:
            raise ValueError(f"Unsupported AI model type: '{model_type}'")

        return WorkflowResponse(
            success=True,
            output=ai_response,
            sources_summary=sources_summary
        )

    except Exception as e:
        logger.error(f"Error executing workflow: {e}", exc_info=True)
        return WorkflowResponse(
            success=False,
            error=str(e)
        )


@router.get("/databases")
async def list_databases():
    """Returns a list of available databases."""
    try:
        db_manager = get_database_manager()
        return db_manager.get_all_databases()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/workspaces")
async def list_workspaces():
    """Returns a list of available workspaces."""
    try:
        workspace_manager = get_workspace_manager()
        return workspace_manager.get_all_workspaces()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/models")
async def list_models():
    """Returns a list of available AI models."""
    return {
        "anthropic": ANTHROPIC_MODELS,
        "google": GOOGLE_MODELS,
        "openai": ["gpt-4-turbo", "gpt-3.5-turbo"] # Example
    }

@router.post("/validate-workflow")
async def validate_workflow(workflow: WorkflowRequest):
    """Validates the workflow without executing it."""
    try:
        sources, workspace, model_node = _parse_workflow(workflow)
        if not sources:
            return {"valid": False, "message": "No data source found."}
        if not model_node:
            return {"valid": False, "message": "No AI model found."}
        return {
            "valid": True, 
            "message": "Workflow is valid.",
            "details": {
                "sources": sources,
                "workspace": workspace or "default",
                "model": model_node.type
            }
        }
    except Exception as e:
        return {"valid": False, "message": str(e)} 