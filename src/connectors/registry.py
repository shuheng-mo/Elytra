"""Process-wide connector registry.

The registry is a singleton owned by the FastAPI app:

* ``init_from_yaml(path)`` — startup event reads ``config/datasources.yaml``,
  builds every connector via :class:`ConnectorFactory`, and ``connect()`` s
  each one. Failures during connect log a warning but do NOT prevent the rest
  of the registry from starting up — a degraded source is better than a hard
  crash, and the agent can still serve queries against healthy sources.
* ``get(name=None)`` — runtime lookup. ``None`` returns the default source
  configured in YAML.
* ``disconnect_all()`` — shutdown event drains every pool.

The registry also exposes ``raw_configs`` so callers (like ``bootstrap.py``)
can iterate the YAML without re-parsing it.

Environment variable expansion: YAML values like ``${DB_HOST:-localhost}`` are
expanded against ``os.environ`` at load time, mirroring the spec's syntax.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

from src.connectors.base import DataSourceConnector
from src.connectors.factory import ConnectorFactory

logger = logging.getLogger(__name__)


_ENV_VAR_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR:-default}`` placeholders in YAML scalars."""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            var, default = m.group(1), m.group(2) or ""
            return os.environ.get(var, default)

        return _ENV_VAR_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


class ConnectorRegistry:
    """Singleton registry of all configured data source connectors."""

    _instance: "ConnectorRegistry | None" = None

    def __init__(self) -> None:
        self._connectors: dict[str, DataSourceConnector] = {}
        self._raw_configs: list[dict] = []
        self._default_source: str | None = None
        self._initialized: bool = False

    # ----- singleton plumbing ------------------------------------------------

    @classmethod
    def get_instance(cls) -> "ConnectorRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Test-only: drop the singleton so a fresh init can run."""
        cls._instance = None

    # ----- lifecycle ---------------------------------------------------------

    async def init_from_yaml(self, path: Path | str) -> None:
        """Parse the datasources YAML and connect every entry. Idempotent."""
        if self._initialized:
            return

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"datasources config not found: {path}")

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw = _expand_env(raw)

        self._default_source = raw.get("default_source")
        self._raw_configs = list(raw.get("datasources") or [])

        for ds_cfg in self._raw_configs:
            name = ds_cfg.get("name")
            if not name:
                logger.warning("skipping datasource entry without name: %s", ds_cfg)
                continue
            try:
                connector = ConnectorFactory.create(ds_cfg)
                await connector.connect()
                self._connectors[name] = connector
                logger.info("registered datasource %s [%s]", name, connector.dialect)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "datasource %s failed to initialize (%s); marking unavailable",
                    name,
                    exc,
                )
                # Still register it so list_datasources can show it as disconnected
                try:
                    connector = ConnectorFactory.create(ds_cfg)
                    self._connectors[name] = connector
                except Exception:
                    pass

        if self._default_source and self._default_source not in self._connectors:
            logger.warning(
                "default_source %r not in datasources; clearing default",
                self._default_source,
            )
            self._default_source = None

        self._initialized = True
        logger.info(
            "ConnectorRegistry initialized — %d sources, default=%s",
            len(self._connectors),
            self._default_source,
        )

    async def disconnect_all(self) -> None:
        for name, connector in list(self._connectors.items()):
            try:
                await connector.disconnect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("disconnect %s failed: %s", name, exc)
        self._connectors.clear()
        self._raw_configs = []
        self._default_source = None
        self._initialized = False

    # ----- accessors ---------------------------------------------------------

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def get(self, name: str | None = None) -> DataSourceConnector:
        """Look up a connector by name; ``None`` returns the default source."""
        target = name or self._default_source
        if not target:
            raise KeyError("no source name given and no default_source configured")
        if target not in self._connectors:
            raise KeyError(
                f"unknown datasource: {target!r}. "
                f"Available: {sorted(self._connectors.keys())}"
            )
        return self._connectors[target]

    def list_names(self) -> list[str]:
        return list(self._connectors.keys())

    def list_connectors(self) -> list[DataSourceConnector]:
        return list(self._connectors.values())

    def default_name(self) -> str | None:
        return self._default_source

    def raw_configs(self) -> list[dict]:
        """Return the raw YAML configs (env-expanded), in declaration order."""
        return list(self._raw_configs)
