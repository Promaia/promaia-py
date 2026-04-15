import os
import re
from datetime import datetime, timedelta
import glob # For finding files
import asyncio # For asyncio.run
from dotenv import load_dotenv # For loading .env file

load_dotenv() # Load environment variables from .env file

# Assuming your OpenAI client might be similar to how Anthropic was set up
# We'll need to import and initialize it.
# from openai import OpenAI # Example

# Define constants for directories
SOURCE_JOURNAL_DIR_NAME = "notion-journal" # Relative to project root
PUBLIC_ENTRIES_DIR_NAME = "public-entries" # Relative to project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # Assuming scrubber.py is in maia/
SOURCE_JOURNAL_DIR = os.path.join(PROJECT_ROOT, SOURCE_JOURNAL_DIR_NAME)
PUBLIC_ENTRIES_DIR = os.path.join(PROJECT_ROOT, PUBLIC_ENTRIES_DIR_NAME)

# Initialize OpenAI client (ensure OPENAI_API_KEY is in your .env or environment)
# client = None
# if os.getenv("OPENAI_API_KEY"):
# client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
# else:
# print("WARNING: OPENAI_API_KEY not found. AI scrubbing will not work.")

SHUSH_EMOJI = "🤫"

def ensure_public_dir_exists():
    """Creates the public entries directory if it doesn't exist."""
    os.makedirs(PUBLIC_ENTRIES_DIR, exist_ok=True)
    print(f"Ensured public entries directory exists at: {PUBLIC_ENTRIES_DIR}")

def get_recent_journal_files(days: int = 14) -> list[str]:
    """
    Gets the file paths of markdown files from the source journal directory
    that are dated within the last 'days'.
    It prioritizes dates in filenames (YYYY-MM-DD) and falls back to file mtime.
    """
    glob_pattern = os.path.join(SOURCE_JOURNAL_DIR, "*.md")
    all_md_files = glob.glob(glob_pattern)
    
    if not all_md_files:
        print(f"No .md files found in {SOURCE_JOURNAL_DIR}")
        return []

    file_date_info: list[tuple[str, datetime]] = []
    all_determined_dates: list[datetime] = []

    for file_path in all_md_files:
        filename = os.path.basename(file_path)
        determined_date = None
        date_source = "unknown" # For debugging
        
        # 1. Try to parse date from filename (YYYY-MM-DD)
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
        if date_match:
            try:
                date_str = date_match.group(1)
                determined_date = datetime.strptime(date_str, "%Y-%m-%d")
                date_source = "filename"
            except ValueError:
                pass 
        
        if determined_date is None:
            # 2. Fallback to file creation time (birthtime if available, else ctime)
            try:
                stat_info = os.stat(file_path)
                if hasattr(stat_info, 'st_birthtime') and stat_info.st_birthtime:
                    determined_date = datetime.fromtimestamp(stat_info.st_birthtime)
                    date_source = "birthtime"
                else:
                    # Fallback to ctime if birthtime is not available or zero
                    determined_date = datetime.fromtimestamp(stat_info.st_ctime)
                    date_source = "ctime"
            except OSError as e:
                print(f"Warning: Could not get stat info (ctime/birthtime) for {file_path}: {e}")
                # 3. As a last resort, try modification time (though less preferred now)
                try:
                    mtime = os.path.getmtime(file_path)
                    determined_date = datetime.fromtimestamp(mtime)
                    date_source = "mtime_fallback"
                except OSError as e_mtime:
                    print(f"Warning: Could not get any timestamp for {file_path}: {e_mtime}")
                    continue # Skip this file if no date can be determined
        
        if determined_date:
            # print(f"Determined date for {filename}: {determined_date.strftime('%Y-%m-%d')} (source: {date_source})") # Debug print
            file_date_info.append((file_path, determined_date))
            all_determined_dates.append(determined_date)
        else:
            print(f"Warning: No date could be determined for {file_path}. Skipping.")

    if not all_determined_dates:
        print("Could not determine dates for any files.")
        return []

    # Determine reference date (most recent date among all files)
    reference_date = max(all_determined_dates)
    cutoff_date = reference_date - timedelta(days=days)
    # We want to include files from the cutoff_date itself, so compare date part only.
    cutoff_date_dateonly = cutoff_date.date()

    recent_files = []
    for file_path, file_dt in file_date_info:
        if file_dt.date() >= cutoff_date_dateonly:
            recent_files.append(file_path)
            
    # Sort by date, newest first (optional, but good for processing order)
    recent_files.sort(key=lambda fp: next(dt for p, dt in file_date_info if p == fp), reverse=True)

    print(f"Found {len(recent_files)} files from the last {days} days (cutoff: {cutoff_date_dateonly}).")
    return recent_files


async def scrub_content_with_ai(content: str, openai_client) -> str:
    """
    Sends content to OpenAI API for scrubbing based on predefined rules,
    including handling the shush emoji.
    """
    if not openai_client:
        print("OpenAI client not initialized. Skipping AI scrubbing.")
        return content

    if not content.strip():
        print("Content is empty, skipping AI scrubbing.")
        return ""

    system_prompt = f"""
You are an AI assistant tasked with scrubbing journal entries to make them suitable for a public blog.
Your instructions are:
**Emoji Handling:**
*   If you encounter the emoji '{SHUSH_EMOJI}' in the text, you MUST OMIT the entire block of text (e.g., paragraph, a list item including all its sub-items, or a distinct section) where this emoji appears. Do not attempt to rephrase the shushed content; remove it completely.

**Content to Remove (General):**
*   Completely remove all personal routines (e.g., detailed morning/evening rituals, bathroom habits, exact meal timings unless part of a public event).
*   Remove all names of specific people. EXCEPTION: Keep the names 'Koii' and 'Koii Benvenutto' if they appear. Do not remove these names.
*   Remove all sexual content, explicit or suggestive.
*   Remove specific private personal information not relevant for public sharing (e.g., exact private addresses, private phone numbers, account credentials, private keys, specific personal financial figures not already public knowledge or related to general business performance).
*   Remove ALL mentions of a person named 'Graham'. This includes any activities, interactions, or feelings related to Graham.
*   Remove sensitive interpersonal details: This includes private feelings (positive or negative) expressed towards specific, named individuals (other than general feelings about public figures or groups), detailed accounts of private arguments, or very personal relationship dynamics.

**Content to Keep:**
*   Keep political information, discussions, and opinions.
*   Keep information about Koii's companies, work, projects, professional achievements, and general business activities.
*   Keep general descriptions of daily activities, learning, and reflections that are not overly personal or covered by the removal rules.

**Output Style:**
*   The output should be a cleaned version of the original journal entry.
*   Maintain a natural, coherent, and readable style. Avoid making it sound overly-sanitized or robotic.
*   If an entire section or paragraph is about a topic that needs full removal (e.g., entirely about Graham, or a block marked with '{SHUSH_EMOJI}'), omit that section completely.
*   Do not invent new information or add content not present in the original.
*   Return only the scrubbed journal content. Do not add any conversational preamble or postamble.
*   If, after scrubbing, the entire entry becomes empty (e.g., because all content was shushed or about Graham), return an empty string.

You will be given a journal entry. Apply these rules and provide the scrubbed version.
""" # Used f-string to easily insert SHUSH_EMOJI
    print(f"Sending {len(content.split())} words (including shush emoji instructions) to AI for scrubbing...")
    try:
        chat_completion = await openai_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content}
            ],
            model="gpt-3.5-turbo-0125", 
            temperature=0.2, # Slightly lower temp for more deterministic scrubbing
        )
        scrubbed_text = chat_completion.choices[0].message.content
        print(f"AI scrubbing complete. Original length: {len(content)}, Scrubbed length: {len(scrubbed_text if scrubbed_text else '')}")
        return scrubbed_text if scrubbed_text else ""
    except Exception as e:
        print(f"Error during AI scrubbing: {e}")
        return f"[AI SCRUBBING FAILED: {e}]\n\n{content}"

def save_scrubbed_entry(original_filename: str, scrubbed_content: str):
    """Saves the scrubbed content to the public-entries directory."""
    base_filename = os.path.basename(original_filename)
    public_filepath = os.path.join(PUBLIC_ENTRIES_DIR, base_filename)

    if not scrubbed_content.strip():
        print(f"Scrubbed content for {base_filename} is empty. Not saving.")
        # Optionally, delete existing public file if it should be removed
        # if os.path.exists(public_filepath):
        #     os.remove(public_filepath)
        return

    with open(public_filepath, 'w', encoding='utf-8') as f:
        f.write(scrubbed_content)
    print(f"Saved scrubbed entry to: {public_filepath}")

async def main_scrub_process():
    """Main function to orchestrate the scrubbing process."""
    print("Starting journal scrubbing process...")
    ensure_public_dir_exists()
    
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        print("CRITICAL: OPENAI_API_KEY not found in environment. Cannot proceed with AI scrubbing.")
        return

    from openai import AsyncOpenAI
    openai_client = AsyncOpenAI(api_key=openai_api_key)

    journal_files = get_recent_journal_files(days=14)

    if not journal_files:
        print("No journal files found to process.")
        return

    for filepath in journal_files:
        print(f"\nProcessing file: {filepath}...")
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                raw_content = f.read()
            
            if not raw_content.strip():
                print("File is empty. Skipping.")
                save_scrubbed_entry(os.path.basename(filepath), "")
                continue

            # No longer calling remove_shushed_blocks here
            # Send raw_content directly to AI if it's not empty
            scrubbed_content = await scrub_content_with_ai(raw_content, openai_client)
            
            save_scrubbed_entry(os.path.basename(filepath), scrubbed_content)

        except Exception as e:
            print(f"Failed to process file {filepath}: {e}")

if __name__ == '__main__':
    # If running this script directly
    print("Scrubber module initialized. Attempting to run main_scrub_process...")
    # Example of how you might run it (ensure OPENAI_API_KEY is set):
    if os.getenv("OPENAI_API_KEY"):
       print("OPENAI_API_KEY found. Proceeding with scrub process.")
       asyncio.run(main_scrub_process())
    else:
       print("CRITICAL: OPENAI_API_KEY not found. Please set it in your .env file at the project root.") 