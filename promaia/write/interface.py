"""
Terminal-based interface for generating blog posts from journal entries.
"""
import os
import glob
import sys
import json
import asyncio
from datetime import datetime, timedelta
from rich.console import Console


from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.formatted_text import HTML

from promaia.storage.files import read_markdown_files, get_existing_page_ids
from promaia.utils.config import get_chat_days_setting, set_chat_days_setting, load_environment
from promaia.chat.interface import get_api_preference, create_system_prompt, display_message_with_timestamp
from promaia.nlq.models import ANTHROPIC_MODELS
from promaia.notion.client import notion_client
from promaia.notion.pages import get_sync_pages, get_pages_by_properties, get_page_title, get_block_content, clear_block_cache
from promaia.markdown.converter import page_to_markdown
from promaia.storage.files import save_page_to_file
from promaia.utils.config_loader import get_notion_database_id
from promaia.nlq.models import GOOGLE_MODELS, LLAMA_MODELS
from promaia.utils.display import print_text, print_markdown, print_separator

# Load environment variables
load_environment()

# Initialize console for rich output
console = Console(width=9999, soft_wrap=False)

# Create a prompt session with history
from promaia.utils.env_writer import get_data_dir as _get_data_dir
session = PromptSession(history=FileHistory(str(_get_data_dir() / ".maia_write_history")))

# Create drafts directory if it doesn't exist
DRAFTS_DIR = str(_get_data_dir() / "drafts")
os.makedirs(DRAFTS_DIR, exist_ok=True)

DEBUG_MODE = os.environ.get("MAIA_DEBUG") == "1"

def debug_print(message):
    """Print debug messages if debug mode is enabled."""
    if DEBUG_MODE:
        print_text(f"DEBUG: {message}", style="dim")

def create_blog_system_prompt(journal_pages, webflow_pages, custom_prompt, for_api="anthropic", max_entries=None):
    """
    Create a system prompt for blog post generation.
    
    Args:
        journal_pages: List of journal page data
        webflow_pages: List of webflow page data
        custom_prompt: User's custom prompt for the blog post
        for_api: Which API to format for ("anthropic" or "openai")
        max_entries: Maximum number of journal entries to include (None means unlimited)
        
    Returns:
        Formatted system prompt
    """
    # Get today's date
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    
    # Limit the number of journal entries if max_entries is specified
    if max_entries is not None:
        limited_journal_pages = journal_pages[:max_entries]
        if len(journal_pages) > max_entries:
            print_text(f"DEBUG: Limited journal entries from {len(journal_pages)} to {max_entries} for prompt", style="dim")
    else:
        # Use all journal entries if max_entries is None
        limited_journal_pages = journal_pages
    
    # Base system prompt
    base_prompt = f"""You are me. These are my journals. These are my blog posts. Your task is to turn the content of the journal entries into a blog post in the style of my previous entries.

Today's date is {today_str}.

General instructions for writing good content on my behalf:
1. Have a compelling title
2. Include an engaging introduction
3. Have a clear structure with headings
4. Be well-written and engaging
5. End with a strong conclusion

My prompt for this particular blog post:
{custom_prompt}

---

---

Here are the journal entries to use as source material for the blog post:
"""

    # Add journal entries as source material
    if for_api == "anthropic":
        # Use limited journal pages for Claude
        for page in limited_journal_pages:
            base_prompt += f"\nJournal Entry: {page['filename']}\n{page['content']}\n---\n"
    elif for_api == "openai":  # Explicitly check for OpenAI
        # OpenAI has a more limited context window
        openai_max = 3
        used_entries = limited_journal_pages[:openai_max]
        if len(limited_journal_pages) > openai_max:
            print_text(f"NOTICE: Using OpenAI API which has a smaller context window. Limiting to {openai_max} journal entries.", style="dim")
            
        for i, page in enumerate(used_entries):
            # Further truncate content if needed
            content = page['content']
            if len(content) > 2000:  # Arbitrary limit to prevent token overflows
                content = content[:2000] + "... (content truncated)"
            base_prompt += f"\nJournal Entry: {page['filename']}\n{content}\n---\n"
    elif for_api == "gemini": # Explicitly check for Gemini
        # Gemini 1.5 Pro has a large context window, use limited_journal_pages directly
        # (which respects the --max-entries flag if provided)
        
        # Start with core instructions and custom prompt
        prompt_parts = [
            f"""You are me. Write in my voice.
Today's date is {today_str}.

My specific instructions for *this* blog post are:
{custom_prompt}

--- GENERAL BLOG WRITING GUIDELINES ---
1. Compelling title
2. Engaging introduction
3. Clear structure with headings
4. Well-written and engaging style (match reference posts)
5. Strong conclusion
6. **Important:** Use single spaces after periods, matching the style of the STYLE REFERENCE POSTS.
"""
        ]

        # Add Journal Entries section
        journal_section = "\n\n--- REFERENCE MATERIAL: JOURNAL ENTRIES ---\nUse the following journal entries for stylistic reference and additional context. Do NOT copy or reuse their content in the new post unless specifically directed to in 'My specific instructions'.\n"
        if limited_journal_pages:
            for page in limited_journal_pages:
                # Basic XML-like tag for clarity
                journal_section += f"\n<journal_entry filename=\"{page['filename']}\">\n{page['content']}\n</journal_entry>\n"
        else:
            # Adjusted message for potentially being intentionally excluded
            journal_section += "\nNo journal entries provided or requested for reference.\n"
        prompt_parts.append(journal_section)

        # Add Reference Blog Posts section
        reference_section = "\n\n--- STYLE REFERENCE: PREVIOUS BLOG POSTS ---\nUse the following blog posts for stylistic reference and additional context. Do NOT copy or reuse their content in the new post unless specifically directed to in 'My specific instructions'.\n"
        if webflow_pages:
            for page in webflow_pages:
                # Basic XML-like tag for clarity
                reference_section += f"\n<reference_post filename=\"{page['filename']}\">\n{page['content']}\n</reference_post>\n"
        else:
            reference_section += "\nNo previous blog posts available for style reference.\n"
        prompt_parts.append(reference_section)

        # Add final instruction
        prompt_parts.append(
            """\n\n--- FINAL INSTRUCTION ---\nGenerate the complete blog post in Markdown format based on 'My specific instructions', using the REFERENCE MATERIAL and STYLE REFERENCE sections for context and style guidance only, unless explicitly told otherwise in the instructions."""
        )
        
        # Join the parts into the final prompt string for Gemini
        base_prompt = "\n".join(prompt_parts)
    elif for_api == "llama": # Local Llama models - similar to Gemini approach
        # Local Llama models typically have good context windows, use limited_journal_pages directly
        # (which respects the --max-entries flag if provided)
        
        # Start with core instructions and custom prompt
        prompt_parts = [
            f"""You are me. Write in my voice.
Today's date is {today_str}.

My specific instructions for *this* blog post are:
{custom_prompt}

--- GENERAL BLOG WRITING GUIDELINES ---
1. Compelling title
2. Engaging introduction
3. Clear structure with headings
4. Well-written and engaging style (match reference posts)
5. Strong conclusion
6. **Important:** Use single spaces after periods, matching the style of the STYLE REFERENCE POSTS.
"""
        ]

        # Add Journal Entries section
        journal_section = "\n\n--- REFERENCE MATERIAL: JOURNAL ENTRIES ---\nUse the following journal entries for stylistic reference and additional context. Do NOT copy or reuse their content in the new post unless specifically directed to in 'My specific instructions'.\n"
        if limited_journal_pages:
            for page in limited_journal_pages:
                # Basic XML-like tag for clarity
                journal_section += f"\n<journal_entry filename=\"{page['filename']}\">\n{page['content']}\n</journal_entry>\n"
        else:
            # Adjusted message for potentially being intentionally excluded
            journal_section += "\nNo journal entries provided or requested for reference.\n"
        prompt_parts.append(journal_section)

        # Add Reference Blog Posts section
        reference_section = "\n\n--- STYLE REFERENCE: PREVIOUS BLOG POSTS ---\nUse the following blog posts for stylistic reference and additional context. Do NOT copy or reuse their content in the new post unless specifically directed to in 'My specific instructions'.\n"
        if webflow_pages:
            for page in webflow_pages:
                # Basic XML-like tag for clarity
                reference_section += f"\n<reference_post filename=\"{page['filename']}\">\n{page['content']}\n</reference_post>\n"
        else:
            reference_section += "\nNo previous blog posts available for style reference.\n"
        prompt_parts.append(reference_section)

        # Add final instruction
        prompt_parts.append(
            """\n\n--- FINAL INSTRUCTION ---\nGenerate the complete blog post in Markdown format based on 'My specific instructions', using the REFERENCE MATERIAL and STYLE REFERENCE sections for context and style guidance only, unless explicitly told otherwise in the instructions."""
        )
        
        # Join the parts into the final prompt string for Local Llama
        base_prompt = "\n".join(prompt_parts)
    else: # Fallback for any unknown API type
        print_text(f"WARNING: Unknown API type '{for_api}' for prompt generation. Using generic entry handling.", style="dim")
        for page in limited_journal_pages:
            base_prompt += f"\nJournal Entry: {page['filename']}\n{page['content']}\n---\n"
    
    # Add separator between sections
    base_prompt += "\n---\n---\n\n"
    
    # Add webflow pages as style guides
    base_prompt += "Here are previous blog posts for style reference only, don't take content from these:\n"
    
    if webflow_pages:
        for page in webflow_pages:
            base_prompt += f"\nBlog Post: {page['filename']}\n{page['content']}\n---\n"
    else:
        base_prompt += "\nNo previous blog posts available for style reference.\n"
    
    # Add final separator and instruction
    base_prompt += "\n---\n---\n\nPlease write a complete blog post based on the above information. Format it in Markdown."
    
    return base_prompt

def save_blog_post(content):
    """
    Save the generated blog post to the drafts directory.
    
    Args:
        content: The blog post content
        
    Returns:
        Path to the saved file
    """
    # Create a filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"blog_post_{timestamp}.md"
    filepath = os.path.join(DRAFTS_DIR, filename)
    
    # Save the content
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    
    return filepath

async def get_reference_webflow_page_ids():
    """
    Get Webflow entry page IDs from Notion where the "Reference" property is checked.

    Returns:
        List of Notion page IDs with Reference=True, or None if error.
    """
    # CMS database removed - return None to fall back to all webflow pages
    return None

def filter_webflow_entries_by_page_ids(webflow_pages, page_ids):
    """
    Filter Webflow entries to only include those with IDs in the provided list.
    
    Args:
        webflow_pages: List of webflow page data from read_markdown_files
        page_ids: List of page IDs to include
        
    Returns:
        Filtered list of webflow pages
    """
    debug_print(f"[filter_webflow] Received page_ids from Notion for filtering: {page_ids}")

    if not page_ids: # Handles if page_ids is None (error) or empty list (no refs)
        # If page_ids is None (meaning an error fetching them), we might want to return all webflow_pages as a fallback.
        # If page_ids is an empty list [], it means no pages were explicitly marked, so returning [] is correct.
        # The current logic in write_blog_post handles the None case by falling back to all_webflow_pages.
        # So, if page_ids is an empty list here, returning [] is the intended behavior for "no specific references found".
        return [] # Return empty if no specific page IDs are provided for filtering.
        
    filtered_pages = []
    
    debug_print(f"[filter_webflow] Total local webflow_pages to iterate: {len(webflow_pages)}")

    for i, page_data in enumerate(webflow_pages):
        filename = page_data.get('filename', '')
        debug_print(f"[filter_webflow] Checking local file [{i+1}/{len(webflow_pages)}]: {filename}")
        
        import re
        # Use a more robust regex to find the UUID anywhere in the filename
        # This accounts for potential whitespace or other characters
        id_match = re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', filename)
        
        if id_match:
            page_id_from_file = id_match.group(1)
            debug_print(f"[filter_webflow]   Regex matched. Extracted ID: {page_id_from_file}")
            debug_print(f"[filter_webflow]     Type(page_id_from_file): {type(page_id_from_file)}")
            debug_print(f"[filter_webflow]     Repr(page_id_from_file): {repr(page_id_from_file)}")
            debug_print(f"[filter_webflow]     Type(page_ids): {type(page_ids)}")
            if page_ids:
                debug_print(f"[filter_webflow]       Type(page_ids[0]): {type(page_ids[0])}")
                debug_print(f"[filter_webflow]       Repr(page_ids[0]): {repr(page_ids[0])}")
                # Explicitly check equality
                is_equal = page_id_from_file == page_ids[0]
                debug_print(f"[filter_webflow]       Direct Equality Check (page_id_from_file == page_ids[0]): {is_equal}")
            if page_id_from_file in page_ids:
                debug_print(f"[filter_webflow]   MATCH FOUND! ID {page_id_from_file} is in reference list. Adding: {filename}")
                filtered_pages.append(page_data)
            else:
                debug_print(f"[filter_webflow]   ID {page_id_from_file} not in reference list {page_ids}.")
        else:
            debug_print(f"[filter_webflow]   Regex did not find ID pattern in filename.")
        
    debug_print(f"[filter_webflow] Returning {len(filtered_pages)} filtered pages.")
    return filtered_pages

async def write_blog_post(days=None, custom_prompt=None, push_to_notion=True, max_entries=None, force_openai=False, no_journal=False):
    """
    Generates a blog post and optionally pushes it to Notion.
    Handles API selection, prompt creation, and LLM interaction.
    
    Args:
        days: Number of days of journal entries to use (default: from config).
        custom_prompt: The user's custom prompt for the blog post.
        push_to_notion: Whether to push the generated blog post to Notion.
        max_entries: Maximum number of journal entries to include.
        force_openai: Whether to force using OpenAI even if another API is preferred.
        no_journal: If True, skips loading journal entries.

    Returns:
        The generated blog content as a string, or None if an error occurs.
    """
    # Needed imports moved inside or ensured accessible
    import os
    import json
    from datetime import datetime
    
    # Get API preference, but force OpenAI if requested
    api_type = "openai" if force_openai else get_api_preference()
    console.print(f"[info]Using {api_type.capitalize()} API[/info]")
    
    # === Ensure live posts are downloaded ===
    try:
        webflow_db_id = os.getenv("NOTION_WEBFLOW_DATABASE_ID")
        if webflow_db_id:
            console.print("[info]Checking for missing local 'Live' Webflow posts...[/info]")
            # Change asyncio.run to await
            await ensure_latest_live_posts_downloaded(webflow_db_id)
        else:
            console.print("[warning]NOTION_WEBFLOW_DATABASE_ID not set. Skipping check for missing live posts.[/warning]")
    except Exception as e:
        console.print(f"[error]Error during live post check/download: {e}[/error]")
        console.print("[warning]Proceeding with blog generation using potentially outdated local Webflow posts.[/warning]")
    # ==============================================

    # Load journal entries with the specified days parameter, or skip if no_journal is True
    journal_pages = []
    if not no_journal:
        journal_pages = read_markdown_files(days=days, content_type="journal")
        console.print(f"[info]Found {len(journal_pages)} journal entries from the past {days if days else 'all'} days for reference.[/info]")
        
        # Check if any journal entries were found (only if not skipping them)
        if len(journal_pages) == 0:
            console.print("[warning]No journal entries were found for the specified time period for reference.[/warning]")
            console.print("  1. No journal entries exist in the 'notion-journal' directory")
            console.print("  2. Journal entries exist but their dates are outside the specified time period")
            console.print("  3. Journal entries have date formatting issues in their filenames")
            console.print(f"  4. You might need to run 'maia journal pull --days {days}' first.")
            # No need to ask to continue if they are optional anyway
    else:
        console.print("[info]Skipping journal entries as reference material as requested.[/info]")
    
    # Load all webflow content
    all_webflow_pages = read_content_by_type("webflow")
    console.print(f"[info]Found {len(all_webflow_pages)} total local webflow entries[/info]")
    
    # Get reference Webflow page IDs from Notion
    webflow_pages = [] # Initialize webflow_pages
    try:
        # Change asyncio.run to await
        reference_page_ids = await get_reference_webflow_page_ids()
        
        if reference_page_ids is not None:
            webflow_pages = filter_webflow_entries_by_page_ids(all_webflow_pages, reference_page_ids)
            console.print(f"[info]Using {len(webflow_pages)} webflow entries marked for reference in Notion.[/info]")
        else:
            webflow_pages = all_webflow_pages
            console.print("[warning]Could not determine reference pages from Notion. Using all local webflow entries as style guides.[/warning]")
    except Exception as e:
        console.print(f"[error]Error filtering webflow entries by reference: {str(e)}[/error]")
        webflow_pages = all_webflow_pages
        console.print("[warning]Using all local webflow entries as style guides due to error.[/warning]")
    
    # If no custom prompt provided, prompt the user
    if not custom_prompt:
        console.print("\n[bold]Enter your custom instructions for the blog post:[/bold]")
        # Use prompt_async within the async function
        custom_prompt = await session.prompt_async(HTML('<style fg="green">You: </style>'))
        custom_prompt = custom_prompt.strip()
    
    # Create the system prompt with limited entries
    system_prompt = create_blog_system_prompt(journal_pages, webflow_pages, custom_prompt, api_type, max_entries)

    # Save the system prompt to a file without output
    debug_dir = str(_get_data_dir() / "debug")
    os.makedirs(debug_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_file = os.path.join(debug_dir, f"write_prompt_{timestamp}.txt")
    with open(debug_file, "w", encoding="utf-8") as f:
        f.write(f"API Type: {api_type}\n\n")
        f.write(f"System Prompt:\n{system_prompt}\n\n")
        f.write(f"User Message: Please write a blog post based on the journal entries and webflow content provided.")

    # Display a message about sending to AI
    console.print("\n[info]Generating blog post...[/info]")
    
    # Get AI response (API calls might need async versions if the libraries support it)
    # Assuming the current client libraries handle async implicitly or are sync for now.
    # If direct async API calls are needed, this section would require further changes.
    blog_content = None
    try:
        if api_type == "anthropic":
            # Anthropic sync client used here
            from anthropic import Anthropic
            client = Anthropic(base_url=os.environ.get("ANTHROPIC_BASE_URL"), max_retries=5)
            response = client.messages.create(
                model=ANTHROPIC_MODELS.get("sonnet", "claude-sonnet-4-6"),
                max_tokens=4000,
                temperature=0.7,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": "Please write a blog post based on the journal entries and webflow content provided."}
                ]
            )
            blog_content = response.content[0].text
            
            # Save the response to a file without output
            with open(os.path.join(debug_dir, f"write_response_{timestamp}.json"), "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "model": response.model,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "response_id": response.id,
                    "content": blog_content
                }, indent=2))
                
        elif api_type == "gemini":
            # Google sync client used here
            import google.generativeai as genai
            
            # Configure API Key
            google_api_key = os.getenv("GOOGLE_API_KEY")
            if not google_api_key:
                console.print("[error]GOOGLE_API_KEY environment variable not set.[/error]")
                return
            genai.configure(api_key=google_api_key)

            # Initialize Model
            from promaia.nlq.models import get_current_google_model
            model = genai.GenerativeModel(get_current_google_model())

            # Start chat session
            chat = model.start_chat(history=[])
            
            # Combine system prompt and main prompt for Gemini
            # The API expects a list of content parts
            gemini_contents = [
                system_prompt, # System instructions first
                "Please write a blog post based on the journal entries and webflow content provided." # Then the standard user request
            ]
            
            response = model.generate_content(
                # Pass combined prompts in contents
                contents=gemini_contents,
                generation_config={
                    "temperature": 0.7,
                    "max_output_tokens": 4000
                }
            )
            blog_content = response.text
            # Save the response to a file without output
            with open(os.path.join(debug_dir, f"write_response_{timestamp}.json"), "w", encoding="utf-8") as f:
                from promaia.nlq.models import get_current_google_model
                f.write(json.dumps({
                    "model": get_current_google_model(),
                    "content": blog_content
                }, indent=2))
        elif api_type == "llama":
            # Local Llama client using OpenAI-compatible interface
            from openai import OpenAI
            
            # Configure Local Llama client
            llama_base_url = os.getenv("LLAMA_BASE_URL", "http://localhost:11434")
            llama_api_key = os.getenv("LLAMA_API_KEY", "local-llama")
            
            if not llama_base_url:
                console.print("[error]LLAMA_BASE_URL environment variable not set.[/error]")
                return
            
            try:
                # Test if local Llama server is available
                import requests
                test_url = f"{llama_base_url.rstrip('/')}/api/tags" if "ollama" in llama_base_url or ":11434" in llama_base_url else f"{llama_base_url.rstrip('/')}/v1/models"
                response = requests.get(test_url, timeout=2)
                if response.status_code != 200:
                    console.print(f"[error]Local Llama server not responding at {llama_base_url}[/error]")
                    return
            except Exception as e:
                console.print(f"[error]Could not connect to local Llama server: {e}[/error]")
                return
            
            client = OpenAI(
                base_url=f"{llama_base_url.rstrip('/')}/v1",
                api_key=llama_api_key
            )
            
            model_name = os.getenv("LLAMA_DEFAULT_MODEL", LLAMA_MODELS.get("llama3", "llama3:latest"))
            
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "Please write a blog post based on the journal entries and content provided."}
                ],
                max_tokens=4000,
                temperature=0.7
            )
            
            blog_content = response.choices[0].message.content
            
            # Save the response to a file without output
            with open(os.path.join(debug_dir, f"write_response_{timestamp}.json"), "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "model": model_name,
                    "content": blog_content
                }, indent=2))
        else:  # OpenAI
            # OpenAI sync client used here
            from openai import OpenAI
            client = OpenAI()
            response = client.chat.completions.create(
                model="gpt-4o",  # default OpenAI model for blog generation
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "Please write a blog post based on the journal entries and webflow content provided."}
                ],
                temperature=0.7,
                max_tokens=4000
            )
            blog_content = response.choices[0].message.content
            
            # Save the response to a file without output
            with open(os.path.join(debug_dir, f"write_response_{timestamp}.json"), "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "model": response.model,
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                    "content": blog_content
                }, indent=2))
                
    except Exception as e:
        console.print(f"[error]Error with {api_type} API: {str(e)}[/error]")
        # Consider re-raising or handling more gracefully in async context
        raise
    
    if not blog_content:
        console.print("[error]Failed to generate blog content[/error]")
        return None
        
    # Display the blog post using copy-friendly markdown rendering
    print_markdown(blog_content, title="Generated Blog Post")
    
    # Push to Notion if requested (Notion client calls are async)
    if push_to_notion:
        from datetime import datetime
        import re
        import os
        
        console.print("\n[info]Pushing blog post to Notion...[/info]")
        
        # Get the database ID
        try:
            database_id = get_notion_database_id("webflow")
        except (FileNotFoundError, ValueError) as e:
            error_message = f"Error loading Notion database ID for 'webflow' for push: {e}"
            console.print(f"[warning]{error_message}[/warning]")
            # Save locally as a fallback if Notion push prep fails
            if not 'saved_filepath' in locals(): # If generation failed before save
                saved_filepath = save_blog_post(blog_content)
                console.print(f"[info]Blog post saved locally as a fallback to: {saved_filepath}[/info]")
            return blog_content # Return content even if Notion push fails

        if not database_id:
            console.print("[warning]No database ID found for 'webflow'. Please configure in notion_config.json.[/warning]")
            # Save locally as a fallback
            if not 'saved_filepath' in locals(): # If generation failed before save
                saved_filepath = save_blog_post(blog_content)
                console.print(f"[info]Blog post saved locally as a fallback to: {saved_filepath}[/info]")
            return blog_content
            
        # Extract title from the content (assuming the first line is a markdown title)
        title_match = re.search(r'^#\s+(.+)$', blog_content, re.MULTILINE)
        if title_match:
            title = title_match.group(1).strip()
        else:
            # Use a default title if no title found
            title = f"Blog Post {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        try:
            # Import requests for direct HTTP calls
            import requests
            import json
            
            # Get Notion credentials via auth module
            from promaia.auth import get_integration
            notion_token = get_integration("notion").get_notion_credentials()
            if not notion_token:
                console.print("[warning]No Notion token found. Run 'maia auth configure notion' to set up authentication.[/warning]")
                raise ValueError("No Notion credentials found. Run 'maia auth configure notion'.")
            
            # Convert markdown to simplified blocks for Notion
            blocks = []
            lines = blog_content.split('\n')
            i = 0
            
            while i < len(lines):
                line = lines[i].strip()
                
                # Skip empty lines
                if not line:
                    i += 1
                    continue
                
                # Headers
                if line.startswith('# '):
                    blocks.append({
                        "object": "block",
                        "type": "heading_1",
                        "heading_1": {
                            "rich_text": [{"type": "text", "text": {"content": line[2:]}}]
                        }
                    })
                elif line.startswith('## '):
                    blocks.append({
                        "object": "block",
                        "type": "heading_2",
                        "heading_2": {
                            "rich_text": [{"type": "text", "text": {"content": line[3:]}}]
                        }
                    })
                elif line.startswith('### '):
                    blocks.append({
                        "object": "block",
                        "type": "heading_3",
                        "heading_3": {
                            "rich_text": [{"type": "text", "text": {"content": line[4:]}}]
                        }
                    })
                
                # Lists
                elif line.startswith('- ') or line.startswith('* '):
                    blocks.append({
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {
                            "rich_text": [{"type": "text", "text": {"content": line[2:]}}]
                        }
                    })
                
                # Numbered lists
                elif re.match(r'^\d+\.', line):
                    content = re.sub(r'^\d+\.\s*', '', line)
                    blocks.append({
                        "object": "block",
                        "type": "numbered_list_item",
                        "numbered_list_item": {
                            "rich_text": [{"type": "text", "text": {"content": content}}]
                        }
                    })
                
                # Code blocks (simplifying for now)
                elif line.startswith('```'):
                    # Find the end of the code block
                    code_content = []
                    i += 1  # Skip the ``` line
                    language = line[3:].strip() or "plain_text"
                    
                    while i < len(lines) and not lines[i].strip().startswith('```'):
                        code_content.append(lines[i])
                        i += 1
                    
                    # Create code block
                    blocks.append({
                        "object": "block",
                        "type": "code",
                        "code": {
                            "language": language.lower(),
                            "rich_text": [{"type": "text", "text": {"content": "\n".join(code_content)}}]
                        }
                    })
                
                # Blockquotes
                elif line.startswith('> '):
                    blocks.append({
                        "object": "block",
                        "type": "quote",
                        "quote": {
                            "rich_text": [{"type": "text", "text": {"content": line[2:]}}]
                        }
                    })
                
                # Regular paragraphs (default)
                else:
                    blocks.append({
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": line}}]
                        }
                    })
                
                i += 1
            
            # Create Notion page data
            payload = {
                "parent": {"database_id": database_id},
                "properties": {
                    # --- Required & Existing ---
                    "Name": { # Title (Aa)
                        "title": [ { "text": { "content": title } } ]
                    },
                    "Blog Status": { # Status (Sun icon)
                        "status": { "name": "Don't sync" } # Default to Don't sync
                    },
                    "Publish Date": { # Date (Calendar icon)
                        "date": { "start": datetime.now().strftime("%Y-%m-%d") }
                    },
                     "Read Time Estimate": { # Text (Paragraph icon)
                        "rich_text": [ { "text": { "content": f"{len(blog_content.split()) // 200} min read" } } ]
                    },
                    "Author Name": { # Text (Paragraph icon)
                        "rich_text": [ { "text": { "content": None } } ] # Default Author
                    },
                    "Tag": { # Select (Tag icon)
                        "select": { "name": "AI" } # Default Tag
                    },

                    # --- New Properties based on Screenshot ---
                    "Reference": { # Checkbox (Checkmark icon)
                        "checkbox": False # Default to unchecked
                    },
                     "Newsletter Last Synced": { # Date (Calendar icon) -> Expected rich_text by Notion
                         "rich_text": [] # Default to empty rich_text if no date, or format date as string if provided
                     },
                     "Description": { # Text (Paragraph icon)
                         "rich_text": [ { "text": { "content": "" } } ] # Default empty
                     },
                     "Slug": { # Text (Paragraph icon)
                         "rich_text": [ { "text": { "content": "" } } ] # Default empty
                     },
                     "Emoji Tags": { # Text (Paragraph icon)
                         "rich_text": [ { "text": { "content": "" } } ] # Default empty
                     },
                     "image alt text": { # Text (Paragraph icon)
                         "rich_text": [ { "text": { "content": "" } } ] # Default empty
                     },
                     "MailerLite Campaign ID": { # Text (Paragraph icon)
                         "rich_text": [ { "text": { "content": "" } } ] # Default empty
                     },
                     "Author Photo": { # URL (Link icon) -> Expected files by Notion
                         "files": [] # Default to empty array for files property
                     },
                     "Thumbnail Image": { # URL (Link icon) -> Expected files by Notion
                         "files": [] # Default to empty array for files property
                     },
                     "Featured": { # Checkbox (Pin icon)
                         "checkbox": False # Default to unchecked
                     }
                     # "Webflow ID" is likely populated by sync, not creation
                     # "Newsletter Status" seems replaced by "Blog Status" based on prior work
                     # "Created time" is automatic

                    # --- REMOVED ---
                    # "Sync" checkbox was removed in previous steps
                },
                "children": blocks
            }

            # Use await for the Notion client call
            # Assuming notion_client methods like pages.create are async
            # Need to verify notion_client usage (looks like it is async already)
            # Direct requests.post call needs to be replaced with async HTTP client if used?
            # Checking the code again - it uses requests.post, which is synchronous!
            # This will block the event loop. We should use an async HTTP client like httpx or aiohttp.
            # For now, let's leave it and see if it works, but flag for potential refactor.
            import requests # TEMPORARY - Should replace with async client
            import json # Make sure json is imported
            from promaia.auth import get_integration
            notion_token = get_integration("notion").get_notion_credentials()
            if not notion_token:
                console.print("[warning]No Notion token found. Run 'maia auth configure notion' to set up authentication.[/warning]")
                raise ValueError("No Notion credentials found. Run 'maia auth configure notion'.")

            headers = {
                "Authorization": f"Bearer {notion_token}",
                "Content-Type": "application/json",
                "Notion-Version": "2022-06-28" # Ensure Notion version is specified
            }

            # --- DEBUGGING START ---
            debug_print(f"Type of headers: {type(headers)}")
            debug_print(f"Type of payload: {type(payload)}")
            # Print the payload itself - might be large
            try:
                import pprint
                import io
                string_stream = io.StringIO()
                pprint.pprint(payload, stream=string_stream)
                debug_print(f"Payload content:\n{string_stream.getvalue()}")
            except Exception as e:
                debug_print(f"Error pretty-printing payload: {e}")
                debug_print(f"Raw Payload: {payload}")
            # --- DEBUGGING END ---

            response = requests.post(
                "https://api.notion.com/v1/pages",
                headers=headers, # Pass headers correctly
                json=payload # Pass payload correctly
            ) # SYNCHRONOUS CALL - Potential issue

            # Check response
            if response.status_code == 200:
                response_data = response.json()
                page_id = response_data["id"]
                console.print(f"[success]Created Notion page with ID: {page_id}[/success]")
                
                # Get the URL of the page
                page_url = f"https://notion.so/{page_id.replace('-', '')}"
                console.print(f"[success]Page URL: {page_url}[/success]")
                
                # Inform the user
                console.print("\n[success]Blog post pushed to Notion successfully![/success]")
                console.print(f"[info]The page has been created with the title: {title}[/info]")
                console.print(f"[info]You can access it at: {page_url}[/info]")
            else:
                error_message = f"Notion API responded with status code {response.status_code}: {response.text}"
                console.print(f"[error]{error_message}[/error]")
                raise Exception(error_message)
            
        except Exception as e:
            console.print(f"[error]Error pushing to Notion: {str(e)}[/error]")
            # If there's an error, save the content locally as a fallback
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"blog_post_{timestamp}.md"
            filepath = os.path.join(DRAFTS_DIR, filename)

            # Ensure drafts directory exists
            os.makedirs(DRAFTS_DIR, exist_ok=True)
            
            # Save the content
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(blog_content)
            
            console.print(f"[info]Blog post saved locally as a fallback to: {filepath}[/info]")
    
    return blog_content

# === NEW: Function to ensure live posts are downloaded ===
from promaia.storage.files import get_existing_page_ids # Need this function
from promaia.notion.pages import get_pages_by_properties # Need this function
# We need process_page, which is currently in cli.py. Easiest might be to move it?
# For now, let's assume we can import it or reimplement minimally here.
# Option 1: Import (might cause circular dependency issues)
# from promaia.cli import process_page 
# Option 2: Reimplement (simpler for now, avoids potential cli dependencies)
async def _minimal_process_page(page_id: str, content_type: str = "webflow"):
    """Minimal reimplementation or placeholder for process_page"""
    from promaia.notion.pages import get_page_title, get_block_content, clear_block_cache
    from promaia.markdown.converter import page_to_markdown
    from promaia.storage.files import save_page_to_file
    
    print_text(f"  Downloading page: {page_id}", style="dim")
    clear_block_cache()
    title = await get_page_title(page_id)
    blocks = await get_block_content(page_id)
    markdown_content = page_to_markdown(blocks)
    filepath = await save_page_to_file(page_id, title, markdown_content, content_type)
    print_text(f"  ✓ Saved page to {filepath}", style="dim")

async def ensure_latest_live_posts_downloaded(content_type: str = "webflow"):
    """Ensure that all Notion pages with Blog Status 'Live' are downloaded locally."""
    print_text(f"\n[info]Ensuring latest 'Live' posts for content type '{content_type}' are downloaded...[/info]", style="dim")
    try:
        print_text(f"  Querying Notion DB {content_type} for 'Live' posts...", style="dim")
        live_pages = await get_pages_by_properties(content_type, {"Blog Status": "Live"}) # Assumes get_pages_by_properties exists and works for status
        
        if live_pages is None: # Indicates an error occurred in the query
             console.print("[warning]Could not retrieve 'Live' pages from Notion. Skipping local check.[/warning]")
             return

        live_page_ids = {page['id'] for page in live_pages}
        console.print(f"  Found {len(live_page_ids)} 'Live' posts in Notion.", style="dim")
        
        local_page_ids = get_existing_page_ids(content_type=content_type)
        console.print(f"  Found {len(local_page_ids)} posts in local '{content_type}' directory.", style="dim")
        
        missing_page_ids = live_page_ids - local_page_ids
        
        if not missing_page_ids:
            console.print("  All 'Live' posts are present locally.", style="dim")
        else:
            console.print(f"[warning]Found {len(missing_page_ids)} 'Live' posts missing locally. Downloading...[/warning]", style="dim")
            # Process missing pages sequentially
            for i, page_id in enumerate(missing_page_ids, 1):
                try:
                    # Using the minimal reimplementation for now
                    await _minimal_process_page(page_id, content_type)
                except Exception as e:
                    console.print(f"[error]Error processing page {page_id}: {str(e)}[/error]", style="dim")
                    continue # Continue with the next page
            console.print("  Finished downloading missing 'Live' posts.", style="dim")
            
    except ImportError as e:
        console.print(f"[error]Import error during live post check: {e}. Make sure all Notion dependencies are installed.[/error]", style="dim")
    except Exception as e:
        # Catch other potential errors during the process
        console.print(f"[error]Unexpected error during live post check/download: {str(e)}[/error]", style="dim")
        # Optionally re-raise or handle differently
        # raise e 
# ==========================================================

def main():
    """Main entry point for the write command."""
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description="Generate a blog post from journal entries")
    parser.add_argument("--days", type=int, help="Number of days to look back for journal entries")
    parser.add_argument("--prompt", type=str, help="Custom prompt for the blog post")
    parser.add_argument("--no-push", action="store_true", help="Do not push to Notion")
    args = parser.parse_args()
    
    # Get days from args or prompt user
    days = args.days
    if days is None:
        # Get the default days value
        days_value = get_chat_days_setting() or 7
        
        # Prompt user for days
        days_input = input(f"Enter number of days to include (default: {days_value}): ").strip()
        
        # If user entered a value, use it; otherwise use the default
        if days_input:
            try:
                days = int(days_input)
                if days < 1:
                    console.print("[warning]Days must be at least 1. Setting to 1.[/warning]")
                    days = 1
                # Update the setting for next time
                set_chat_days_setting(days)
            except ValueError:
                console.print(f"[warning]Invalid input. Using default ({days_value} days).[/warning]")
                days = days_value
        else:
            days = days_value
    
    # Get custom prompt from args or prompt user
    custom_prompt = args.prompt
    
    # Generate the blog post
    asyncio.run(write_blog_post(days=days, custom_prompt=custom_prompt, push_to_notion=not args.no_push))

if __name__ == "__main__":
    main() 