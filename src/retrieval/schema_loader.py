"""Load `db/data_dictionary.yaml` into typed in-memory objects.

Each table becomes a `TableInfo` whose `to_text()` method renders an
embedding-friendly textual description (used by both BM25 and the embedder).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.config import settings


@dataclass
class ColumnInfo:
    name: str
    type: str
    chinese_name: str = ""
    description: str = ""
    is_primary_key: bool = False
    enum_values: list[str] = field(default_factory=list)
    business_logic: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "chinese_name": self.chinese_name,
            "description": self.description,
            "is_primary_key": self.is_primary_key,
            "enum_values": self.enum_values,
            "business_logic": self.business_logic,
        }


@dataclass
class TableInfo:
    name: str
    layer: str
    chinese_name: str
    description: str
    columns: list[ColumnInfo]
    common_queries: list[str] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    update_frequency: str | None = None
    row_count_approx: int | None = None

    def to_text(self) -> str:
        """Concatenated description used for BM25 / embedding inputs."""
        parts: list[str] = [
            f"[{self.layer}] 表 {self.name} ({self.chinese_name}): {self.description}"
        ]
        if self.common_queries:
            parts.append("常用查询: " + "; ".join(self.common_queries))

        col_segments: list[str] = []
        for col in self.columns:
            seg = f"{col.name}({col.chinese_name}) {col.type}: {col.description}".strip()
            if col.enum_values:
                seg += f" [取值: {', '.join(map(str, col.enum_values))}]"
            if col.business_logic:
                seg += f" [计算: {col.business_logic}]"
            col_segments.append(seg)
        if col_segments:
            parts.append("字段: " + "; ".join(col_segments))

        if self.relationships:
            rel_parts = [
                f"{r.get('target_table')}({r.get('join_key')},{r.get('join_type', '')})"
                for r in self.relationships
            ]
            parts.append("关联: " + ", ".join(rel_parts))

        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.name,
            "layer": self.layer,
            "chinese_name": self.chinese_name,
            "description": self.description,
            "columns": [c.to_dict() for c in self.columns],
            "common_queries": self.common_queries,
            "relationships": self.relationships,
            "update_frequency": self.update_frequency,
            "row_count_approx": self.row_count_approx,
        }


class SchemaLoader:
    """Reads and caches the data dictionary YAML."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else settings.data_dictionary_path
        self._tables: list[TableInfo] | None = None

    def load(self, *, reload: bool = False) -> list[TableInfo]:
        if self._tables is not None and not reload:
            return self._tables
        with self.path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        tables: list[TableInfo] = []
        for entry in data.get("tables", []) or []:
            columns = [
                ColumnInfo(
                    name=c["name"],
                    type=c.get("type", ""),
                    chinese_name=c.get("chinese_name", ""),
                    description=c.get("description", ""),
                    is_primary_key=bool(c.get("is_primary_key", False)),
                    enum_values=list(c.get("enum_values", []) or []),
                    business_logic=c.get("business_logic"),
                )
                for c in (entry.get("columns") or [])
            ]
            tables.append(
                TableInfo(
                    name=entry["name"],
                    layer=entry.get("layer", ""),
                    chinese_name=entry.get("chinese_name", ""),
                    description=entry.get("description", ""),
                    columns=columns,
                    common_queries=list(entry.get("common_queries") or []),
                    relationships=list(entry.get("relationships") or []),
                    update_frequency=entry.get("update_frequency"),
                    row_count_approx=entry.get("row_count_approx"),
                )
            )
        self._tables = tables
        return tables

    def get_by_name(self, name: str) -> TableInfo | None:
        for t in self.load():
            if t.name == name:
                return t
        return None

    def get_by_layer(self, layer: str) -> list[TableInfo]:
        return [t for t in self.load() if t.layer == layer]
