"""GET / PUT /api/config — admin-only runtime configuration.

Allows admin users to view and update the configurable subset of
environment variables. Changes are applied to ``os.environ`` and the
global ``settings`` singleton is rebuilt in-place (hot-reload).

The .env file on disk is **also** updated so changes survive a restart.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.auth.permission import PermissionFilter
import src.config as _cfg
from src.config import CONFIGURABLE_VARS, PROJECT_ROOT, reload_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["config"])

_pf = PermissionFilter(_cfg.settings.permissions_yaml_path)


def _require_admin(user_id: str | None) -> None:
    ctx = _pf.get_context(user_id)
    if ctx.role != "admin":
        raise HTTPException(
            status_code=403,
            detail=f"admin role required, current role: {ctx.role}",
        )


# ---------------------------------------------------------------------------
# GET /api/config — read current configurable vars
# ---------------------------------------------------------------------------


class ConfigVarDescriptor(BaseModel):
    key: str
    value: str
    type: str  # "str" | "float" | "int"
    description: str


class ConfigResponse(BaseModel):
    items: list[ConfigVarDescriptor]


@router.get("/config", response_model=ConfigResponse)
def get_config(user_id: str | None = None) -> ConfigResponse:
    _require_admin(user_id)

    items: list[ConfigVarDescriptor] = []
    current = _cfg.settings  # always read the live singleton
    for env_key, (field_name, typ, desc) in CONFIGURABLE_VARS.items():
        # For INTENT_CLASSIFIER (no field on Settings), read from os.environ
        if field_name:
            value = str(getattr(current, field_name, ""))
        else:
            value = os.getenv(env_key, "")
        items.append(ConfigVarDescriptor(
            key=env_key,
            value=value,
            type=typ.__name__,
            description=desc,
        ))
    return ConfigResponse(items=items)


# ---------------------------------------------------------------------------
# PUT /api/config — update vars, hot-reload settings, persist to .env
# ---------------------------------------------------------------------------


class ConfigUpdateRequest(BaseModel):
    user_id: str | None = None
    updates: dict[str, str]  # {ENV_VAR_NAME: new_value}


class ConfigUpdateResponse(BaseModel):
    success: bool
    applied: dict[str, str]
    message: str = ""


@router.put("/config", response_model=ConfigUpdateResponse)
def put_config(req: ConfigUpdateRequest) -> ConfigUpdateResponse:
    _require_admin(req.user_id)

    applied: dict[str, str] = {}
    errors: list[str] = []

    for key, raw_value in req.updates.items():
        if key not in CONFIGURABLE_VARS:
            errors.append(f"unknown config key: {key}")
            continue

        _, typ, _ = CONFIGURABLE_VARS[key]
        # Validate type
        try:
            if typ is int:
                int(raw_value)
            elif typ is float:
                float(raw_value)
        except ValueError:
            errors.append(f"{key}: expected {typ.__name__}, got {raw_value!r}")
            continue

        os.environ[key] = raw_value
        applied[key] = raw_value

    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    # Rebuild the Settings singleton from updated os.environ
    reload_settings()

    # Persist to .env so changes survive restart
    _persist_to_dotenv(applied)

    logger.info("config hot-reloaded: %s", list(applied.keys()))
    return ConfigUpdateResponse(
        success=True,
        applied=applied,
        message=f"已更新 {len(applied)} 项配置并热加载",
    )


def _persist_to_dotenv(updates: dict[str, str]) -> None:
    """Update .env file on disk, preserving existing lines."""
    env_path = PROJECT_ROOT / ".env"
    lines: list[str] = []
    seen: set[str] = set()

    if env_path.exists():
        lines = env_path.read_text().splitlines(keepends=True)

    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                seen.add(key)
                continue
        new_lines.append(line if line.endswith("\n") else line + "\n")

    # Append any new keys not already in the file
    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={value}\n")

    env_path.write_text("".join(new_lines))
