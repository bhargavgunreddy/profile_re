from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    # .../src/polygon/ -> repo root is two levels up
    return Path(__file__).resolve().parents[2]


def _load_dotenv_file(path: Path) -> None:
    """
    Minimal .env loader:
    - lines: KEY=VALUE
    - ignores blank lines and comments (# ...)
    - does NOT override existing os.environ values
    """
    if not path.exists() or not path.is_file():
        return

    try:
        content = path.read_text(encoding="utf-8")
    except PermissionError:
        # In some sandboxed environments (or locked-down files), reading local secrets may be blocked.
        # Fail closed (do nothing) and allow env vars / other locations to provide the key.
        return
    except OSError:
        return

    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if not k:
            continue
        os.environ.setdefault(k, v)


_DOTENV_LOADED = False


def load_dotenv() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    root = _repo_root()
    # Common local-secret patterns.
    # Prefer repo-root files, but also support running from subdirectories.
    candidates = [
        root / ".env",
        root / ".env.local",
        # If user created .env next to the script folder by mistake, still pick it up.
        Path(__file__).resolve().parent / ".env",
        Path(__file__).resolve().parent / ".env.local",
        # If running from a different cwd, allow local .env there too.
        Path.cwd() / ".env",
        Path.cwd() / ".env.local",
    ]
    for p in candidates:
        _load_dotenv_file(p)
    _DOTENV_LOADED = True


def debug_polygon_key(*, env_var: str = "POLYGON_API_KEY") -> dict:
    """
    Returns a small diagnostic dict (does not print secrets).
    Useful to verify that `.env` is being discovered.
    """
    load_dotenv()
    root = _repo_root()
    candidates = [
        str(root / ".env"),
        str(root / ".env.local"),
        str(Path(__file__).resolve().parent / ".env"),
        str(Path(__file__).resolve().parent / ".env.local"),
        str(Path.cwd() / ".env"),
        str(Path.cwd() / ".env.local"),
    ]
    return {
        "cwd": str(Path.cwd()),
        "repo_root": str(root),
        "checked": candidates,
        "env_has_key": bool(os.getenv(env_var, "").strip()),
    }


def get_polygon_api_key(*, required: bool = True, env_var: str = "POLYGON_API_KEY") -> str | None:
    """
    Returns the Polygon API key from env (or local .env/.env.local).
    Never stores secrets in tracked code.
    """
    load_dotenv()
    key = os.getenv(env_var, "").strip()
    if key:
        return key
    if required:
        raise RuntimeError(
            f"Missing {env_var}. Set it in your shell or put it in a local .env file (gitignored)."
        )
    return None

