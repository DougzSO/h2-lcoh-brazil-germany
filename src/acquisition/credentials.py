"""Reads one-time manual-setup credentials (e.g. the CDSE token required by
landuse_fetch.py) from environment variables, an untracked .env file at the
project root, or an untracked config/cdse_credentials.json file. Fails
explicitly if a required credential is absent from all three. Never logs a
credential's value -- only its name and which source(s) were checked."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DOTENV_PATH = _PROJECT_ROOT / ".env"
_JSON_CREDENTIALS_PATH = _PROJECT_ROOT / "config" / "cdse_credentials.json"

load_dotenv(dotenv_path=_DOTENV_PATH)


class MissingCredentialError(RuntimeError):
    """Raised when a required credential is not set."""


def _read_json_credential(name: str) -> str | None:
    """Best-effort read of `name` from config/cdse_credentials.json.
    Returns None (never raises) if the file is missing, unparseable, or
    lacks the key -- callers fall through to the MissingCredentialError
    path, which reports all checked sources."""
    if not _JSON_CREDENTIALS_PATH.exists():
        return None
    try:
        data = json.loads(_JSON_CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get(name)
    return value if value else None


def get_credential(name: str) -> str:
    """Return credential `name`, checked in order: environment variable
    (including one loaded from .env), then config/cdse_credentials.json.
    Raises MissingCredentialError if unset/empty in all sources."""
    value = os.environ.get(name) or _read_json_credential(name)
    if not value:
        raise MissingCredentialError(
            f"Required credential '{name}' is not set. Set it as an "
            f"environment variable, add it to an untracked .env file at "
            f"'{_DOTENV_PATH}' (e.g. {name}=your_value_here), or add it to "
            f'an untracked \'{_JSON_CREDENTIALS_PATH}\' (e.g. {{"{name}": '
            f'"your_value_here"}}).'
        )
    return value


def get_cdse_token() -> str:
    """Return the CDSE token required by landuse_fetch.py. Raises
    MissingCredentialError if CDSE_TOKEN is not set."""
    return get_credential("CDSE_TOKEN")
