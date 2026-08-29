# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import leaf, license_class, manifest, source_ref, strings_with_lines

GENERATOR = "trillionniumgame-metrics-ops-denominator"

METRIC_FILES = ["server/metrics.go", "server/status.go"]
OPS_ROOT_FILES = ["main.go", "Dockerfile", "Makefile", "docker-compose.yml", "docker-compose-postgres.yml", "docker-compose-cockroachdb.yml", "buf.yaml", "buf.lock", "buf.sh"]

FUNC_RE = re.compile(r"(?m)^func\s+(?:\([^\n)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")
DOCKER_RE = re.compile(r"(?mi)^\s*(FROM|RUN|COPY|ADD|ENTRYPOINT|CMD|EXPOSE|HEALTHCHECK|USER|WORKDIR|ENV|ARG|VOLUME)\s+(.+)$")
MAKE_TARGET_RE = re.compile(r"(?m)^([A-Za-z0-9_.-]+)\s*:(?![=])")
COMPOSE_SERVICE_RE = re.compile(r"(?m)^  ([A-Za-z0-9_.-]+):\s*$")
COMPOSE_PORT_RE = re.compile(r"(?m)^\s*-\s*[\"']?([^\n\"']*:[0-9]+(?:/[a-z]+)?)[\"']?\s*$")


def extract_metrics_ops(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    metric_leaves: list[dict[str, Any]] = []
    metric_manual: list[dict[str, Any]] = []
    ops_leaves: list[dict[str, Any]] = []
    ops_manual: list[dict[str, Any]] = []

    for relative in METRIC_FILES:
        path = root / relative
        if not path.is_file():
            metric_manual.append({"class": "missing_metric_source", "symbol": relative})
            continue
        data = path.read_bytes()
        if license_class(data) != "apache-2.0":
            metric_manual.append({"class": "restricted_metric_source", "symbol": relative, "source": source_ref(root, relative).to_dict()})
            continue
        text = data.decode("utf-8")
        for match in FUNC_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            metric_leaves.append(leaf("D7", "metric_function", f"{relative}:{match.group(1)}", source_ref(root, relative, line, line), {"name": match.group(1)}, owner="observability", workstream="W15", task="TG-W0-002"))
        for value, line in strings_with_lines(text):
            lower = value.lower()
            item_class = None
            if value.startswith("/") and any(marker in lower for marker in ("health", "status", "metrics")):
                item_class = "health_or_status_route_candidate"
            elif re.fullmatch(r"[a-z][a-z0-9_:.-]{2,100}", value) and any(marker in lower for marker in ("api", "grpc", "socket", "match", "leader", "tournament", "storage", "runtime", "session", "request", "latency", "duration", "count", "gauge", "histogram")):
                item_class = "metric_name_candidate"
            elif any(marker in lower for marker in ("counter", "gauge", "histogram", "summary")) and len(value) < 180:
                item_class = "metric_help_or_type_candidate"
            if item_class:
                metric_leaves.append(leaf("D7", item_class, f"{relative}:{item_class}@{line}:{value[:100]}", source_ref(root, relative, line, line), {"value": value}, owner="observability", workstream="W15", task="TG-W0-002"))

    candidate_paths: list[Path] = []
    for relative in OPS_ROOT_FILES:
        path = root / relative
        if path.is_file():
            candidate_paths.append(path)
    build_root = root / "build"
    if build_root.is_dir():
        candidate_paths.extend(sorted(p for p in build_root.rglob("*") if p.is_file()))
    for path in sorted(set(candidate_paths)):
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        state = license_class(data)
        ref = source_ref(root, relative)
        ops_leaves.append(leaf("D7", "ops_source_file", relative, ref, {"size": len(data), "mode_executable": bool(path.stat().st_mode & 0o111), "license_class": state}, owner="sre", workstream="W15", task="TG-W0-002"))
        if state == "restricted-review-required":
            ops_manual.append({"class": "restricted_ops_source", "symbol": relative, "source": ref.to_dict()})
            continue
        text = data.decode("utf-8", errors="replace")
        if path.name.lower().startswith("dockerfile") or path.name == "Dockerfile":
            for match in DOCKER_RE.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                ops_leaves.append(leaf("D7", "dockerfile_instruction", f"{relative}:{match.group(1).upper()}@{line}", source_ref(root, relative, line, line), {"instruction": match.group(1).upper(), "argument": match.group(2).strip()}, owner="sre", workstream="W15", task="TG-W0-002"))
        if path.name == "Makefile" or path.suffix == ".mk":
            for match in MAKE_TARGET_RE.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                ops_leaves.append(leaf("D7", "make_target_candidate", f"{relative}:{match.group(1)}", source_ref(root, relative, line, line), {"target": match.group(1)}, owner="release-engineering", workstream="W16", task="TG-W0-002"))
        if path.suffix in {".yml", ".yaml"} and "compose" in path.name:
            for match in COMPOSE_SERVICE_RE.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                ops_leaves.append(leaf("D7", "compose_service_candidate", f"{relative}:{match.group(1)}", source_ref(root, relative, line, line), {"service": match.group(1)}, owner="sre", workstream="W15", task="TG-W0-002"))
            for match in COMPOSE_PORT_RE.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                ops_leaves.append(leaf("D7", "compose_port_candidate", f"{relative}:{match.group(1)}@{line}", source_ref(root, relative, line, line), {"mapping": match.group(1).strip()}, owner="sre", workstream="W15", task="TG-W0-002"))

    metric_manual.append({"class": "observability_blackbox_required", "symbol": "metrics-health-logs", "reason": "Source strings do not prove emitted label sets, cardinality, scrape behavior, readiness semantics or log contracts."})
    ops_manual.append({"class": "operations_blackbox_required", "symbol": "startup-shutdown-upgrade-backup", "reason": "File inventory does not prove startup ordering, graceful drain, migration, backup/PITR, rolling upgrade or failure semantics."})
    metrics = manifest("DEN-METRICS", "D7", metric_leaves, metric_manual, generator=GENERATOR)
    ops = manifest("DEN-OPS", "D7", ops_leaves, ops_manual, generator=GENERATOR)
    reconciliation = {"schema": "trillionnium.metrics-ops-reconciliation.v1", "status": "candidate-unclassified", "metric_leaf_count": metrics["leaf_count"], "ops_leaf_count": ops["leaf_count"], "source_file_count": sum(1 for item in ops_leaves if item["class"] == "ops_source_file"), "sg1_eligible": False, "compatibility_credit": False}
    return metrics, ops, reconciliation
