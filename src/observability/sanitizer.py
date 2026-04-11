"""Input sanitization for user queries before they reach the agent.

Five defensive checks, each calibrated to NL2SQL traffic specifically:

1. **Length cap** — reject > 2000 chars, warn > 1000. Real NL2SQL questions
   are one sentence; anything longer is a prompt-stuffing attempt or a
   copy-paste mistake.
2. **Jailbreak phrase stripping** — regex sweep for ~15 common hijack
   openers in English + Chinese. Strips the phrase, flags a warning, and
   lets the cleaned query continue.
3. **Role reversal rejection** — line-start tokens like ``assistant:`` /
   ``<|im_start|>`` that try to inject a fake assistant turn. Hard reject.
4. **SQL keyword density** — if the user text contains English SQL DDL/DML
   keywords, warn on 1 hit and reject on > 2. Chinese translations are
   ignored (legitimate questions often mention "删除" etc.).
5. **Markdown / system tag fences** — triple backticks and ``<system>``
   tags are almost always exfiltration attempts. Hard reject.

Output-side SQL validation lives in ``connectors.base._validate_sql_safety``
and remains the real safety net. This module is the cheap pre-filter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

MAX_LENGTH = 2000
WARN_LENGTH = 1000


class SanitizerAction(str, Enum):
    PASS = "pass"
    WARN = "warn"
    REJECT = "reject"


@dataclass
class SanitizerResult:
    cleaned: str
    violations: list[str] = field(default_factory=list)
    action: SanitizerAction = SanitizerAction.PASS


_JAILBREAK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+|the\s+)?(previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+(your\s+)?(system\s+)?(prompt|instructions?)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"from\s+now\s+on\s+you", re.IGNORECASE),
    re.compile(r"\bdan\s+mode\b", re.IGNORECASE),
    re.compile(r"\bdeveloper\s+mode\b", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*[:：]", re.IGNORECASE),
    re.compile(r"###\s*instructions?", re.IGNORECASE),
    re.compile(r"override\s+(your\s+)?(system\s+)?prompt", re.IGNORECASE),
    # Chinese equivalents
    re.compile(r"忽略(之前|以上|上面|前面)的?(指令|指示|提示)"),
    re.compile(r"你\s*现在\s*是"),
    re.compile(r"新的?(指令|指示)\s*[:：]"),
    re.compile(r"请忽略(所有|之前|以上)"),
]

_ROLE_REVERSAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?m)^\s*(assistant|ai|system|user|claude|gpt|chatgpt)\s*[:：]", re.IGNORECASE),
    re.compile(r"<\|im_start\|>"),
    re.compile(r"<\|im_end\|>"),
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"</\s*system\s*>", re.IGNORECASE),
]

# Only match capitalized English SQL keywords to avoid Chinese false positives
# ("查询删除了哪些订单" legitimately mentions deletion).
_SQL_KEYWORDS_PATTERN = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|ALTER|GRANT|REVOKE|INSERT\s+INTO|"
    r"UPDATE\s+\w+\s+SET|UNION\s+SELECT|UNION\s+ALL\s+SELECT)\b"
    r"|(;\s*SELECT)",
    re.IGNORECASE,
)

_MARKDOWN_FENCE_PATTERN = re.compile(r"```")


def sanitize_user_query(text: str) -> SanitizerResult:
    """Run the five-check sanitizer and return a result.

    The caller is responsible for honoring the ``action`` field:
        - ``PASS``: use ``cleaned`` (possibly identical to input)
        - ``WARN``: use ``cleaned`` but log / persist ``violations``
        - ``REJECT``: short-circuit, do not invoke the agent
    """
    if text is None:
        return SanitizerResult(
            cleaned="",
            violations=["empty_input"],
            action=SanitizerAction.REJECT,
        )

    violations: list[str] = []
    action = SanitizerAction.PASS
    cleaned = text

    # Check 1: length
    stripped = cleaned.strip()
    if not stripped:
        return SanitizerResult(
            cleaned="",
            violations=["empty_input"],
            action=SanitizerAction.REJECT,
        )

    if len(stripped) > MAX_LENGTH:
        return SanitizerResult(
            cleaned=stripped[:MAX_LENGTH],
            violations=["length_exceeded"],
            action=SanitizerAction.REJECT,
        )

    if len(stripped) > WARN_LENGTH:
        violations.append("length_warning")
        action = SanitizerAction.WARN

    # Check 5 (markdown/system fences): run before jailbreak stripping so we
    # catch fenced jailbreak payloads
    if _MARKDOWN_FENCE_PATTERN.search(cleaned):
        violations.append("markdown_fence")
        return SanitizerResult(
            cleaned=cleaned,
            violations=violations,
            action=SanitizerAction.REJECT,
        )

    # Check 3: role reversal — hard reject
    for pattern in _ROLE_REVERSAL_PATTERNS:
        if pattern.search(cleaned):
            violations.append("role_reversal")
            return SanitizerResult(
                cleaned=cleaned,
                violations=violations,
                action=SanitizerAction.REJECT,
            )

    # Check 2: jailbreak phrase — strip and warn
    for pattern in _JAILBREAK_PATTERNS:
        if pattern.search(cleaned):
            cleaned = pattern.sub("", cleaned)
            if "jailbreak" not in violations:
                violations.append("jailbreak")
                action = SanitizerAction.WARN

    # Check 4: SQL keyword density — count English keyword hits
    sql_hits = _SQL_KEYWORDS_PATTERN.findall(cleaned)
    if sql_hits:
        hit_count = len(sql_hits)
        if hit_count > 2:
            violations.append(f"sql_keywords:{hit_count}")
            return SanitizerResult(
                cleaned=cleaned,
                violations=violations,
                action=SanitizerAction.REJECT,
            )
        violations.append(f"sql_keywords:{hit_count}")
        if action == SanitizerAction.PASS:
            action = SanitizerAction.WARN

    cleaned = cleaned.strip()
    if not cleaned:
        # Jailbreak stripping left us with nothing useful
        return SanitizerResult(
            cleaned="",
            violations=violations + ["empty_after_strip"],
            action=SanitizerAction.REJECT,
        )

    return SanitizerResult(
        cleaned=cleaned,
        violations=violations,
        action=action,
    )
