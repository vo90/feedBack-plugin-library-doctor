"""Privacy boundary for diagnostic logging.

Library Doctor UI and API responses may show the selected local package to the
person using the plugin.  Support-facing logs have a different contract: retain
an opaque per-session correlation token, never the song/package identity, local
path, or exception text that may contain either.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path
from typing import Any


_PACKAGE_SUFFIXES = (".feedpak", ".sloppak")
_PRIVATE_PATH = "[local-path]"


class PrivacySafeLog:
    """Logger adapter that redacts library identities before host collection."""

    def __init__(self, logger, *, salt: bytes | None = None):
        if logger is None:
            raise ValueError("A logger is required.")
        self._logger = logger
        self._salt = bytes(salt) if salt is not None else secrets.token_bytes(16)

    def _opaque_package(self, value: str) -> str:
        digest = hashlib.sha256(self._salt + value.encode("utf-8", "replace")).hexdigest()
        return f"package:{digest[:12]}"

    def _safe_string(self, value: str) -> str:
        normalized = value.strip().lower()
        if any(suffix in normalized for suffix in _PACKAGE_SUFFIXES):
            return self._opaque_package(value)
        if "/" in value or "\\" in value:
            return _PRIVATE_PATH
        return value

    def _safe(self, value: Any):
        if isinstance(value, BaseException):
            return type(value).__name__
        if isinstance(value, (Path, os.PathLike)):
            return _PRIVATE_PATH
        if isinstance(value, str):
            return self._safe_string(value)
        if isinstance(value, dict):
            return {self._safe(key): self._safe(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return tuple(self._safe(item) for item in value)
        if isinstance(value, list):
            return [self._safe(item) for item in value]
        if isinstance(value, set):
            return {self._safe(item) for item in value}
        return value

    def _emit(self, method: str, message, args, kwargs):
        safe_message = self._safe(message)
        safe_args = tuple(self._safe(arg) for arg in args)
        getattr(self._logger, method)(safe_message, *safe_args, **kwargs)

    def debug(self, message, *args, **kwargs):
        self._emit("debug", message, args, kwargs)

    def info(self, message, *args, **kwargs):
        self._emit("info", message, args, kwargs)

    def warning(self, message, *args, **kwargs):
        self._emit("warning", message, args, kwargs)

    def warn(self, message, *args, **kwargs):
        self.warning(message, *args, **kwargs)

    def error(self, message, *args, **kwargs):
        self._emit("error", message, args, kwargs)

    def critical(self, message, *args, **kwargs):
        self._emit("critical", message, args, kwargs)

    def exception(self, message, *args, **kwargs):
        # Host tracebacks can reproduce private exception messages even when the
        # explicit log argument is sanitized.  Retain the exception type through
        # the sanitized argument and deliberately omit traceback collection.
        kwargs.pop("exc_info", None)
        self._emit("error", message, args, {**kwargs, "exc_info": False})

    def log(self, level, message, *args, **kwargs):
        safe_message = self._safe(message)
        safe_args = tuple(self._safe(arg) for arg in args)
        self._logger.log(level, safe_message, *safe_args, **kwargs)

    def isEnabledFor(self, level):  # noqa: N802 - mirrors logging.Logger
        return self._logger.isEnabledFor(level)

    def __getattr__(self, name):
        return getattr(self._logger, name)
