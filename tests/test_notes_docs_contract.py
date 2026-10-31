"""The Notes API docs' Python tabs are the contract; the SDK must have what they name.

`notes-api-docs.fixtures.json` vendors every ```python block of the seven
`docs/pages/notes/api/**` pages at a pinned workspace commit. Each snippet must
parse, and every method, keyword argument and attribute it uses must exist on
the SDK (checked by walking the AST with a small symbol model and introspecting
the real classes). Snippets that name something the SDK deliberately does not
implement yet are listed in KNOWN_GAPS — explicitly, so the list shrinks as
gaps close and a new divergence fails loudly.

Regenerate the fixture (pinned commit in the file):
    git -C <workspace> show <commit>:docs/pages/notes/api/<page>/+Page.mdx  # per page, ```python blocks
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from dreamlake.api._files import NoteFiles
from dreamlake.api._notes_facade import (
    DiffResult,
    FindResult,
    FocusedRead,
    MappedRead,
    NoteHandle,
    Notes,
    NoteVersions,
    Outline,
    Roster,
    SavedVersion,
    VersionPage,
    VisitReceipt,
)
from dreamlake.api.notes import NoteRef, NoteSnapshot, PatchReceipt

FIXTURE = json.loads((Path(__file__).parent / "notes-api-docs.fixtures.json").read_text())
SNIPPETS = FIXTURE["snippets"]

#: Documented in the pinned pages, deliberately not implemented in this release.
#: Key = the symbol the snippet uses; value = why it is still a gap.
KNOWN_GAPS = {
    "NoteHandle.watch": "typed watch stream with total timeout; durable catch-up (after=) needs server retention",
    "NoteHandle.move": "one move facade across collections is still being specified; rename(name=) covers the title-only case",
    "dl.projects": "other collections' move facades are out of the Notes SDK's scope in this release",
    "dl.artifacts": "see dl.projects",
    "dl.envs": "see dl.projects",
    "dl.pipelines": "see dl.projects",
    "dl.sources": "see dl.projects",
    "dl.annotations": "see dl.projects",
    "dl.tasks": "see dl.projects",
    "dl.infra": "see dl.projects",
}

READ_RESULT = (NoteSnapshot, FocusedRead, MappedRead)
COLLECTIONS = {"notes": Notes}
#: What a documented call or property evaluates to, so chained usage is checked too.
RETURNS = {
    (Notes, "__call__"): NoteHandle, (Notes, "list"): list, (Notes, "create"): NoteHandle,
    (NoteHandle, "read"): READ_RESULT, (NoteHandle, "outline"): Outline, (NoteHandle, "find"): FindResult,
    (NoteHandle, "diff"): DiffResult, (NoteHandle, "presence"): Roster, (NoteHandle, "visit"): VisitReceipt,
    (NoteHandle, "rename"): NoteRef, (NoteHandle, "versions"): NoteVersions, (NoteHandle, "files"): NoteFiles,
    (NoteVersions, "list"): VersionPage, (NoteVersions, "read"): SavedVersion,
    **{(NoteHandle, name): PatchReceipt for name in ("replace", "insert", "remove", "append", "write", "patch")},
}


class _Root:
    """The configured client/module `dl`."""


class _Gap(str):
    """A usage the SDK does not have; propagates silently through the rest of the chain."""


ROOT, OPAQUE = _Root(), object()


def _has(cls: type, name: str) -> bool:
    fields = getattr(cls, "__dataclass_fields__", {})
    return name in fields or hasattr(cls, name)


class Checker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.env: dict[str, object] = {}
        self.missing: set[str] = set()
        self.used: set[str] = set()

    # ── statements ──
    def visit_Assign(self, node: ast.Assign) -> None:
        kind = self.eval(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.env[target.id] = kind

    def visit_Expr(self, node: ast.Expr) -> None:
        self.eval(node.value)

    def visit_For(self, node: ast.For) -> None:
        self.eval(node.iter)
        if isinstance(node.target, ast.Name):
            self.env[node.target.id] = OPAQUE
        for statement in node.body:
            self.visit(statement)

    # ── expressions ──
    def eval(self, node: ast.AST) -> object:
        if isinstance(node, ast.Name):
            return ROOT if node.id == "dl" else self.env.get(node.id, OPAQUE)
        if isinstance(node, ast.Attribute):
            return self.attribute(self.eval(node.value), node.attr)
        if isinstance(node, ast.Call):
            return self.call(node)
        for child in ast.iter_child_nodes(node):
            self.eval(child)
        return OPAQUE

    def attribute(self, base: object, name: str) -> object:
        if base is ROOT:
            if name in COLLECTIONS:
                self.used.add(f"dl.{name}")
                return COLLECTIONS[name]
            self.missing.add(f"dl.{name}")
            return _Gap(f"dl.{name}")
        members = base if isinstance(base, tuple) else (base,)
        if all(isinstance(m, type) for m in members):
            owners = [m for m in members if _has(m, name)]
            if not owners:
                label = f"{members[0].__name__}.{name}"
                self.missing.add(label)
                return _Gap(label)
            self.used.add(f"{owners[0].__name__}.{name}")
            return RETURNS.get((owners[0], name), OPAQUE)
        return OPAQUE if isinstance(base, _Gap) else OPAQUE

    def call(self, node: ast.Call) -> object:
        for arg in node.args:
            self.eval(arg)
        for keyword in node.keywords:
            self.eval(keyword.value)
        if not isinstance(node.func, ast.Attribute):
            return OPAQUE
        base = self.eval(node.func.value)
        name = node.func.attr
        if base is ROOT:
            if name not in COLLECTIONS:
                self.missing.add(f"dl.{name}")
                return _Gap(f"dl.{name}")
            cls, function, label = COLLECTIONS[name], COLLECTIONS[name].__call__, f"dl.{name}(...)"
        else:
            members = base if isinstance(base, tuple) else (base,)
            if not all(isinstance(m, type) for m in members):
                return OPAQUE
            owner = next((m for m in members if _has(m, name)), None)
            if owner is None:
                label = f"{members[0].__name__}.{name}"
                self.missing.add(label)
                return _Gap(label)
            cls, function, label = owner, inspect.getattr_static(owner, name), f"{owner.__name__}.{name}"
            if not callable(function):
                self.missing.add(f"{label} is not callable")
                return OPAQUE
        self.used.add(label)
        self.signature(function, cls, label, node)
        key = (cls, "__call__" if base is ROOT else name)
        return RETURNS.get(key, OPAQUE)

    def signature(self, function, cls: type, label: str, node: ast.Call) -> None:
        params = [p for p in inspect.signature(function).parameters.values() if p.name != "self"]
        names = {p.name for p in params}
        positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if len(node.args) > len(positional) and not any(p.kind is p.VAR_POSITIONAL for p in params):
            self.missing.add(f"{label} takes no positional argument")
        if not any(p.kind is p.VAR_KEYWORD for p in params):
            for keyword in node.keywords:
                if keyword.arg is not None and keyword.arg not in names:
                    self.missing.add(f"{label}({keyword.arg}=)")


def check(code: str) -> Checker:
    checker = Checker()
    checker.visit(ast.parse(code))
    return checker


@pytest.mark.parametrize("snippet", SNIPPETS, ids=[f"{s['page']}#{s['index']}" for s in SNIPPETS])
def test_documented_python_snippet_names_only_what_the_sdk_has(snippet):
    checker = check(snippet["code"])  # ast.parse raising is the parse check
    unexpected = checker.missing - set(KNOWN_GAPS)
    assert not unexpected, f"{snippet['page']}#{snippet['index']} uses {sorted(unexpected)}; docs and SDK diverge"


def test_every_known_gap_is_still_a_gap_and_still_documented():
    # The list is honest in both directions: a gap that closed must leave it,
    # and a gap nobody documents any more is not a gap.
    hit: set[str] = set()
    for snippet in SNIPPETS:
        hit |= check(snippet["code"]).missing & set(KNOWN_GAPS)
    assert hit == set(KNOWN_GAPS), f"stale known gaps: {sorted(set(KNOWN_GAPS) - hit)}"
    for name in ("watch", "move"):
        assert not hasattr(NoteHandle, name)


def test_the_core_documented_surface_is_exercised():
    used: set[str] = set()
    for snippet in SNIPPETS:
        used |= check(snippet["code"]).used
    for label in ("dl.notes(...)", "Notes.list", "Notes.create", "NoteHandle.read", "NoteHandle.outline", "NoteHandle.find",
                  "NoteHandle.diff", "NoteHandle.replace", "NoteHandle.insert", "NoteHandle.remove", "NoteHandle.append",
                  "NoteHandle.write", "NoteHandle.patch", "NoteHandle.presence", "NoteHandle.visit", "NoteHandle.rename",
                  "NoteVersions.list", "NoteVersions.read", "NoteFiles.move"):
        assert label in used, label


def test_fixture_is_pinned():
    assert FIXTURE["commit"] == "798b16d5" and len(SNIPPETS) >= 30
    assert {s["page"] for s in SNIPPETS} == {"index", "reading", "editing", "collaboration", "resources", "moving", "cli"}
