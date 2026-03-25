# File Transfer Tools — Implementation Plan

Built-in tools for Google Drive download → Gmail upload, with a sandboxed local filesystem for intermediate storage.

## Architecture

```
Agent Run Start
  → create /tmp/promaia/{run_id}/
  → tools registered with sandbox_root reference

Agent calls search_drive_files → returns file IDs + names
Agent calls download_drive_file → writes to sandbox, returns relative path
Agent calls send_email(attachment_paths=["report.pdf"]) → reads from sandbox, attaches

Agent Run End
  → shutil.rmtree(/tmp/promaia/{run_id}/)
```

All file paths the agent sees are **relative to the sandbox root**. Tool handlers resolve them internally via `sandbox_root / agent_path` and reject anything that escapes via `..` or absolute paths.

## Tools

### `search_drive_files`

Search Google Drive by name, folder, or MIME type.

```python
{
    "name": "search_drive_files",
    "description": "Search Google Drive for files by name or query.",
    "parameters": {
        "query": {
            "type": "str",
            "required": True,
            "description": "Search query. Supports filename matches or Drive query syntax (e.g. \"name contains 'invoice'\" or \"mimeType='application/pdf'\")."
        },
        "max_results": {
            "type": "int",
            "required": False,
            "default": 10,
            "description": "Maximum number of results to return."
        }
    },
    "returns": {
        "files": [
            {
                "id": "drive file ID",
                "name": "filename.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 102400,  # null for Google-native types
                "modified_time": "ISO 8601",
                "is_google_native": False  # true for Docs/Sheets/Slides
            }
        ]
    }
}
```

### `download_drive_file`

Download a file from Drive into the agent's sandbox.

```python
{
    "name": "download_drive_file",
    "description": "Download a file from Google Drive to the local workspace. Google-native files (Docs, Sheets, Slides) are exported to the specified format.",
    "parameters": {
        "file_id": {
            "type": "str",
            "required": True,
            "description": "Google Drive file ID from search_drive_files."
        },
        "filename": {
            "type": "str",
            "required": False,
            "description": "Override the filename in the workspace. Defaults to the original Drive filename."
        },
        "export_format": {
            "type": "str",
            "required": False,
            "default": "pdf",
            "description": "Export format for Google-native files. Options: pdf, docx, xlsx, csv, pptx, txt. Ignored for non-native files."
        }
    },
    "returns": {
        "path": "report.pdf",  # relative to sandbox
        "size_bytes": 102400,
        "mime_type": "application/pdf"
    }
}
```

### `list_workspace_files`

List files currently in the agent's sandbox.

```python
{
    "name": "list_workspace_files",
    "description": "List files in the local workspace.",
    "parameters": {},
    "returns": {
        "files": [
            {
                "path": "report.pdf",
                "size_bytes": 102400,
                "mime_type": "application/pdf"
            }
        ]
    }
}
```

### Existing Gmail tools — add `attachment_paths`

Add an optional `attachment_paths: list[str]` parameter to `send_email`, `create_draft`, and `reply_to_email` (or whichever Gmail tools currently exist). Each path is resolved against the sandbox root.

```python
# Addition to existing tool parameters:
"attachment_paths": {
    "type": "list[str]",
    "required": False,
    "default": [],
    "description": "List of workspace file paths to attach."
}
```

## Implementation

### 1. Sandbox manager (`maia/tools/sandbox.py`)

Handles lifecycle and path resolution. Intentionally simple — no abstraction beyond what's needed.

```python
import os
import shutil
import uuid
import mimetypes
from pathlib import Path

class Sandbox:
    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.root = Path(f"/tmp/promaia/{self.run_id}")
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str) -> Path:
        """Resolve a relative path to an absolute sandbox path. Raises on escape."""
        resolved = (self.root / relative_path).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise ValueError(f"Path escapes sandbox: {relative_path}")
        return resolved

    def list_files(self) -> list[dict]:
        files = []
        for p in self.root.iterdir():
            if p.is_file():
                mime, _ = mimetypes.guess_type(str(p))
                files.append({
                    "path": p.name,
                    "size_bytes": p.stat().st_size,
                    "mime_type": mime or "application/octet-stream",
                })
        return files

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)
```

### 2. Drive tools (`maia/tools/drive.py`)

Requires adding `https://www.googleapis.com/auth/drive.readonly` to the existing Google OAuth2 scopes.

```python
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

# Google-native MIME types → export MIME mapping
EXPORT_MIME_MAP = {
    "application/vnd.google-apps.document": {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain",
    },
    "application/vnd.google-apps.spreadsheet": {
        "pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv",
    },
    "application/vnd.google-apps.presentation": {
        "pdf": "application/pdf",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    },
}

# File extension for each export format
EXPORT_EXTENSIONS = {
    "pdf": ".pdf", "docx": ".docx", "xlsx": ".xlsx",
    "csv": ".csv", "pptx": ".pptx", "txt": ".txt",
}


def search_drive_files(credentials, query: str, max_results: int = 10) -> list[dict]:
    service = build("drive", "v3", credentials=credentials)
    # Wrap bare strings in a name-contains query for convenience
    if "'" not in query and "=" not in query:
        query = f"name contains '{query}'"

    results = service.files().list(
        q=query,
        pageSize=max_results,
        fields="files(id, name, mimeType, size, modifiedTime)",
    ).execute()

    files = []
    for f in results.get("files", []):
        is_native = f["mimeType"].startswith("application/vnd.google-apps.")
        files.append({
            "id": f["id"],
            "name": f["name"],
            "mime_type": f["mimeType"],
            "size_bytes": int(f["size"]) if "size" in f else None,
            "modified_time": f["modifiedTime"],
            "is_google_native": is_native,
        })
    return files


def download_drive_file(
    credentials, sandbox, file_id: str,
    filename: str | None = None, export_format: str = "pdf"
) -> dict:
    service = build("drive", "v3", credentials=credentials)
    meta = service.files().get(fileId=file_id, fields="name, mimeType").execute()

    native_mime = meta["mimeType"]
    is_native = native_mime in EXPORT_MIME_MAP

    if is_native:
        export_mime = EXPORT_MIME_MAP[native_mime].get(export_format, "application/pdf")
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        ext = EXPORT_EXTENSIONS.get(export_format, ".pdf")
        out_name = filename or (Path(meta["name"]).stem + ext)
    else:
        request = service.files().get_media(fileId=file_id)
        out_name = filename or meta["name"]

    out_path = sandbox.resolve(out_name)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    out_path.write_bytes(buf.getvalue())

    import mimetypes as mt
    mime, _ = mt.guess_type(str(out_path))
    return {
        "path": out_name,
        "size_bytes": out_path.stat().st_size,
        "mime_type": mime or "application/octet-stream",
    }
```

### 3. Gmail attachment support (`maia/tools/gmail.py` — modify existing)

Add to existing send/draft tool handlers:

```python
import base64
from email.mime.base import MIMEBase
from email import encoders
import mimetypes

def attach_files(message_body: dict, attachment_paths: list[str], sandbox) -> dict:
    """Add file attachments to an existing Gmail message body.
    
    message_body is a {'raw': base64_encoded_string} dict from the 
    existing email builder. This function decodes it, adds MIME parts,
    and re-encodes.
    """
    import email
    raw = base64.urlsafe_b64decode(message_body["raw"])
    msg = email.message_from_bytes(raw)

    # Convert to multipart/mixed if not already
    if not msg.is_multipart():
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        mixed = MIMEMultipart("mixed")
        for header in ("To", "From", "Subject", "Cc", "Bcc", "In-Reply-To", "References"):
            if msg[header]:
                mixed[header] = msg[header]
        mixed.attach(MIMEText(msg.get_payload(), msg.get_content_subtype()))
        msg = mixed

    for rel_path in attachment_paths:
        abs_path = sandbox.resolve(rel_path)
        if not abs_path.exists():
            raise FileNotFoundError(f"Workspace file not found: {rel_path}")

        mime_type, _ = mimetypes.guess_type(str(abs_path))
        mime_type = mime_type or "application/octet-stream"
        main_type, sub_type = mime_type.split("/", 1)

        part = MIMEBase(main_type, sub_type)
        part.set_payload(abs_path.read_bytes())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition", "attachment",
            filename=abs_path.name,
        )
        msg.attach(part)

    return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
```

### 4. Tool registration

Register tools in the agent's tool registry with the sandbox instance:

```python
# In agent setup, roughly:
sandbox = Sandbox(run_id=agent_run.id)

tools = [
    # ... existing tools ...
    Tool(
        name="search_drive_files",
        description="Search Google Drive for files by name or query.",
        handler=lambda params: search_drive_files(
            credentials=user_credentials,
            query=params["query"],
            max_results=params.get("max_results", 10),
        ),
        parameters=SEARCH_DRIVE_SCHEMA,
    ),
    Tool(
        name="download_drive_file",
        description="Download a file from Google Drive to the local workspace.",
        handler=lambda params: download_drive_file(
            credentials=user_credentials,
            sandbox=sandbox,
            file_id=params["file_id"],
            filename=params.get("filename"),
            export_format=params.get("export_format", "pdf"),
        ),
        parameters=DOWNLOAD_DRIVE_SCHEMA,
    ),
    Tool(
        name="list_workspace_files",
        description="List files in the local workspace.",
        handler=lambda _: {"files": sandbox.list_files()},
        parameters={},
    ),
]

# Modify existing Gmail tools to accept attachment_paths
# and pass sandbox to the attachment handler

try:
    result = await agent_runner.run(tools=tools, ...)
finally:
    sandbox.cleanup()
```

## OAuth Scope Changes

Add to the existing Google OAuth2 configuration:

```python
SCOPES = [
    # existing
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    # new
    "https://www.googleapis.com/auth/drive.readonly",
]
```

Existing users (Mitchell, Fateen) will need to re-authorize to pick up the new scope. This is a one-time prompt.

## Rough Effort Estimate

| Task | Estimate |
|---|---|
| Sandbox class | 30 min |
| Drive search + download tools | 2 hr |
| Gmail attachment modification | 1 hr |
| Tool registration wiring | 30 min |
| OAuth scope + re-auth flow | 30 min |
| Manual testing with pilot users | 1 hr |
| **Total** | **~5.5 hr** |

## Deferred

- `rename_workspace_file` / `move_workspace_file` — add only if agents get confused about filenames in practice.
- `upload_to_drive` — inverse direction, add when a pilot user needs it.
- Size limits / streaming for large files — not needed at pilot scale.
- Nested subdirectory support in sandbox — keep flat for now.
