# Rules

## Use get_data_dir() for all data directory paths

Never use hardcoded paths like `~/.promaia` or `maia-data/`, and never read `PROMAIA_DATA_DIR` or other path-related env vars directly with `os.environ` or `os.getenv`. Always use the helpers from `promaia.utils.env_writer` — `get_data_dir()`, `get_project_root()`, etc. This ensures code works both locally and inside Docker containers.

## Use the auth module for credentials

Always use `promaia.auth` to read credentials — e.g. `get_integration("notion")`, `get_integration("google")`, `get_integration("discord")`. Never read tokens like `NOTION_TOKEN` directly from environment variables or workspace `api_key` fields. For new integrations, implement them in `promaia/auth/integrations/` following existing patterns.

## Use uv for all Python commands

Always use `uv` to run Python tooling: `uv pip install`, `uv run`, `uv venv`. Never use bare `pip`, `python`, or `py` directly.
