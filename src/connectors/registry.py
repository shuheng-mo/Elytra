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


USER_LAYER_FILENAME = "datasources.local.yaml"


def _user_layer_path(primary_path: Path) -> Path:
    """User-managed YAML lives next to the main datasources.yaml."""
    return primary_path.parent / USER_LAYER_FILENAME


class ConnectorRegistry:
    """Singleton registry of all configured data source connectors."""

    _instance: "ConnectorRegistry | None" = None

    def __init__(self) -> None:
        self._connectors: dict[str, DataSourceConnector] = {}
        self._raw_configs: list[dict] = []
        self._default_source: str | None = None
        self._initialized: bool = False
        # Names of connectors that originated from the user layer (local YAML,
        # added via API). Only these can be deleted at runtime.
        self._user_managed: set[str] = set()
        self._primary_yaml_path: Path | None = None

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
        """Parse the datasources YAML and connect every entry. Idempotent.

        Also merges a gitignored user layer ``datasources.local.yaml`` sitting
        next to the primary file, so connectors added at runtime via the API
        survive a backend restart.
        """
        if self._initialized:
            return

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"datasources config not found: {path}")

        self._primary_yaml_path = path

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw = _expand_env(raw)

        self._default_source = raw.get("default_source")
        primary_cfgs = list(raw.get("datasources") or [])

        # Merge user layer (runtime-added connectors). Names collide with
        # primary entries? Primary wins and user layer is logged as ignored.
        user_cfgs: list[dict] = []
        user_layer = _user_layer_path(path)
        if user_layer.exists():
            try:
                user_raw = yaml.safe_load(user_layer.read_text(encoding="utf-8")) or {}
                user_raw = _expand_env(user_raw)
                for entry in user_raw.get("datasources") or []:
                    name = entry.get("name")
                    if not name:
                        continue
                    if any(e.get("name") == name for e in primary_cfgs):
                        logger.warning(
                            "user-layer datasource %r shadowed by primary YAML; skipped",
                            name,
                        )
                        continue
                    user_cfgs.append(entry)
                    self._user_managed.add(name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to load user layer %s: %s", user_layer, exc)

        self._raw_configs = primary_cfgs + user_cfgs

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

    def is_user_managed(self, name: str) -> bool:
        """True if this connector was added via the runtime API (user layer)."""
        return name in self._user_managed

    # ----- runtime mutation (user-added connectors) --------------------------

    async def add_connector(self, config: dict) -> DataSourceConnector:
        """Instantiate + connect + register a new connector at runtime.

        Persists the entry to the gitignored user-layer YAML so it survives
        a backend restart. Raises ``ValueError`` on validation failure,
        ``RuntimeError`` on connect/probe failure.
        """
        name = config.get("name")
        if not name:
            raise ValueError("datasource config missing 'name'")
        if name in self._connectors:
            raise ValueError(f"datasource {name!r} already exists")

        # Instantiate + connect + ping. If ping fails, tear down so we don't
        # leave a stale connector in the registry.
        connector = ConnectorFactory.create(config)
        try:
            await connector.connect()
            ok = await connector.test_connection()
            if not ok:
                raise RuntimeError("test_connection returned False")
        except Exception as exc:
            try:
                await connector.disconnect()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"failed to connect {name}: {exc}") from exc

        # Commit to in-memory registry first.
        self._connectors[name] = connector
        self._raw_configs.append(config)
        self._user_managed.add(name)

        # Persist to the user layer YAML (best effort; roll back on failure).
        try:
            self._persist_user_layer()
        except Exception as exc:  # noqa: BLE001
            # Roll back in-memory state.
            self._connectors.pop(name, None)
            self._raw_configs = [c for c in self._raw_configs if c.get("name") != name]
            self._user_managed.discard(name)
            try:
                await connector.disconnect()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"persist user layer failed: {exc}") from exc

        logger.info("added datasource %s [%s] at runtime", name, connector.dialect)
        return connector

    async def remove_connector(self, name: str) -> None:
        """Remove a connector from the registry.

        User-managed entries (from ``datasources.local.yaml``) are permanently
        deleted — both in-memory and on disk. Primary entries (from the
        git-tracked ``datasources.yaml``) are removed from the in-memory
        registry only; they reappear on the next backend restart unless the
        user edits the YAML manually. We refuse to rewrite the git-tracked
        file from the API because that would silently clobber source control.
        """
        if name not in self._connectors:
            raise KeyError(f"unknown datasource: {name!r}")

        is_user_managed = name in self._user_managed

        connector = self._connectors.pop(name)
        self._raw_configs = [c for c in self._raw_configs if c.get("name") != name]
        self._user_managed.discard(name)
        if self._default_source == name:
            self._default_source = None

        try:
            await connector.disconnect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("disconnect %s failed: %s", name, exc)

        if is_user_managed:
            self._persist_user_layer()
            logger.info("removed user-managed datasource %s (persisted)", name)
        else:
            logger.warning(
                "removed primary-layer datasource %s (runtime only; will reappear "
                "on backend restart unless config/datasources.yaml is edited)",
                name,
            )

    def _persist_user_layer(self) -> None:
        """Write user-managed configs to datasources.local.yaml."""
        if self._primary_yaml_path is None:
            raise RuntimeError("registry not initialized from a YAML path")

        user_layer_path = _user_layer_path(self._primary_yaml_path)
        user_cfgs = [
            c for c in self._raw_configs if c.get("name") in self._user_managed
        ]
        doc = {
            "_header": (
                "User-added connectors (gitignored). Edit via the UI or the "
                "/api/datasources API — manual edits may be overwritten."
            ),
            "datasources": user_cfgs,
        }
        yaml_text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
        user_layer_path.write_text(yaml_text, encoding="utf-8")
        # 0600 so secrets (passwords, keys) aren't world-readable
        try:
            os.chmod(user_layer_path, 0o600)
        except Exception:  # noqa: BLE001
            pass
