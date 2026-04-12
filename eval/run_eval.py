"""Evaluation runner — drive the public /api/query endpoint with a curated
test set and report the metrics defined in PRD §6.2.

Usage
-----
    # Against the local backend (default API_URL=http://localhost:8000):
    python eval/run_eval.py

    # Against a different host:
    API_URL=http://backend:8000 python eval/run_eval.py

    # Custom test file / output dir:
    python eval/run_eval.py --cases eval/test_queries.yaml --out eval/results

Outputs
-------
    eval/results/<timestamp>.json   — full per-case detail (raw API responses,
                                       which checks passed/failed, latencies)
    eval/results/<timestamp>.md     — human-readable summary table

Metrics computed (PRD §6.2):
    sql_success_rate          — # successful executions / total
    result_accuracy_rate      — # cases passing result_check / total
    schema_recall_rate        — # cases where any expected_table appears in SQL / total
    avg_latency_ms            — mean wall-clock latency
    self_correction_rate     — # cases that recovered after retry / # initial failures
    sql_contains_match_rate  — # cases where every required substring appears in SQL / total
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import httpx
import yaml

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
DEFAULT_TIMEOUT = float(os.getenv("API_TIMEOUT", "180"))
DEFAULT_CASES = Path(__file__).resolve().parent / "test_queries.yaml"
DEFAULT_OUT = Path(__file__).resolve().parent / "results"


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class TestCase:
    id: int
    category: str
    query: str
    source: str = ""  # data source name; empty = use backend default
    expected_tables: list[str] = field(default_factory=list)
    expected_sql_contains: list[str] = field(default_factory=list)
    expected_result_check: dict[str, Any] = field(default_factory=dict)


@dataclass
class CaseResult:
    case_id: int
    category: str
    query: str
    # what the API returned
    success: bool
    generated_sql: str | None
    intent: str | None
    model_used: str | None
    retry_count: int
    latency_ms: int
    error: str | None
    result_row_count: int
    # check outcomes
    schema_recall_hit: bool
    sql_contains_hit: bool
    result_check_pass: bool
    result_check_reason: str
    # per-node timing breakdown (populated since v0.6.0)
    node_timings: dict[str, float] = field(default_factory=dict)
    # raw response for debugging (may be large)
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loading & API calls
# ---------------------------------------------------------------------------


def load_cases(path: Path) -> list[TestCase]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    cases: list[TestCase] = []
    for entry in data.get("test_cases", []) or []:
        cases.append(
            TestCase(
                id=int(entry["id"]),
                category=str(entry.get("category", "")),
                query=str(entry["query"]),
                source=str(entry.get("source", "")),
                expected_tables=list(entry.get("expected_tables", []) or []),
                expected_sql_contains=list(entry.get("expected_sql_contains", []) or []),
                expected_result_check=dict(entry.get("expected_result_check", {}) or {}),
            )
        )
    return cases


def call_api(
    client: httpx.Client,
    api_url: str,
    query: str,
    session_id: str,
    source: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {"query": query, "session_id": session_id}
    if source:
        body["source"] = source
    resp = client.post(f"{api_url}/api/query", json=body)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Per-case checks
# ---------------------------------------------------------------------------

_TABLE_REF_PATTERN = "[^a-zA-Z0-9_]"


def check_schema_recall(sql: str | None, expected: list[str]) -> bool:
    if not expected:
        return True
    if not sql:
        return False
    sql_lower = sql.lower()
    for table in expected:
        # Word-boundary-ish: a table name surrounded by non-identifier chars
        # or at string boundaries.
        pattern = re.compile(
            rf"(?:^|{_TABLE_REF_PATTERN}){re.escape(table.lower())}(?:{_TABLE_REF_PATTERN}|$)"
        )
        if pattern.search(sql_lower):
            return True
    return False


def check_sql_contains(sql: str | None, needles: list[str]) -> bool:
    if not needles:
        return True
    if not sql:
        return False
    sql_lower = sql.lower()
    return all(n.lower() in sql_lower for n in needles)


def check_result(
    rows: list[dict[str, Any]] | None,
    spec: dict[str, Any],
) -> tuple[bool, str]:
    """Evaluate one of the result_check shapes against the actual rows.

    Returns (passed, reason). The reason is empty on success and a short
    description of the failure otherwise.
    """
    if not spec:
        return True, ""
    rows = rows or []
    check_type = spec.get("type", "")

    if check_type == "non_empty":
        return (len(rows) > 0, "" if rows else "no rows returned")

    if check_type == "row_count":
        cond = spec.get("condition", "True")
        try:
            ok = bool(eval(cond, {"__builtins__": {}}, {"count": len(rows)}))  # noqa: S307
        except Exception as exc:  # noqa: BLE001
            return False, f"row_count condition failed to eval: {exc}"
        return ok, "" if ok else f"row_count={len(rows)} fails {cond!r}"

    if check_type == "single_value":
        if len(rows) != 1:
            return False, f"expected 1 row, got {len(rows)}"
        row = rows[0]
        col = spec.get("column")
        if col is None:
            if len(row) != 1:
                return False, f"expected 1 column, got {len(row)}"
            value = next(iter(row.values()))
        else:
            if col not in row:
                return False, f"column {col!r} not in result"
            value = row[col]
        cond = spec.get("condition", "True")
        try:
            ok = bool(eval(cond, {"__builtins__": {}}, {"value": value}))  # noqa: S307
        except Exception as exc:  # noqa: BLE001
            return False, f"single_value condition failed to eval: {exc}"
        return ok, "" if ok else f"value={value!r} fails {cond!r}"

    if check_type == "first_row":
        if not rows:
            return False, "no rows returned"
        col = spec.get("column")
        row = rows[0]
        if col is None or col not in row:
            return False, f"column {col!r} not in first row"
        value = row[col]
        cond = spec.get("condition", "True")
        try:
            ok = bool(eval(cond, {"__builtins__": {}}, {"value": value}))  # noqa: S307
        except Exception as exc:  # noqa: BLE001
            return False, f"first_row condition failed to eval: {exc}"
        return ok, "" if ok else f"value={value!r} fails {cond!r}"

    return False, f"unknown check type: {check_type!r}"


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_one(
    client: httpx.Client,
    api_url: str,
    case: TestCase,
    session_id: str,
) -> CaseResult:
    try:
        resp = call_api(client, api_url, case.query, session_id, source=case.source)
    except httpx.HTTPError as exc:
        return CaseResult(
            case_id=case.id,
            category=case.category,
            query=case.query,
            success=False,
            generated_sql=None,
            intent=None,
            model_used=None,
            retry_count=0,
            latency_ms=0,
            error=f"HTTP error: {exc}",
            result_row_count=0,
            schema_recall_hit=False,
            sql_contains_hit=False,
            result_check_pass=False,
            result_check_reason="API call failed",
            raw={},
        )

    sql = resp.get("generated_sql")
    rows = resp.get("result") or []
    success = bool(resp.get("success"))

    schema_hit = check_schema_recall(sql, case.expected_tables)
    contains_hit = check_sql_contains(sql, case.expected_sql_contains)
    if success:
        result_pass, reason = check_result(rows, case.expected_result_check)
    else:
        result_pass, reason = False, "execution failed"

    return CaseResult(
        case_id=case.id,
        category=case.category,
        query=case.query,
        success=success,
        generated_sql=sql,
        intent=resp.get("intent"),
        model_used=resp.get("model_used"),
        retry_count=int(resp.get("retry_count", 0)),
        latency_ms=int(resp.get("latency_ms", 0)),
        error=resp.get("error"),
        result_row_count=len(rows),
        schema_recall_hit=schema_hit,
        sql_contains_hit=contains_hit,
        result_check_pass=result_pass,
        result_check_reason=reason,
        node_timings=resp.get("node_timings") or {},
        raw=resp,
    )


def aggregate(results: list[CaseResult]) -> dict[str, Any]:
    n = len(results) or 1
    successes = [r for r in results if r.success]
    sql_success_rate = len(successes) / n
    result_accuracy_rate = sum(1 for r in results if r.result_check_pass) / n
    schema_recall_rate = sum(1 for r in results if r.schema_recall_hit) / n
    sql_contains_rate = sum(1 for r in results if r.sql_contains_hit) / n
    avg_latency = sum(r.latency_ms for r in results) / n

    # Self-correction rate: of cases that needed at least one retry, how many
    # ended up succeeding? (numerator counts retried-and-then-succeeded cases.)
    retried = [r for r in results if r.retry_count > 0]
    retried_success = [r for r in retried if r.success]
    self_correction_rate = (
        len(retried_success) / len(retried) if retried else None
    )

    # Per-category breakdown — handy when one category drags the rest down.
    by_category: dict[str, dict[str, Any]] = {}
    for r in results:
        b = by_category.setdefault(
            r.category,
            {"n": 0, "sql_success": 0, "result_pass": 0, "schema_hit": 0},
        )
        b["n"] += 1
        b["sql_success"] += int(r.success)
        b["result_pass"] += int(r.result_check_pass)
        b["schema_hit"] += int(r.schema_recall_hit)

    return {
        "total": n,
        "sql_success_rate": round(sql_success_rate, 4),
        "result_accuracy_rate": round(result_accuracy_rate, 4),
        "schema_recall_rate": round(schema_recall_rate, 4),
        "sql_contains_match_rate": round(sql_contains_rate, 4),
        "avg_latency_ms": round(avg_latency, 2),
        "self_correction_rate": (
            round(self_correction_rate, 4) if self_correction_rate is not None else None
        ),
        "retried_count": len(retried),
        "by_category": by_category,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

# PRD §6.2 Phase 1 thresholds — used to decorate the markdown report only.
PHASE_1_TARGETS = {
    "sql_success_rate": 0.85,
    "result_accuracy_rate": 0.75,
    "schema_recall_rate": 0.80,
    "avg_latency_ms_max": 5000.0,
}


def _verdict(metric_value: float, target: float, *, lower_is_better: bool = False) -> str:
    if lower_is_better:
        return "PASS" if metric_value <= target else "FAIL"
    return "PASS" if metric_value >= target else "FAIL"


def write_reports(
    out_dir: Path,
    results: list[CaseResult],
    metrics: dict[str, Any],
    api_url: str,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = out_dir / f"{stamp}.json"
    md_path = out_dir / f"{stamp}.md"

    payload = {
        "timestamp": stamp,
        "api_url": api_url,
        "metrics": metrics,
        "cases": [asdict(r) for r in results],
    }
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    # Markdown summary -------------------------------------------------------
    lines: list[str] = []
    lines.append(f"# Elytra eval report — {stamp}")
    lines.append("")
    lines.append(f"- API: `{api_url}`")
    lines.append(f"- Total cases: **{metrics['total']}**")
    lines.append("")
    lines.append("## Summary metrics")
    lines.append("")
    lines.append("| Metric | Value | Phase 1 target | Verdict |")
    lines.append("|:---|---:|---:|:---:|")
    lines.append(
        f"| SQL execution success rate | {metrics['sql_success_rate']:.1%} "
        f"| ≥ {PHASE_1_TARGETS['sql_success_rate']:.0%} "
        f"| {_verdict(metrics['sql_success_rate'], PHASE_1_TARGETS['sql_success_rate'])} |"
    )
    lines.append(
        f"| Result accuracy rate | {metrics['result_accuracy_rate']:.1%} "
        f"| ≥ {PHASE_1_TARGETS['result_accuracy_rate']:.0%} "
        f"| {_verdict(metrics['result_accuracy_rate'], PHASE_1_TARGETS['result_accuracy_rate'])} |"
    )
    lines.append(
        f"| Schema recall rate | {metrics['schema_recall_rate']:.1%} "
        f"| ≥ {PHASE_1_TARGETS['schema_recall_rate']:.0%} "
        f"| {_verdict(metrics['schema_recall_rate'], PHASE_1_TARGETS['schema_recall_rate'])} |"
    )
    lines.append(
        f"| Avg latency (ms) | {metrics['avg_latency_ms']:.0f} "
        f"| < {int(PHASE_1_TARGETS['avg_latency_ms_max'])} "
        f"| {_verdict(metrics['avg_latency_ms'], PHASE_1_TARGETS['avg_latency_ms_max'], lower_is_better=True)} |"
    )
    sc_rate = metrics.get("self_correction_rate")
    sc_display = f"{sc_rate:.1%}" if sc_rate is not None else "n/a (no retries)"
    lines.append(
        f"| Self-correction success rate | {sc_display} "
        f"| (informational) | — |"
    )
    lines.append(
        f"| SQL substring match rate | {metrics['sql_contains_match_rate']:.1%} "
        f"| (informational) | — |"
    )
    lines.append("")

    # Per-category
    lines.append("## By category")
    lines.append("")
    lines.append("| Category | N | SQL success | Result pass | Schema hit |")
    lines.append("|:---|---:|---:|---:|---:|")
    for cat, stats in sorted(metrics["by_category"].items()):
        n = stats["n"]
        lines.append(
            f"| {cat} | {n} "
            f"| {stats['sql_success']}/{n} ({stats['sql_success']/n:.0%}) "
            f"| {stats['result_pass']}/{n} ({stats['result_pass']/n:.0%}) "
            f"| {stats['schema_hit']}/{n} ({stats['schema_hit']/n:.0%}) |"
        )
    lines.append("")

    # Per-case detail
    lines.append("## Per-case detail")
    lines.append("")
    lines.append(
        "| ID | Category | Query | Exec | Result | Schema | Retries | Latency (ms) | Notes |"
    )
    lines.append("|---:|:---|:---|:---:|:---:|:---:|---:|---:|:---|")
    for r in results:
        notes_parts: list[str] = []
        if r.error:
            notes_parts.append(f"err: {r.error[:60]}")
        if r.result_check_reason and not r.result_check_pass:
            notes_parts.append(r.result_check_reason[:60])
        notes = "; ".join(notes_parts)
        # Escape pipes in markdown table cells
        query_clean = r.query.replace("|", "\\|")
        notes_clean = notes.replace("|", "\\|")
        lines.append(
            f"| {r.case_id} | {r.category} | {query_clean} "
            f"| {'✓' if r.success else '✗'} "
            f"| {'✓' if r.result_check_pass else '✗'} "
            f"| {'✓' if r.schema_recall_hit else '✗'} "
            f"| {r.retry_count} | {r.latency_ms} | {notes_clean} |"
        )
    lines.append("")
    lines.append(f"_Full JSON: `{json_path.name}`_")

    with md_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return json_path, md_path


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="Backend base URL")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="Test set YAML")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Report output dir")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="Per-request HTTP timeout"
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="Run only cases whose category matches this string",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Session id to send with each query (default: timestamp-based)",
    )
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.filter:
        cases = [c for c in cases if args.filter in c.category]
    if not cases:
        print(f"No test cases loaded from {args.cases}", file=sys.stderr)
        return 1

    session_id = args.session_id or f"eval-{int(time.time())}"
    print(
        f"Running {len(cases)} cases against {args.api_url} "
        f"(session_id={session_id})",
        flush=True,
    )

    results: list[CaseResult] = []
    with httpx.Client(timeout=args.timeout) as client:
        for i, case in enumerate(cases, start=1):
            print(f"  [{i}/{len(cases)}] #{case.id} {case.query[:60]}", flush=True)
            r = run_one(client, args.api_url, case, session_id)
            results.append(r)
            print(
                f"      exec={'✓' if r.success else '✗'} "
                f"result={'✓' if r.result_check_pass else '✗'} "
                f"schema={'✓' if r.schema_recall_hit else '✗'} "
                f"retries={r.retry_count} latency={r.latency_ms}ms",
                flush=True,
            )

    metrics = aggregate(results)
    json_path, md_path = write_reports(args.out, results, metrics, args.api_url)

    print()
    print("=" * 60)
    print("Summary:")
    print(f"  SQL success rate     : {metrics['sql_success_rate']:.1%}")
    print(f"  Result accuracy rate : {metrics['result_accuracy_rate']:.1%}")
    print(f"  Schema recall rate   : {metrics['schema_recall_rate']:.1%}")
    print(f"  Avg latency (ms)     : {metrics['avg_latency_ms']:.0f}")
    sc = metrics.get("self_correction_rate")
    if sc is not None:
        print(f"  Self-correction rate : {sc:.1%}  (over {metrics['retried_count']} retried cases)")
    print(f"\nReports:\n  {json_path}\n  {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
