"""Versioned, explicit test graphs and their locally recorded executions."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Callable, Iterator

SCHEMA_VERSION = "dreamlake.cict/v1"
STATUSES = {"not_run", "running", "passed", "failed", "skipped", "unknown"}
TERMINAL = {"passed", "failed", "skipped", "unknown"}


def _id(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("IDs must be nonempty strings of at most 256 characters")
    return value


def _json(value: Any) -> Any:
    """Reject non-JSON values instead of silently coercing user attributes."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [_json(v) for v in value]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return {k: _json(v) for k, v in value.items()}
    raise ValueError("attributes must contain only finite JSON values and string keys")


def _attributes(value: dict | None) -> dict:
    if value is not None and not isinstance(value, dict):
        raise ValueError("attributes must be an object")
    return _json(value or {})


def _metrics(value: dict | None) -> dict:
    value = _attributes(value)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in value.values()):
        raise ValueError("metrics must be finite numbers")
    return value


def _write(path: str | Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Replace a complete sibling file so readers never observe truncated snapshots.
    import os
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, allow_nan=False, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Suite:
    """Declare all tests and branch alternatives before executing any code.

    IDs are supplied by the caller and must remain stable across runs. A suite
    is a graph definition, not a scheduler: callers explicitly execute tests
    and select decisions. Unvisited alternatives remain ``not_run``.
    """
    def __init__(self, suite_id: str, *, name: str | None = None):
        self.id = _id(suite_id)
        self.name = name if name is not None else self.id
        if not isinstance(self.name, str):
            raise ValueError("name must be a string")
        self._records: dict[str, dict] = {}
        self._edges: dict[str, dict] = {}

    def node(self, node_id: str, *, name: str | None = None, test_id: str | None = None,
             kind: str = "test", parent_id: str | None = None,
             attributes: dict | None = None) -> "Suite":
        node_id = _id(node_id)
        if node_id in self._records:
            raise ValueError(f"duplicate node ID: {node_id}")
        if kind not in {"test", "decision", "group"}:
            raise ValueError("kind must be test, decision or group")
        if parent_id is not None and parent_id not in self._records:
            raise ValueError("declare the parent before its child")
        title = name if name is not None else node_id
        if not isinstance(title, str):
            raise ValueError("name must be a string")
        record = {"id": node_id, "testId": _id(test_id if test_id is not None else node_id),
                  "name": title, "kind": kind, "status": "not_run",
                  "attributes": _attributes(attributes), "metrics": {}}
        if parent_id is not None:
            record["parentId"] = parent_id
        self._records[node_id] = record
        return self

    def edge(self, edge_id: str, source: str, target: str, *,
             label: str | None = None, condition: str | None = None) -> "Suite":
        edge_id = _id(edge_id)
        if edge_id in self._edges:
            raise ValueError(f"duplicate edge ID: {edge_id}")
        if source not in self._records or target not in self._records:
            raise ValueError("edge endpoints must be declared nodes")
        # Dependencies are a DAG; parent relationships are separate containment.
        pending, visited = [target], set()
        while pending:
            node = pending.pop()
            if node == source:
                raise ValueError("edges must not form a cycle")
            if node not in visited:
                visited.add(node)
                pending.extend(e["target"] for e in self._edges.values() if e["source"] == node)
        edge = {"id": edge_id, "source": source, "target": target, "selected": None}
        for key, value in (("label", label), ("condition", condition)):
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError(f"{key} must be a string; conditions are descriptions, never code")
                edge[key] = value
        self._edges[edge_id] = edge
        return self

    def snapshot(self) -> dict:
        return deepcopy({"schemaVersion": SCHEMA_VERSION, "kind": "suite", "suiteId": self.id,
                         "name": self.name, "records": list(self._records.values()),
                         "edges": list(self._edges.values())})

    def write(self, path: str | Path) -> None:
        _write(path, self.snapshot())

    @classmethod
    def from_dict(cls, data: dict) -> "Suite":
        """Load a static definition without evaluating conditions or running code."""
        if not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION or data.get("kind") != "suite":
            raise ValueError("expected a dreamlake.cict/v1 suite")
        suite = cls(data.get("suiteId"), name=data.get("name"))
        records, edges = data.get("records"), data.get("edges")
        if not isinstance(records, list) or not isinstance(edges, list):
            raise ValueError("records and edges must be arrays")
        for record in records:
            if not isinstance(record, dict) or record.get("status") != "not_run" or record.get("metrics") != {}:
                raise ValueError("static records must be not_run with empty metrics")
            suite.node(record.get("id"), name=record.get("name"), test_id=record.get("testId"),
                       kind=record.get("kind"), parent_id=record.get("parentId"),
                       attributes=record.get("attributes"))
        for edge in edges:
            if not isinstance(edge, dict) or edge.get("selected") is not None:
                raise ValueError("static edges must have null selection")
            suite.edge(edge.get("id"), edge.get("source"), edge.get("target"),
                       label=edge.get("label"), condition=edge.get("condition"))
        return suite

    @classmethod
    def read(cls, path: str | Path) -> "Suite":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def run(self, run_id: str, *, on_event: Callable[[dict], None] | None = None) -> "Run":
        return Run(self, run_id, on_event=on_event)


class Run:
    """Record explicit execution transitions and emit versioned graph snapshots.

    Callbacks receive detached event dictionaries synchronously, after state is
    recorded. Callback failures propagate; no automatic retry or upload occurs.
    One writer per Run is required. Metric units belong in the metric key (for
    example ``durationMs``); missing metrics are absent, never synthesized zero.
    """
    def __init__(self, suite: Suite, run_id: str, *, on_event: Callable[[dict], None] | None = None):
        self._data = suite.snapshot()
        self._data.update(kind="run", runId=_id(run_id))
        self._records = {r["id"]: r for r in self._data["records"]}
        self._edges = {e["id"]: e for e in self._data["edges"]}
        self._events: list[dict] = []
        self._on_event = on_event
        self._emit("run.created", snapshot=self.snapshot())

    def _emit(self, event_type: str, **payload: Any) -> None:
        event = {"schemaVersion": SCHEMA_VERSION, "suiteId": self._data["suiteId"],
                 "runId": self._data["runId"], "sequence": len(self._events) + 1,
                 "time": datetime.now(timezone.utc).isoformat(), "type": event_type, **payload}
        self._events.append(deepcopy(event))
        if self._on_event is not None:
            self._on_event(deepcopy(event))

    def _node(self, node_id: str) -> dict:
        if node_id not in self._records:
            raise ValueError(f"unknown node: {node_id}")
        return self._records[node_id]

    def start(self, node_id: str) -> None:
        node = self._node(node_id)
        if node["status"] != "not_run":
            raise ValueError("only a not_run node can start; use a new run ID for retries")
        node.update(status="running", startedAt=datetime.now(timezone.utc).isoformat())
        self._emit("node.updated", record=deepcopy(node))

    def finish(self, node_id: str, status: str, *, metrics: dict | None = None,
               attributes: dict | None = None) -> None:
        node = self._node(node_id)
        if status not in TERMINAL:
            raise ValueError("finish requires passed, failed, skipped or unknown")
        if node["status"] != "running" and not (node["status"] == "not_run" and status in {"skipped", "unknown"}):
            raise ValueError("a test must start before passing or failing, and cannot finish twice")
        metric_values, attribute_values = _metrics(metrics), _attributes(attributes)
        node["metrics"].update(metric_values)
        node["attributes"].update(attribute_values)
        node.update(status=status, finishedAt=datetime.now(timezone.utc).isoformat())
        self._emit("node.updated", record=deepcopy(node))

    def record(self, node_id: str, *, metrics: dict | None = None,
               attributes: dict | None = None) -> None:
        """Record explicit live measurements without completing a running node."""
        node = self._node(node_id)
        if node["status"] != "running":
            raise ValueError("live measurements require a running node")
        metric_values, attribute_values = _metrics(metrics), _attributes(attributes)
        node["metrics"].update(metric_values)
        node["attributes"].update(attribute_values)
        self._emit("node.updated", record=deepcopy(node))

    def select(self, node_id: str, edge_id: str, *, attributes: dict | None = None) -> None:
        node = self._node(node_id)
        if node["kind"] != "decision" or node["status"] not in {"not_run", "running"}:
            raise ValueError("select requires an unfinished decision node")
        alternatives = [e for e in self._edges.values() if e["source"] == node_id]
        if not any(e["id"] == edge_id for e in alternatives):
            raise ValueError("selected edge must leave this decision")
        attribute_values = _attributes(attributes)
        if node["status"] == "not_run":
            self.start(node_id)
        for edge in alternatives:
            edge["selected"] = edge["id"] == edge_id
        # One event carries the complete selection; consumers never see half a decision.
        self._emit("decision.selected", nodeId=node_id, edges=deepcopy(alternatives))
        self.finish(node_id, "passed", attributes=attribute_values)

    @contextmanager
    def test(self, node_id: str) -> Iterator["Run"]:
        """Record explicit test execution; assertions fail, other exceptions are unknown.

        Exceptions propagate without recording their text, locals or output.
        Explicit finish inside the block overrides the automatic pass.
        """
        self.start(node_id)
        try:
            yield self
        except BaseException as error:
            if self._node(node_id)["status"] == "running":
                self.finish(node_id, "failed" if isinstance(error, AssertionError) else "unknown")
            raise
        else:
            if self._node(node_id)["status"] == "running":
                self.finish(node_id, "passed")

    def snapshot(self) -> dict:
        return deepcopy(self._data)

    def events(self) -> list[dict]:
        return deepcopy(self._events)

    def write(self, path: str | Path) -> None:
        _write(path, self.snapshot())

    def write_events(self, path: str | Path) -> None:
        """Export only explicitly recorded events as newline-delimited JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as stream:
            for event in self._events:
                stream.write(json.dumps(event, allow_nan=False, ensure_ascii=False) + "\n")
