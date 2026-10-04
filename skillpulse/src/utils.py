"""Shared helpers: project paths, logging setup, hashing and small text utilities."""

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from loguru import logger

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

LOG_FORMAT_CONSOLE = (
    "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
)
LOG_FORMAT_FILE = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"


def resolve_path(path: Union[str, Path]) -> Path:
    """Return an absolute path; relative paths are resolved against the project root."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def ensure_parent_dir(path: Path) -> Path:
    """Create the parent directory of ``path`` if needed and return ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def setup_logging(log_file: Union[str, Path], level: str = "INFO") -> None:
    """Configure loguru with a colourful console sink and a rotating file sink."""
    logger.remove()
    logger.add(sys.stderr, level=level, colorize=True, format=LOG_FORMAT_CONSOLE)
    log_path = ensure_parent_dir(resolve_path(log_file))
    logger.add(
        log_path,
        level=level,
        rotation="5 MB",
        retention="14 days",
        encoding="utf-8",
        format=LOG_FORMAT_FILE,
    )


def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def stable_hash(*parts: Optional[str], length: int = 16) -> str:
    """Deterministic short hash of the given parts (case and edge-whitespace insensitive)."""
    joined = "|".join((part or "").strip().lower() for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def truncate(text: Optional[str], max_chars: int) -> Optional[str]:
    """Shorten ``text`` to ``max_chars`` characters, adding an ellipsis when cut."""
    if text is None or len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"
