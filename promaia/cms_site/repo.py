"""Git plumbing for the static-site checkout.

The checkout lives under ``get_data_dir()`` by default (``cms.site.checkout_dir``).
Authentication uses the ``github`` integration from ``promaia.auth``: the token
is handed to each git subprocess as an ``http.extraheader`` through
``GIT_CONFIG_*`` environment variables, so it is never written to
``.git/config``, the remote URL or the logs.
"""
from __future__ import annotations

import base64
import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

COMMIT_NAME = "Promaia CMS sync"
COMMIT_EMAIL = "cms-sync@users.noreply.github.com"


class SiteRepoError(RuntimeError):
    pass


def resolve_checkout_dir(checkout_dir: str) -> Path:
    """Relative paths resolve under get_data_dir(); absolute paths are kept."""
    p = Path(checkout_dir).expanduser()
    if p.is_absolute():
        return p
    from promaia.utils.env_writer import get_data_dir
    return get_data_dir() / p


def _github_token() -> Optional[str]:
    try:
        from promaia.auth import get_integration
        return get_integration("github").get_default_credential()
    except Exception as e:  # missing integration / unreadable .env
        logger.debug(f"No GitHub credential available: {e}")
        return None


def _git_env(repo_url: str) -> dict:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    token = _github_token()
    if token and repo_url.startswith("https://github.com/"):
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {basic}"
    return env


def _scrub(text: str) -> str:
    token = _github_token()
    if token:
        text = text.replace(token, "***")
    return text


def run_git(args: Sequence[str], cwd: Optional[Path], repo_url: str = "",
            check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None, env=_git_env(repo_url),
        capture_output=True, text=True,
    )
    if check and proc.returncode != 0:
        raise SiteRepoError(f"git {' '.join(args[:2])} failed: {_scrub(proc.stderr.strip() or proc.stdout.strip())}")
    return proc


def is_git_checkout(path: Path) -> bool:
    return (path / ".git").exists()


def ensure_checkout(path: Path, repo_url: str, branch: str, pull: bool) -> str:
    """Make sure *path* holds the site repo. Returns a short status string.

    Missing → clone. Present and ``pull`` → fast-forward to origin/<branch>
    (fails rather than merging if the checkout has diverged).
    """
    if not path.exists() or not any(path.iterdir()):
        if not repo_url:
            raise SiteRepoError(f"site checkout {path} is missing and no repo_url is configured")
        path.parent.mkdir(parents=True, exist_ok=True)
        run_git(["clone", "--branch", branch, repo_url, str(path)], cwd=None, repo_url=repo_url)
        return "cloned"
    if not pull:
        return "used as-is"
    if not is_git_checkout(path):
        raise SiteRepoError(f"{path} exists but is not a git checkout; cannot pull/push")
    run_git(["fetch", "origin", branch], cwd=path, repo_url=repo_url)
    run_git(["merge", "--ff-only", f"origin/{branch}"], cwd=path, repo_url=repo_url)
    return "pulled"


def commit_and_push(path: Path, repo_url: str, branch: str, pathspecs: List[str],
                    message: str, push: bool = True) -> Optional[str]:
    """Stage *pathspecs*, commit if anything changed, push. Returns the commit sha or None."""
    # Pathspecs are directories/files that still exist (deletions inside them are
    # staged by -A); a pathspec matching nothing would make git add fail.
    existing = [p for p in pathspecs if (path / p).exists()]
    if not existing:
        return None
    run_git(["add", "-A", "--", *existing], cwd=path, repo_url=repo_url)
    if run_git(["diff", "--cached", "--quiet"], cwd=path, check=False).returncode == 0:
        return None
    identity: List[str] = []
    if not run_git(["config", "user.email"], cwd=path, check=False).stdout.strip():
        identity = ["-c", f"user.name={COMMIT_NAME}", "-c", f"user.email={COMMIT_EMAIL}"]
    run_git([*identity, "commit", "-m", message], cwd=path, repo_url=repo_url)
    sha = run_git(["rev-parse", "--short", "HEAD"], cwd=path).stdout.strip()
    if push:
        run_git(["push", "origin", f"HEAD:{branch}"], cwd=path, repo_url=repo_url)
    return sha
