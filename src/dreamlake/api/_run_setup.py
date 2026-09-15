"""Strict metadata-only private setup validation; no local reads or prompts."""
import copy
import re
from urllib.parse import urlsplit
from ._run_source import RunConfigurationError


def validate_setup(value):
    def fail():
        raise RunConfigurationError("Invalid private setup metadata; review repository, pinned commit, selections and outputs")
    def exact(v, keys):
        return isinstance(v, dict) and set(v) == set(keys)
    def match(v, pattern):
        return isinstance(v, str) and re.fullmatch(pattern, v) is not None
    if not exact(value, ["repository", "commit", "destination", "dependencies", "mappings"]):
        fail()
    s = value
    repo = s["repository"]
    if not match(repo, r"https://[A-Za-z0-9.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git") or len(repo) > 2048 or urlsplit(repo).netloc != urlsplit(repo).hostname or any(p in {".", ".."} for p in urlsplit(repo).path.split("/")):
        fail()
    if not match(s["commit"], r"[a-f0-9]{40}") or not match(s["destination"], r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}") or s["dependencies"] != "uv-lock" or not isinstance(s["mappings"], list) or not 1 <= len(s["mappings"]) <= 100:
        fail()
    ids, envs, paths = set(), set(), []
    for m in s["mappings"]:
        if not exact(m, ["id", "entryId", "revision", "valueShape", "selection", "output"]):
            fail()
        if not match(m["id"], r"[A-Za-z0-9_-]{1,64}") or m["id"] in ids or not match(m["entryId"], r"[A-Za-z0-9_-]{1,128}") or type(m["revision"]) is not int or not 1 <= m["revision"] <= 9007199254740991 or m["valueShape"] not in ("string", "map"):
            fail()
        ids.add(m["id"])
        sel, out = m["selection"], m["output"]
        if not isinstance(sel, dict) or not exact(sel, ["kind"] if sel.get("kind") == "whole" else ["kind", "names"]) or sel["kind"] not in ("whole", "fields"):
            fail()
        if sel["kind"] == "fields":
            names = sel["names"]
            if m["valueShape"] != "map" or not isinstance(names, list) or not 1 <= len(names) <= 100 or any(not match(n, r"[A-Za-z0-9_-]{1,128}") or n in ("__proto__", "prototype", "constructor") for n in names) or len(set(names)) != len(names):
                fail()
        scalar = m["valueShape"] == "string" or (sel["kind"] == "fields" and len(sel["names"]) == 1)
        if isinstance(out, dict) and out.get("kind") == "env":
            if not exact(out, ["kind", "name"]) or not scalar or not match(out["name"], r"[A-Za-z_][A-Za-z0-9_]{0,127}") or re.fullmatch(r"PATH|HOME|USER|SHELL|BASH_ENV|ENV|IFS|CDPATH|LD_.*|DYLD_.*|PYTHON.*|UV_.*|DREAMLAKE_.*", out["name"]) or out["name"] in envs:
                fail()
            envs.add(out["name"])
        else:
            if not exact(out, ["kind", "path", "format"]) or out["kind"] != "file" or out["format"] not in ("utf8", "json") or (out["format"] == "utf8" and not scalar):
                fail()
            path = out["path"]
            if not isinstance(path, str) or len(path) > 1024 or any(c in path for c in ("\\", "\r", "\n", "\0")) or any(p in ("", ".", "..") for p in path.split("/")) or any(p == path or p.startswith(path + "/") or path.startswith(p + "/") for p in paths):
                fail()
            paths.append(path)
    return copy.deepcopy(s)
