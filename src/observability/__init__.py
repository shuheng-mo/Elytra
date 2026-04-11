"""Observability primitives: error classification + input sanitization.

This module is deliberately small and has no third-party dependencies so it
can be imported from any layer (nodes, persistence, API) without creating
import cycles.
"""

from src.observability.errors import ErrorType, classify_error
from src.observability.sanitizer import (
    SanitizerAction,
    SanitizerResult,
    sanitize_user_query,
)

__all__ = [
    "ErrorType",
    "classify_error",
    "SanitizerAction",
    "SanitizerResult",
    "sanitize_user_query",
]
