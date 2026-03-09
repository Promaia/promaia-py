# Claude Code Project Instructions

## Python tooling

- Use `uv` for all Python commands: `uv pip install`, `uv run`, `uv venv`
- Do NOT use bare `pip`, `python`, or `py` directly

## Docker / Services

- After changing service code, run `maia services restart <service>` or `maia services restart all` to apply changes (takes up to 5 seconds for the supervisor to pick up the restart request)
- The web service (`maia-web`) uses uvicorn `--reload` and auto-restarts on `.py` changes in dev mode — no manual restart needed
- After adding, removing, or changing any packages, rebuild the docker image and restart/recreate all containers.

## Paths

- Always use `get_data_dir()` (from `promaia.utils.env_writer`) for anything under the data directory — never use relative paths or hardcoded `~/.promaia`
- **NEVER read `PROMAIA_DATA_DIR`, `PROMAIA_PROJECT_ROOT`, or any path-related env vars directly** — always go through the helpers in `promaia.utils.env_writer` (`get_data_dir()`, `get_project_root()`, etc.)
- **STOP AND ASK** before writing any code that constructs a filesystem path from an env var, a hardcoded string like `"maia-data/"`, or `os.environ`. If you're reaching for `os.environ` or `os.getenv` for a directory, you're doing it wrong — use the env_writer helpers instead.
- This ensures code works both locally and inside Docker containers

## Credentials

- Always use the auth module (`promaia.auth`) to read credentials: `get_integration("notion")`, `get_integration("google")`, `get_integration("discord")`, etc.
- If the integration you need isn't implemented in the auth module yet:
  - For new integrations: implement it in `promaia/auth/integrations/` following existing patterns
  - For existing code that predates the auth module: match the surrounding code's pattern, but prefer migrating to the auth module when practical
- Never read `NOTION_TOKEN` or workspace `api_key` fields directly — use `get_integration("notion").get_notion_credentials(workspace)`

## Scratchspace

- Use `scratchspace_*/` folders for temporary planning, research, and working documents
- Name them descriptively: `scratchspace_merge_plan/`, `scratchspace_auth_refactor/`, etc.
- These folders are gitignored and will be deleted once the plan or implementation is complete
- Do NOT store anything permanent here — use proper project paths for lasting artifacts

## Prosecheck

- Prosecheck is a prose-based linting tool that enforces project rules defined in `RULES.md` files — see https://github.com/Promaia/prosecheck for details
- Run `prosecheck` after major changes (new features, refactors, architectural shifts) to verify compliance with project conventions
- Rules are defined in `RULES.md` at the repo root; each markdown heading is a rule name and its body describes what to check

## Git commit style

- No emojis in commit messages
- Subject line under 80 characters
- Body should be a concise bulleted list of changes
- Describe what changed and why, not which files were touched — the diff shows that
