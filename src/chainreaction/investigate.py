"""Investigator agent (M4): deterministic tools for a tool-calling loop.

The model may only assert what a tool returned. Read-only tools
recompute from the workspace + graph + coverage map, so replay
reproduces them without a key; probes run through ctx.run_cmd and
replay from its recordings. Every tool returns compact JSON with
``ok``, ``summary``, and ``truncated`` (+ ``count`` when capped).
"""

from __future__ import annotations

import ast
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chainreaction.graph.repo import relative_posix
from chainreaction.interpret import (
    config_items,
    seed_exclusions,
    signature_seeds,
)
from chainreaction.propagate import (
    _def_index,
    _resolve_call,
    failure_path,
    matches_symbol,
    predicted_chain,
    probe_entries,
    propagate,
)
from chainreaction.runs.context import Cancelled
from chainreaction.testmap import parse_failures
from chainreaction.verifier import (
    frames_to_modules,
    parse_traceback,
    site_packages_subpath,
)

if TYPE_CHECKING:
    from chainreaction.interpret import ChangeSet
    from chainreaction.runs.context import RunContext
    from chainreaction.testmap import TestMap


# -- tool schemas (OpenAI function-calling shape) --

def _schema(name: str, description: str, properties: dict,
            required: list[str] | None = None) -> dict:
    return {"type": "function",
            "function": {"name": name, "description": description,
                         "parameters": {"type": "object",
                                        "properties": properties,
                                        "required": required or []}}}


TOOLS = [
    _schema("list_changes",
            "List the decision's change-set items (symbol, kind, quote, "
            "release). Filter by kind to focus (removed, renamed, "
            "signature_changed, ...).",
            {"kind": {"type": "string",
                      "description": "Only items of this kind."}}),
    _schema("find_usages",
            "Find every use of a removed/renamed symbol in the repo: file, "
            "line, enclosing def, whether it runs at import time, and the "
            "code line.",
            {"symbol": {"type": "string",
                        "description": "Change symbol, e.g. numpy.sctypes."}},
            ["symbol"]),
    _schema("trace_importers",
            "Import chain(s) from a module up to the package root, with "
            "file:line per hop. Shows what breaks when the module breaks.",
            {"module": {"type": "string",
                        "description": "Dotted module, e.g. pkg.mod."},
             "max_depth": {"type": "integer",
                           "description": "Longest chain kept (default 10)."}},
            ["module"]),
    _schema("callers_of",
            "Call paths leading to a function or class, with file:line "
            "per hop. Shows what reaches the definition at call time.",
            {"qualified_def": {"type": "string",
                               "description": "e.g. pkg.mod.helper."},
             "max_depth": {"type": "integer",
                           "description": "Longest path kept (default 6)."}},
            ["qualified_def"]),
    _schema("propagate",
            "Run the static breakage engine for symbols: per-symbol "
            "use counts, HARD/AT_RISK module sets, and the worst "
            "failure chain. Prefer one call with every candidate "
            "symbol over a find_usages per item.",
            {"symbols": {"type": "array", "items": {"type": "string"},
                         "description": "Change symbols to seed as broken."}},
            ["symbols"]),
    _schema("tests_covering",
            "Test ids that execute the given modules or file:line hits, "
            "from the before-env coverage map.",
            {"modules": {"type": "array", "items": {"type": "string"}},
             "lines": {"type": "array", "items": {"type": "string"},
                       "description": "file:line hits, e.g. pkg/mod.py:11."}}),
    _schema("read_file",
            "Read numbered source lines from the workspace (at most 200 "
            "lines per call). Path is workspace-relative.",
            {"path": {"type": "string"},
             "start": {"type": "integer"},
             "end": {"type": "integer"}},
            ["path", "start", "end"]),
    _schema("run_import_probe",
            "Import a module in a target env and report ok, the traceback "
            "chain, and the error line. Confirms import breaks.",
            {"module": {"type": "string"},
             "env": {"type": "string", "enum": ["before", "after"]}},
            ["module", "env"]),
    _schema("run_tests",
            "Run up to 50 test ids in a target env. Reports pass/fail per "
            "test plus a failure tail. Confirms call-time breaks.",
            {"test_ids": {"type": "array", "items": {"type": "string"}},
             "env": {"type": "string", "enum": ["before", "after"]}},
            ["test_ids", "env"]),
    _schema("run_snippet",
            "Run a short Python snippet (at most 40 lines) in a target "
            "env. Reports stdout, stderr, ok. Confirms behavior changes. "
            "No os/subprocess/socket/shutil imports, no open() in write "
            "mode, no __import__.",
            {"code": {"type": "string"},
             "env": {"type": "string", "enum": ["before", "after"]}},
            ["code", "env"]),
    _schema("submit_findings",
            "End the investigation with findings, triage, and a summary. "
            "Every claim must cite tool call ids from this conversation; "
            "only assert what a tool returned.",
            {"findings": {"type": "array", "items": {"type": "object",
                         "properties": {
                             "id": {"type": "string"},
                             "claim": {"type": "string"},
                             "kind": {"type": "string",
                                      "enum": ["breaks_import",
                                               "breaks_call",
                                               "behavior_change",
                                               "not_affected",
                                               "dependency_conflict"]},
                             "symbol": {"type": "string"},
                             "sites": {"type": "array",
                                       "items": {"type": "string"}},
                             "tests": {"type": "array",
                                       "items": {"type": "string"}},
                             "evidence": {"type": "array",
                                          "items": {"type": "string"}},
                             "confidence": {"type": "string",
                                            "enum": ["confirmed", "likely",
                                                     "speculative"]}},
                         "required": ["id", "claim", "kind", "symbol",
                                      "sites", "tests", "evidence",
                                      "confidence"]}},
             "triage": {"type": "array", "items": {"type": "object",
                      "properties": {
                          "symbol": {"type": "string"},
                          "relevant": {"type": "boolean"},
                          "reason": {"type": "string"},
                          "evidence": {"type": "array",
                                       "items": {"type": "string"}}},
                      "required": ["symbol", "relevant", "reason",
                                   "evidence"]}},
             "summary": {"type": "string"}},
            ["findings", "triage", "summary"]),
]


# -- tool environment + dispatch --

@dataclass
class ToolEnv:
    """Everything tools need; probes use ctx.run_cmd (recorded)."""

    ctx: RunContext | None
    workspace: Path
    package: str
    graph: Any  # nx.DiGraph (networkx only needed at runtime by callers)
    changeset: ChangeSet | None = None
    testmap: TestMap | None = None
    before_env: str | None = None
    after_env: str = ""
    probe_code: str = ""
    # Prefix the coverage map adds to test ids (stripped for pytest).
    test_id_prefix: str = ""
    # Memoized per-loop: collected test ids per file (None until first
    # tests_covering call warms it; tools stay deterministic either way).
    _file_cache: dict = field(default_factory=dict, repr=False)


def run_tool(env: ToolEnv, name: str, args: dict | None) -> dict:
    """Execute one tool call; unknown tools fail closed, never raise."""
    args = dict(args or {})
    impl = _READ_ONLY_TOOLS.get(name) or _PROBE_TOOLS.get(name)
    if impl is None:
        return {"ok": False, "summary": f"unknown tool: {name}",
                "error": f"unknown tool: {name}", "truncated": False}
    try:
        result = impl(env, args)
    except Exception as exc:  # noqa: BLE001 - tools fail closed
        return {"ok": False, "summary": f"{name} failed: {exc}",
                "error": f"{name} failed: {exc}", "truncated": False}
    result.setdefault("truncated", False)
    return result


def _cap(items: list, limit: int) -> tuple[list, bool]:
    return (items, False) if len(items) <= limit else (items[:limit], True)


# -- read-only tools --

def _tool_list_changes(env: ToolEnv, args: dict) -> dict:
    items = []
    for item in (env.changeset.items if env.changeset else []):
        if args.get("kind") and item.kind != args["kind"]:
            continue
        items.append({"symbol": item.symbol, "kind": item.kind,
                      "quote": item.quote,
                      "release": item.introduced_in or ""})
    items.sort(key=lambda i: (i["symbol"], i["kind"]))
    kept, truncated = _cap(items, 100)
    return {"ok": True, "items": kept, "count": len(items),
            "truncated": truncated,
            "summary": f"{len(items)} change items"
                       + (f" (kind={args['kind']})" if args.get("kind")
                          else "")}


def _edge_file(env: ToolEnv, file: str | None) -> str:
    if not file:
        return ""
    try:
        return relative_posix(file, env.workspace)
    except ValueError:
        return str(file)


def _code_line(env: ToolEnv, rel: str, lineno: int) -> str:
    if not rel or lineno < 1:
        return ""
    cached = env._file_cache.get(rel)
    if cached is None:
        try:
            text = (env.workspace / rel).read_text(encoding="utf-8")
        except OSError:
            return ""
        cached = env._file_cache[rel] = text.splitlines()
    if lineno > len(cached):
        return ""
    return cached[lineno - 1].strip()


def _runs_at_import(env: ToolEnv, node: str) -> bool:
    """Module scope, or a def a module executes at import (cf. propagate)."""
    g = env.graph
    if g.nodes[node].get("kind") == "module":
        return True
    return any(d.get("kind") == "EXECUTES_AT_IMPORT"
               for _, _, d in g.in_edges(node, data=True))


def _tool_find_usages(env: ToolEnv, args: dict) -> dict:
    symbol = args.get("symbol") or ""
    if not symbol:
        return {"ok": False, "summary": "find_usages needs a symbol",
                "error": "find_usages needs a symbol", "truncated": False}
    excluded = seed_exclusions(env.changeset) if env.changeset else {}
    sites = []
    for u, v, d in env.graph.edges(data=True):
        if d.get("kind") != "USES_EXTERNAL":
            continue
        if matches_symbol(v, {symbol}, excluded) is None:
            continue
        rel = _edge_file(env, d.get("file"))
        line = d.get("line") or 0
        kind = env.graph.nodes[u].get("kind") if u in env.graph.nodes else None
        sites.append({"file": rel, "line": line,
                      "site": f"{rel}:{line}",
                      "def": u if kind in ("function", "class") else None,
                      "import_time": _runs_at_import(env, u),
                      "code": _code_line(env, rel, line)})
    sites.sort(key=lambda s: (s["file"], s["line"]))
    kept, truncated = _cap(sites, 50)
    n_import = sum(1 for s in sites if s["import_time"])
    return {"ok": True, "sites": kept, "count": len(sites),
            "truncated": truncated,
            "summary": f"{len(sites)} sites use {symbol} "
                       f"({n_import} import-time)"}


def _hop(env: ToolEnv, module: str, file: str | None,
         line: int | None) -> dict:
    rel = _edge_file(env, file)
    num = line or 0
    return {"module": module, "file": rel, "line": num,
            "site": f"{rel}:{num}"}


def _tool_trace_importers(env: ToolEnv, args: dict) -> dict:
    module = args.get("module") or ""
    g = env.graph
    if module not in g.nodes:
        return {"ok": False,
                "summary": f"trace_importers: unknown module {module}",
                "error": f"unknown module: {module}", "truncated": False}
    try:
        max_depth = int(args.get("max_depth") or 10)
    except (TypeError, ValueError):
        max_depth = 10
    max_depth = min(max(max_depth, 1), 25)
    node = g.nodes[module]
    chains: list[list[dict]] = []

    def walk(path: list[dict], seen: set[str]) -> None:
        if len(chains) >= 10:
            return
        current = path[-1]["module"]
        if current == env.package:
            chains.append(path)  # chains end at the package root
            return
        ups = sorted({u for u, _, d in g.in_edges(current, data=True)
                      if d.get("kind") in ("IMPORTS", "PARENT_INIT")})
        fresh = [u for u in ups if u not in seen]
        if not fresh or len(path) > max_depth:
            chains.append(path)
            return
        for up in fresh:
            data = g[up][current]
            hop = _hop(env, up, data.get("file"), data.get("line"))
            walk(path + [hop], seen | {up})

    walk([_hop(env, module, node.get("file"), node.get("line") or 1)],
         {module})
    chains.sort(key=lambda c: [h["module"] for h in c])
    kept, truncated = _cap(chains, 10)
    return {"ok": True, "chains": kept, "count": len(chains),
            "truncated": truncated or len(chains) > len(kept),
            "summary": f"{len(chains)} import chain(s) from {module}"}


def _resolve_def(g, name: str) -> str | None:
    if name in g.nodes and g.nodes[name].get("kind") in ("function",
                                                         "class"):
        return name
    cands = sorted(n for n in g.nodes
                   if g.nodes[n].get("kind") in ("function", "class")
                   and (n == name or n.endswith("." + name)))
    return cands[0] if len(cands) == 1 else None


def _tool_callers_of(env: ToolEnv, args: dict) -> dict:
    name = args.get("qualified_def") or ""
    g = env.graph
    target = _resolve_def(g, name)
    if target is None:
        return {"ok": False,
                "summary": f"callers_of: unknown def {name}",
                "error": f"unknown def: {name}", "truncated": False}
    try:
        max_depth = int(args.get("max_depth") or 6)
    except (TypeError, ValueError):
        max_depth = 6
    max_depth = min(max(max_depth, 1), 15)
    node = g.nodes[target]
    # Callers resolve exactly as the AT_RISK closure does: same-module
    # bare calls (`helper()`) are recorded under the raw name, not the
    # qualified def, so raw graph predecessors alone would miss them.
    defs = _def_index(g)
    callers: dict[str, list[tuple[str, str, int]]] = {}
    for u, v, d in g.edges(data=True):
        kind = d.get("kind")
        if kind == "EXECUTES_AT_IMPORT":
            resolved = v
        elif kind == "CALLS":
            resolved = _resolve_call(g, u, v, defs)
        else:
            continue
        if resolved is not None:
            callers.setdefault(resolved, []).append(
                (u, _edge_file(env, d.get("file")), d.get("line") or 0))
    for ups in callers.values():
        ups.sort()
    paths: list[list[dict]] = []

    def walk(path: list[dict], seen: set[str]) -> None:
        if len(paths) >= 10:
            return
        current = path[-1]["def"]
        fresh = [(u, f, ln) for u, f, ln in callers.get(current, [])
                 if u not in seen]
        if not fresh or len(path) > max_depth:
            paths.append(path)
            return
        for up, file, line in fresh:
            hop = {"def": up, "file": file, "line": line,
                   "site": f"{file}:{line}"}
            walk(path + [hop], seen | {up})

    first_file = _edge_file(env, node.get("file"))
    first_line = node.get("line") or 0
    walk([{"def": target, "file": first_file, "line": first_line,
           "site": f"{first_file}:{first_line}"}], {target})
    paths.sort(key=lambda p: [h["def"] for h in p])
    kept, truncated = _cap(paths, 10)
    return {"ok": True, "paths": kept, "count": len(paths),
            "truncated": truncated or len(paths) > len(kept),
            "summary": f"{len(paths)} call path(s) to {target}"}


def _symbol_hits(env: ToolEnv, symbols: list[str],
                 excluded: dict) -> dict:
    """Per-symbol use counts (grounds triage without a call per item)."""
    hits = {s: {"sites": 0, "import_time": 0} for s in symbols}
    for u, v, d in env.graph.edges(data=True):
        if d.get("kind") != "USES_EXTERNAL":
            continue
        for symbol in symbols:
            if matches_symbol(v, {symbol}, excluded) is None:
                continue
            hits[symbol]["sites"] += 1
            if _runs_at_import(env, u):
                hits[symbol]["import_time"] += 1
    return hits


def _tool_propagate(env: ToolEnv, args: dict) -> dict:
    symbols = [s for s in (args.get("symbols") or []) if s]
    if not symbols:
        return {"ok": False, "summary": "propagate needs symbols",
                "error": "propagate needs symbols", "truncated": False}
    changeset = env.changeset
    cfg = config_items(changeset) if changeset else []
    excluded = seed_exclusions(changeset) if changeset else {}
    sig = signature_seeds(changeset) if changeset else {}
    entries = probe_entries(env.probe_code or "", env.package, env.graph)
    impacts = propagate(env.graph, set(symbols), cfg, env.package,
                        entries=tuple(entries), excluded=excluded,
                        sig_seeds=sig)
    hard = sorted(n for n, imp in impacts.items()
                  if imp.status == "HARD"
                  and env.graph.nodes[n].get("kind") == "module")
    at_risk = sorted(n for n, imp in impacts.items()
                     if imp.status == "AT_RISK"
                     and env.graph.nodes[n].get("kind") == "module")
    hops = failure_path(env.graph, impacts, set(symbols), env.package,
                        excluded=excluded, sig_seeds=sig)
    chain = predicted_chain(env.graph, hops)
    hop_dicts = [{"from": u, "to": v, "file": _edge_file(env, f),
                  "line": ln, "site": f"{_edge_file(env, f)}:{ln}"}
                 for u, v, f, ln in hops]
    hard_kept, hard_trunc = _cap(hard, 50)
    risk_kept, risk_trunc = _cap(at_risk, 50)
    hits = _symbol_hits(env, sorted(set(symbols)), excluded)
    n_used = sum(1 for h in hits.values() if h["sites"])
    return {"ok": True, "hard": hard_kept, "at_risk": risk_kept,
            "hard_count": len(hard), "at_risk_count": len(at_risk),
            "symbols": hits,
            "worst_chain": chain, "chain_hops": hop_dicts,
            "truncated": hard_trunc or risk_trunc,
            "summary": f"{len(hard)} HARD + {len(at_risk)} AT_RISK "
                       f"modules; {n_used}/{len(hits)} symbols used; "
                       f"worst chain length {len(chain)}"}


def _tool_tests_covering(env: ToolEnv, args: dict) -> dict:
    if env.testmap is None:
        return {"ok": False,
                "summary": "tests_covering: no coverage map in this run",
                "error": "no coverage map in this run", "truncated": False}
    modules = {m for m in (args.get("modules") or []) if m}
    lines = {ln for ln in (args.get("lines") or []) if ln}
    if not modules and not lines:
        return {"ok": False,
                "summary": "tests_covering needs modules or lines",
                "error": "tests_covering needs modules or lines",
                "truncated": False}
    ids: set[str] = set()
    if modules:
        ids.update(env.testmap.covering(modules))
    if lines:
        ids.update(env.testmap.hitting(lines))
    ordered = sorted(ids)
    kept, truncated = _cap(ordered, 50)
    return {"ok": True, "test_ids": kept, "count": len(ordered),
            "truncated": truncated,
            "summary": f"{len(ordered)} test(s) cover the query"}


def _tool_read_file(env: ToolEnv, args: dict) -> dict:
    try:
        start = int(args.get("start") or 0)
        end = int(args.get("end") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "summary": "read_file needs start/end lines",
                "error": "read_file needs start/end lines",
                "truncated": False}
    if start < 1 or end < start or end - start + 1 > 200:
        return {"ok": False,
                "summary": "read_file: window must be 1..200 lines",
                "error": "window must be 1..200 lines (start>=1, end>=start)",
                "truncated": False}
    rel = (args.get("path") or "").replace("\\", "/").lstrip("/")
    candidate = (env.workspace / rel).resolve()
    try:
        candidate.relative_to(env.workspace.resolve())
    except ValueError:
        return {"ok": False,
                "summary": f"read_file: {rel} escapes the workspace",
                "error": f"path escapes the workspace: {rel}",
                "truncated": False}
    if not candidate.is_file():
        return {"ok": False, "summary": f"read_file: no such file {rel}",
                "error": f"no such file: {rel}", "truncated": False}
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "summary": f"read_file: cannot read {rel}",
                "error": f"cannot read {rel}: {exc}", "truncated": False}
    numbered = [[n, line] for n, line in
                enumerate(text.splitlines(), start=1)
                if start <= n <= end]
    return {"ok": True, "path": rel, "lines": numbered,
            "sites": [f"{rel}:{n}" for n, _ in numbered],
            "truncated": False,
            "summary": f"{rel}:{start}-{end} ({len(numbered)} lines)"}


_READ_ONLY_TOOLS = {
    "list_changes": _tool_list_changes,
    "find_usages": _tool_find_usages,
    "trace_importers": _tool_trace_importers,
    "callers_of": _tool_callers_of,
    "propagate": _tool_propagate,
    "tests_covering": _tool_tests_covering,
    "read_file": _tool_read_file,
}

# -- probe tools (run in the target envs through ctx.run_cmd) --

_MODULE_RE = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*\Z")
_SNIPPET_BANNED_IMPORTS = frozenset({"os", "subprocess", "socket",
                                     "shutil"})
# open() modes that can write (fail closed on anything unparseable).
_WRITE_MODE_CHARS = frozenset("wax+")


def validate_snippet(code: str) -> str | None:
    """Reject unsafe probe code; None means it may run.

    Banned: os/subprocess/socket/shutil imports, open() in write mode,
    __import__, unparseable code, and >40 lines.
    """
    lines = (code or "").splitlines()
    if len(lines) > 40:
        return f"snippet has {len(lines)} lines (at most 40)"
    try:
        tree = ast.parse(code or "")
    except SyntaxError as exc:
        return f"snippet does not parse: {exc}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _SNIPPET_BANNED_IMPORTS:
                    return f"snippet must not import {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in _SNIPPET_BANNED_IMPORTS:
                return f"snippet must not import {node.module}"
        elif isinstance(node, ast.Name) and node.id == "__import__":
            return "snippet must not use __import__"
        elif isinstance(node, ast.Attribute) and node.attr == "__import__":
            return "snippet must not use __import__"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "open":
            mode = None
            if len(node.args) >= 2:
                mode = node.args[1]
            for kw in node.keywords:
                if kw.arg == "mode":
                    mode = kw.value
            if mode is None:
                continue  # open(path) reads
            if not isinstance(mode, ast.Constant) \
                    or not isinstance(mode.value, str):
                return "open() with a non-literal mode is not allowed"
            if set(mode.value) & _WRITE_MODE_CHARS:
                return f"open() in write mode {mode.value!r} is not allowed"
    return None


def _probe_env(env: ToolEnv, which: str) -> str | None:
    if which == "before":
        return env.before_env
    if which == "after":
        return env.after_env or None
    return None


def _run_in_env(env: ToolEnv, env_name: str, cmd: list[str],
                timeout: int):
    """ctx.run_cmd with the non-editable PYTHONPATH fallback (cf. stages)."""
    ctx = env.ctx
    extra = None
    proj = ctx.state.get("project")
    if getattr(proj, "editable", True) is False \
            and getattr(proj, "pythonpath", ""):
        extra = {"PYTHONPATH": proj.pythonpath}
    return ctx.run_cmd(cmd, cwd=str(env.workspace), env_name=env_name,
                       timeout=timeout, extra_env=extra)


def _frame_display(env: ToolEnv, file: str) -> str:
    """Repo-relative file for a traceback frame (deterministic)."""
    # A scrubbed token (<env:after>/site-packages/...) is never a repo
    # file. On POSIX it would pass the workspace check below as a
    # nonexistent relative path; on Windows Path() rejects it. Map it
    # through site-packages first so both OSes give the same answer.
    if str(file).startswith("<"):
        sub = site_packages_subpath(file, env.package)
        if sub:
            return f"{env.package}/{sub}"
    try:
        rel = Path(file)
        ws = env.workspace.resolve()
        if rel.is_absolute():
            try:
                return rel.relative_to(ws).as_posix()
            except ValueError:
                pass
        else:
            cand = (ws / rel).resolve()
            try:
                cand.relative_to(ws)
                return rel.as_posix()
            except ValueError:
                pass
    except OSError:
        pass
    sub = site_packages_subpath(file, env.package)
    if sub:
        return f"{env.package}/{sub}"
    return Path(str(file).replace("\\", "/")).name


def _tool_run_import_probe(env: ToolEnv, args: dict) -> dict:
    module = args.get("module") or ""
    which = args.get("env") or ""
    if not _MODULE_RE.match(module):
        return {"ok": False,
                "summary": f"run_import_probe: bad module {module!r}",
                "error": f"bad module name: {module!r}", "truncated": False}
    env_name = _probe_env(env, which)
    if env_name is None or env.ctx is None:
        reason = (f"no {which} env in this run" if which in ("before",
                                                             "after")
                  else f"bad env: {which!r} (want before|after)")
        if env.ctx is None:
            reason = "probes need a run context"
        return {"ok": False, "summary": f"run_import_probe: {reason}",
                "error": reason, "truncated": False}
    res = _run_in_env(env, env_name,
                      [env.ctx.env_python(env_name), "-c",
                       f"import {module}"],
                      timeout=120)
    if res.returncode is None:
        return {"ok": False,
                "summary": f"run_import_probe: {module} did not start "
                           f"({res.missing or 'missing interpreter'})",
                "error": f"probe did not start: "
                         f"{res.missing or 'missing interpreter'}",
                "truncated": False}
    # Parse unscrubbed (real paths map to modules); report scrubbed.
    frames = parse_traceback(env.ctx.scrubber.untext(res.stderr))
    chain = frames_to_modules(frames, env.workspace, env.package)
    scrubbed_err = env.ctx.scrubber.text(res.stderr)
    err_lines = [ln for ln in scrubbed_err.splitlines() if ln.strip()]
    error = None
    if frames:
        last = frames[-1]
        err_file = _frame_display(env, last.file)
        error = {"file": err_file, "line": last.line, "func": last.func,
                 "site": f"{err_file}:{last.line}"}
    ok = res.returncode == 0
    summary = (f"import {module} in {which}: ok" if ok
               else f"import {module} in {which} fails: "
                    f"{' -> '.join(chain) or 'no in-repo frames'}")
    return {"ok": ok, "chain": chain, "error": error,
            "error_line": err_lines[-1] if err_lines else "",
            "timed_out": res.timed_out, "truncated": False,
            "summary": summary}


def _strip_prefix(env: ToolEnv, tid: str) -> str:
    tid = tid.replace("\\", "/")
    prefix = (env.test_id_prefix or "").replace("\\", "/")
    if prefix and tid.startswith(prefix):
        return tid[len(prefix):]
    return tid


def _tool_run_tests(env: ToolEnv, args: dict) -> dict:
    raw_ids = [t for t in (args.get("test_ids") or [])
               if isinstance(t, str) and t]
    which = args.get("env") or ""
    if not raw_ids or len(raw_ids) > 50:
        return {"ok": False,
                "summary": "run_tests needs 1..50 test ids",
                "error": "run_tests needs 1..50 test ids",
                "truncated": False}
    if any("\n" in t or "\r" in t or "\0" in t for t in raw_ids):
        return {"ok": False, "summary": "run_tests: bad test id",
                "error": "bad test id", "truncated": False}
    env_name = _probe_env(env, which)
    if env_name is None or env.ctx is None:
        reason = (f"no {which} env in this run" if which in ("before",
                                                             "after")
                  else f"bad env: {which!r} (want before|after)")
        if env.ctx is None:
            reason = "probes need a run context"
        return {"ok": False, "summary": f"run_tests: {reason}",
                "error": reason, "truncated": False}
    ids = [_strip_prefix(env, t) for t in raw_ids]
    res = _run_in_env(env, env_name,
                      [env.ctx.env_python(env_name), "-m", "pytest", "-q",
                       "--tb=short", "-rfE", "-p", "no:cacheprovider",
                       *ids],
                      timeout=600)
    if res.returncode is None:
        return {"ok": False,
                "summary": "run_tests did not start "
                           f"({res.missing or 'missing interpreter'})",
                "error": f"run_tests did not start: "
                         f"{res.missing or 'missing interpreter'}",
                "truncated": False}
    scrubbed = env.ctx.scrubber.text(res.stdout)
    failed, errfiles = parse_failures(scrubbed, env.workspace)
    results = []
    ran = bool(failed or errfiles) or res.returncode == 0
    for want, plain in zip(raw_ids, ids):
        if plain in failed:
            outcome = "fail"
        elif any(plain == f or plain.startswith(f.rstrip("/") + "/")
                 or plain.startswith(f + "::") for f in errfiles):
            outcome = "error"
        elif ran:
            # A completed run lists every failure: unlisted ran clean.
            outcome = "pass"
        else:
            # Startup crash / abort: nothing attributable, don't guess.
            outcome = "unknown"
        results.append({"id": want, "outcome": outcome})
    out_lines = scrubbed.splitlines()
    tail = out_lines[-15:]
    n_fail = sum(1 for r in results if r["outcome"] == "fail")
    n_err = sum(1 for r in results if r["outcome"] == "error")
    return {"ok": True, "results": results, "tail": "\n".join(tail),
            "timed_out": res.timed_out,
            "truncated": len(out_lines) > len(tail),
            "summary": f"{len(results)} test(s) in {which}: "
                       f"{n_fail} failed, {n_err} errors"}


def _tool_run_snippet(env: ToolEnv, args: dict) -> dict:
    code = args.get("code") or ""
    which = args.get("env") or ""
    rejected = validate_snippet(code)
    if rejected is not None:
        return {"ok": False, "summary": f"run_snippet: {rejected}",
                "error": rejected, "truncated": False}
    env_name = _probe_env(env, which)
    if env_name is None or env.ctx is None:
        reason = (f"no {which} env in this run" if which in ("before",
                                                             "after")
                  else f"bad env: {which!r} (want before|after)")
        if env.ctx is None:
            reason = "probes need a run context"
        return {"ok": False, "summary": f"run_snippet: {reason}",
                "error": reason, "truncated": False}
    res = _run_in_env(env, env_name,
                      [env.ctx.env_python(env_name), "-c", code],
                      timeout=120)
    if res.returncode is None:
        return {"ok": False,
                "summary": "run_snippet did not start "
                           f"({res.missing or 'missing interpreter'})",
                "error": f"snippet did not start: "
                         f"{res.missing or 'missing interpreter'}",
                "truncated": False}
    out = env.ctx.scrubber.text(res.stdout)
    err = env.ctx.scrubber.text(res.stderr)
    out_kept, out_trunc = (out, False) if len(out) <= 2000 \
        else (out[:2000], True)
    err_lines = err.splitlines()
    err_kept = "\n".join(err_lines[-10:])
    frames = parse_traceback(env.ctx.scrubber.untext(res.stderr))
    chain = frames_to_modules(frames, env.workspace, env.package)
    ok = res.returncode == 0
    return {"ok": ok, "stdout": out_kept, "stderr": err_kept,
            "chain": chain, "timed_out": res.timed_out,
            "truncated": out_trunc or len(err_lines) > 10,
            "summary": f"snippet in {which}: "
                       f"{'ok' if ok else 'failed'}"}


_PROBE_TOOLS = {
    "run_import_probe": _tool_run_import_probe,
    "run_tests": _tool_run_tests,
    "run_snippet": _tool_run_snippet,
}
# submit_findings is intercepted by the loop, never dispatched.

_PROBE_CALL_NAMES = frozenset(_PROBE_TOOLS)
_NUDGE = "Call submit_findings with your findings."
# At most this many non-exit calls run per turn; the rest are dropped
# with a note (never run, never counted) so one giant batch cannot
# blow the whole budget. submit_findings always runs.
_MAX_CALLS_PER_TURN = 8

_FINDING_KINDS = frozenset({"breaks_import", "breaks_call",
                            "behavior_change", "not_affected",
                            "dependency_conflict"})
_CONFIDENCES = frozenset({"confirmed", "likely", "speculative"})
_FINDING_KEYS = ("id", "claim", "kind", "symbol", "sites", "tests",
                 "evidence", "confidence")
_TRIAGE_KEYS = ("symbol", "relevant", "reason", "evidence")


# -- agent loop --

@dataclass
class LoopBudgets:
    max_tool_calls: int = 25
    max_probe_calls: int = 8
    wall_seconds: int = 360
    max_tokens: int = 200_000


def _layer_lines(layers: list[dict]) -> list[str]:
    """Probe layers for the system prompt (names + peel/decision code)."""
    lines = []
    for layer in layers:
        name = layer.get("name", "?")
        kind = layer.get("kind", "")
        module = layer.get("module") or ""
        env = layer.get("env") or ""
        head = f"- {name}: import {module}" if module else f"- {name}"
        if env:
            head += f" (in the {env} env)"
        lines.append(head)
        code = layer.get("code") or ""
        if code and kind in ("peeled", "decision"):
            if len(code) > 1500:
                code = code[:1500] + "\n# [... truncated]"
            lines.append(f"  {kind} preamble; run it before the import "
                         "to reveal what breaks underneath:")
            lines.append("  ```")
            lines.extend(f"  {ln}" for ln in code.splitlines())
            lines.append("  ```")
    return lines


def _build_system_prompt(*, decision: str, package: str, tenv: ToolEnv,
                         baseline: dict, fix_hints: list[str],
                         layers: list[dict]) -> str:
    n_items = len(tenv.changeset.items) if tenv.changeset else 0
    failed = sorted(baseline.get("failed") or [])
    envs = []
    if tenv.before_env:
        envs.append("before")
    if tenv.after_env:
        envs.append("after")
    if baseline:
        baseline_line = (f"Before-suite baseline: "
                         f"{baseline.get('passed', 0)} passed, "
                         f"{len(failed)} failed of "
                         f"{baseline.get('total', 0)}.")
    else:
        baseline_line = ("Before-suite baseline: none (no suite ran "
                         "before the decision).")
    lines = [
        "You investigate a dependency decision against a repo with "
        "deterministic tools. Rules:",
        "1. Only assert what a tool returned. Every finding cites the "
        "tool call ids it rests on.",
        "2. Prefer confirming probes over speculation: import breaks "
        "need a run_import_probe (or a peeled run_snippet that still "
        "fails), call breaks need run_tests, behavior changes need a "
        "run_snippet or run_tests confirmation. Claim confirmed only "
        "when a probe shows the failure itself (a failing import, "
        "call, or test) — a hasattr demonstration is likely, not "
        "confirmed.",
        "3. Triage in submit_findings: relevant only when the repo "
        "actually uses the symbol (strings and comments don't count). "
        "One propagate call over all symbols grounds the long tail; "
        "spend find_usages on the flagged ones.",
        "4. Method (25 tool calls, 8 probes — spend them on "
        "confirming, not enumerating): list_changes once to see the "
        "set; then ONE propagate call with every candidate symbol — "
        "its per-symbol hit counts tell you what is used; then "
        "find_usages/trace_importers/callers_of only for symbols "
        "with hits; then probes for the important ones; then "
        "submit_findings. Do not call find_usages for every symbol: "
        "with dozens of items that blows the budget before you probe "
        "anything. Batch at most 8 calls per turn; bigger batches "
        "are cut off and you must re-issue the rest.",
        "5. Peel import layers: when a layer's import probe fails, "
        "run the next layer's preamble (as a run_snippet, then the "
        "import in the same snippet) to reveal what breaks "
        "underneath. Confirm every layer below.",
        f"Decision: {decision}",
        f"Package: {package} ({n_items} change items)",
        f"Probe envs available: {', '.join(envs) or 'none'}",
        baseline_line,
        "Probe layers:",
        *_layer_lines(layers),
    ]
    if failed:
        lines.append("Baseline failures: " + ", ".join(failed[:20])
                     + (" ..." if len(failed) > 20 else ""))
    if fix_hints:
        lines.append("Fix order hints: " + ", ".join(fix_hints[:20])
                     + (" ..." if len(fix_hints) > 20 else ""))
    if tenv.testmap is None:
        lines.append("No coverage map in this run: tests_covering is "
                     "unavailable.")
    return "\n".join(lines)


def _build_first_user(*, package: str, tenv: ToolEnv) -> str:
    n_items = len(tenv.changeset.items) if tenv.changeset else 0
    return (f"Investigate the decision on {package}: triage the "
            f"{n_items} change items, confirm what breaks with probes, "
            "and call submit_findings with your findings.")


def _render_turn(n: int, content: str, calls: list,
                 results: list[dict]) -> str:
    lines = [f"--- turn {n} ---",
             f"model: {content or '(no text)'}"]
    for call, result in zip(calls, results):
        lines.append(f"tool {call['id']} {call['name']} "
                     f"{json.dumps(call['args'], sort_keys=True)} -> "
                     f"{json.dumps(result, sort_keys=True)}")
    return "\n".join(lines)


def _usage_tokens(usage: dict | None) -> int:
    if not usage:
        return 0
    total = usage.get("total_tokens")
    if isinstance(total, (int, float)):
        return int(total)
    prompt = usage.get("prompt_tokens") or 0
    completion = usage.get("completion_tokens") or 0
    if isinstance(prompt, (int, float)) \
            and isinstance(completion, (int, float)):
        return int(prompt + completion)
    return 0


def _validate_submit(args: Any) -> str | None:
    """Structural check on submit_findings arguments; None means valid."""
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    for key in ("findings", "triage", "summary"):
        if key not in args:
            return f"missing required key: {key}"
    if not isinstance(args["findings"], list) \
            or not isinstance(args["triage"], list) \
            or not isinstance(args["summary"], str):
        return "findings/triage must be lists, summary a string"
    seen_ids: set[str] = set()
    for i, finding in enumerate(args["findings"]):
        if not isinstance(finding, dict):
            return f"findings[{i}] must be an object"
        for key in _FINDING_KEYS:
            if key not in finding:
                return f"findings[{i}] is missing {key}"
        if finding["kind"] not in _FINDING_KINDS:
            return f"findings[{i}] has bad kind {finding['kind']!r}"
        if finding["confidence"] not in _CONFIDENCES:
            return (f"findings[{i}] has bad confidence "
                    f"{finding['confidence']!r}")
        for key in ("sites", "tests", "evidence"):
            if not isinstance(finding[key], list):
                return f"findings[{i}].{key} must be a list"
        if finding["id"] in seen_ids:
            return f"duplicate finding id {finding['id']!r}"
        seen_ids.add(finding["id"])
    for i, entry in enumerate(args["triage"]):
        if not isinstance(entry, dict):
            return f"triage[{i}] must be an object"
        for key in _TRIAGE_KEYS:
            if key not in entry:
                return f"triage[{i}] is missing {key}"
        if not isinstance(entry["relevant"], bool) \
                or not isinstance(entry["evidence"], list):
            return f"triage[{i}] has bad relevant/evidence types"
    return None


def _select_runnable(calls: list) -> tuple[list, list]:
    """(runnable, capped): at most 8 non-submit calls run per turn."""
    runnable, capped = [], []
    others = 0
    for call in calls:
        if call["name"] == "submit_findings":
            runnable.append(call)
        elif others < _MAX_CALLS_PER_TURN:
            runnable.append(call)
            others += 1
        else:
            capped.append(call)
    return runnable, capped


def _budget_hit(budgets: LoopBudgets, tool_calls: int, probe_calls: int,
                tokens: int, elapsed: float) -> str | None:
    if tool_calls >= budgets.max_tool_calls:
        return "tool calls"
    if probe_calls >= budgets.max_probe_calls:
        return "probe calls"
    if tokens >= budgets.max_tokens:
        return "tokens"
    if elapsed >= budgets.wall_seconds:
        return "wall time"
    return None


def run_investigation(ctx, tenv: ToolEnv, *, decision: str,
                      baseline: dict | None = None,
                      fix_hints: list[str] | None = None,
                      layers: list[dict] | None = None,
                      budgets: LoopBudgets | None = None) -> dict:
    """Run the tool-calling loop; return the investigation content.

    Complete means the model called submit_findings with valid
    arguments. Anything else (nudged twice, budget without a submit,
    live model error) is incomplete, and the run continues on the
    static prediction. Replay re-raises recording failures instead
    of converting them: a missing turn is a ReplayMiss.
    """
    budgets = budgets or LoopBudgets()
    baseline = baseline or {}
    fix_hints = fix_hints or []
    layers = layers or []
    system = _build_system_prompt(decision=decision, package=tenv.package,
                                  tenv=tenv, baseline=baseline,
                                  fix_hints=fix_hints, layers=layers)
    history = [_build_first_user(package=tenv.package, tenv=tenv)]
    trace: list[dict] = []
    tool_calls = 0
    probe_calls = 0
    tokens = 0
    nudged = False
    forced = False
    t0 = time.monotonic()
    n_turns = 0
    while True:
        hit = _budget_hit(budgets, tool_calls, probe_calls, tokens,
                          time.monotonic() - t0)
        if hit is not None and not forced:
            forced = True
            history.append(f"Budget exhausted ({hit}). {_NUDGE}")
        user = "\n".join(history)
        n_turns += 1
        try:
            turn = ctx.chat_turn(system, user, max_tokens=8000,
                                 reasoning_effort="high", tools=TOOLS,
                                 tool_choice="auto")
        except Exception as exc:
            if ctx.mode == "replay":
                raise
            if isinstance(exc, Cancelled):
                raise
            return {"status": "incomplete",
                    "reason": f"model turn failed: {exc}",
                    "findings": [], "triage": [], "summary": "",
                    "trace": trace,
                    "usage": _usage(tool_calls, probe_calls, tokens, n_turns)}
        tokens += _usage_tokens(turn.usage)
        ctx.emit("log", {"message": f"investigate: model turn {n_turns} "
                                    f"({len(turn.tool_calls or [])} tool "
                                    f"calls)"})
        calls = []
        for i, raw in enumerate(turn.tool_calls or []):
            # Short sequential ids: the model cites these in findings,
            # and UUIDs invite chimera typos. The model's raw id stays
            # on the trace entry for audit. Order is deterministic, so
            # replay assigns identical ids.
            calls.append({"id": f"call_{len(trace) + i + 1}",
                          "model_id": raw.get("id"),
                          "name": raw.get("name"),
                          "args": raw.get("arguments")})
        if not calls:
            if not nudged and not forced:
                nudged = True
                history.append(f"model: {turn.content or '(no text)'}")
                history.append(_NUDGE)
                continue
            return {"status": "incomplete",
                    "reason": "model did not call submit_findings",
                    "findings": [], "triage": [], "summary": "",
                    "trace": trace,
                    "usage": _usage(tool_calls, probe_calls, tokens, n_turns)}
        if forced:
            # The final turn only honors a submit_findings; anything
            # else in the batch is dropped, and no submit ends it.
            submits = [c for c in calls
                       if c["name"] == "submit_findings"]
            if not submits:
                return {"status": "incomplete",
                        "reason": "budget exhausted without findings",
                        "findings": [], "triage": [], "summary": "",
                        "trace": trace,
                        "usage": _usage(tool_calls, probe_calls, tokens, n_turns)}
            calls = submits[:1]
            history.append("budget exhausted: only the submit_findings "
                           "call runs; the rest of the batch is dropped.")
        else:
            calls, capped = _select_runnable(calls)
            if capped:
                history.append(
                    "dropped over the 8-per-turn cap (not run, not "
                    "counted; re-issue next turn): "
                    + ", ".join(f"{c['id']} {c['name']}" for c in capped))
        results = []
        for pos, call in enumerate(calls):
            if call["name"] != "submit_findings" \
                    and tool_calls >= budgets.max_tool_calls:
                rest = calls[pos:]
                history.append(
                    "tool budget reached mid-turn; not run: "
                    + ", ".join(f"{c['id']} {c['name']}" for c in rest)
                    + ". The next turn is submit-only.")
                break
            tool_calls += 1
            if call["name"] in _PROBE_CALL_NAMES:
                probe_calls += 1
            ctx.emit("tool_call", {"id": call["id"], "name": call["name"],
                                   "args": call["args"]})
            if call["name"] == "submit_findings":
                problem = _validate_submit(call["args"])
                if problem is None:
                    result = {"ok": True,
                              "summary": "findings accepted"}
                else:
                    result = {"ok": False, "summary": problem,
                              "error": problem, "truncated": False}
            elif not isinstance(call["args"], dict):
                result = {"ok": False,
                          "summary": "arguments must be a JSON object",
                          "error": "arguments must be a JSON object",
                          "truncated": False}
            else:
                result = run_tool(tenv, call["name"], call["args"])
            trace.append({"id": call["id"], "model_id": call["model_id"],
                          "name": call["name"], "args": call["args"],
                          "result": result})
            ctx.emit("tool_result", {"id": call["id"],
                                     "summary": result.get("summary", ""),
                                     "ok": result.get("ok", False),
                                     "truncated": result.get("truncated",
                                                             False)})
            results.append(result)
            if call["name"] == "submit_findings" and result["ok"]:
                # Calls after a submit in the same batch are dropped:
                # the loop ends here.
                ctx.emit("artifact", {
                    "name": "investigation.json",
                    "findings": len(call["args"]["findings"]),
                    "kinds": _kind_counts(call["args"]["findings"])})
                return {"status": "complete",
                        "findings": call["args"]["findings"],
                        "triage": call["args"]["triage"],
                        "summary": call["args"]["summary"],
                        "trace": trace,
                        "usage": _usage(tool_calls, probe_calls, tokens, n_turns)}
        if forced:
            # The final turn did not produce a valid submit.
            return {"status": "incomplete",
                    "reason": "budget exhausted without findings",
                    "findings": [], "triage": [], "summary": "",
                    "trace": trace,
                    "usage": _usage(tool_calls, probe_calls, tokens, n_turns)}
        history.append(_render_turn(n_turns, turn.content, calls, results))


def _usage(tool_calls: int, probe_calls: int, tokens: int, turns: int
           ) -> dict:
    # No wall time: the artifact must be replay-identical, and seconds
    # always differ. Durations live in the event stream only.
    return {"tool_calls": tool_calls, "probe_calls": probe_calls,
            "tokens": tokens, "turns": turns}


def _kind_counts(findings: list) -> dict:
    counts: dict[str, int] = {}
    for finding in findings:
        kind = finding.get("kind", "?")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


# -- findings verification (deterministic checker) --

def _cited(trace_by_id: dict, evidence: list) -> tuple[list[dict], list[str]]:
    """Trace entries for evidence ids + ids missing from the trace."""
    entries = []
    missing = []
    for cid in evidence:
        if isinstance(cid, str) and cid in trace_by_id:
            entries.append(trace_by_id[cid])
        else:
            missing.append(cid)
    return entries, missing


def _result_text(entry: dict) -> str:
    return json.dumps(entry.get("result") or {}, sort_keys=True)


def _test_passes_in(result: dict, tid: str) -> bool:
    for row in result.get("results") or []:
        if row.get("id") == tid:
            return row.get("outcome") == "pass"
    return False


def _run_tests_has_failure(result: dict) -> bool:
    return any(row.get("outcome") in ("fail", "error")
               for row in result.get("results") or [])


def _run_tests_all_pass(result: dict) -> bool:
    rows = result.get("results") or []
    return bool(rows) and all(row.get("outcome") == "pass" for row in rows)


def _confirmed_by(finding: dict, entries: list[dict]) -> bool:
    """A cited probe/test/snippet executed with a kind-matching outcome.

    Tool-level failures (a string "error": rejected snippets,
    missing envs) never confirm; note run_import_probe's "error" is
    the traceback frame dict, which does confirm. behavior_change
    and dependency_conflict can only be weakly checked — the checker
    sees that a probe ran, not what the behavior means — so any
    executed probe confirms them; breaks_* and not_affected need
    the matching outcome.
    """
    kind = finding.get("kind")
    tests = finding.get("tests") or []
    for entry in entries:
        name = entry.get("name")
        result = entry.get("result") or {}
        if not isinstance(result, dict) \
                or isinstance(result.get("error"), str):
            continue
        if name == "run_import_probe":
            if kind == "breaks_import" and result.get("ok") is False:
                return True
            if kind == "not_affected" and result.get("ok") is True:
                return True
        elif name == "run_tests" and result.get("ok") is True:
            if kind == "breaks_call" and _run_tests_has_failure(result):
                return True
            if kind == "not_affected":
                if tests and all(_test_passes_in(result, t) for t in tests):
                    return True
                if not tests and _run_tests_all_pass(result):
                    return True
            if kind in ("behavior_change", "dependency_conflict"):
                return True
        elif name == "run_snippet":
            if kind == "breaks_call" and result.get("ok") is False:
                return True
            if kind == "breaks_import" and result.get("ok") is False \
                    and result.get("chain"):
                # A peeled snippet that still fails to import (the
                # traceback names repo modules) confirms the next layer.
                return True
            if kind == "not_affected" and result.get("ok") is True:
                return True
            if kind in ("behavior_change", "dependency_conflict") \
                    and result.get("ok") is True:
                return True
    return False


def verify_investigation(inv: dict) -> dict:
    """Mark each finding/triage entry verified or not (kept either way).

    Pure function of the investigation content: evidence ids must
    exist, sites must appear in a cited result, tests in a cited
    covering/test result, and confirmed findings need a kind-matching
    probe outcome. Reasons join with "; " in check order.
    """
    trace_by_id = {t.get("id"): t for t in inv.get("trace") or []
                   if isinstance(t.get("id"), str)}
    out = dict(inv)
    findings = []
    for finding in inv.get("findings") or []:
        reasons: list[str] = []
        finding = dict(finding)
        kind = finding.get("kind")
        if kind not in _FINDING_KINDS:
            reasons.append(f"unknown kind {kind!r}")
        if finding.get("confidence") not in _CONFIDENCES:
            reasons.append(
                f"unknown confidence {finding.get('confidence')!r}")
        evidence = finding.get("evidence") or []
        entries, missing = _cited(trace_by_id, evidence)
        for cid in missing:
            reasons.append(f"evidence {cid!r} is not in the tool trace")
        texts = [_result_text(e) for e in entries]
        for site in finding.get("sites") or []:
            if not any(site in text for text in texts):
                reasons.append(f"site {site} is in no cited tool result")
        covering = [e for e in entries
                    if e.get("name") in ("tests_covering", "run_tests")]
        cover_texts = [_result_text(e) for e in covering]
        for tid in finding.get("tests") or []:
            if not any(tid in text for text in cover_texts):
                reasons.append(f"test {tid} is in no cited covering/test "
                               "result")
        if finding.get("confidence") == "confirmed" \
                and not _confirmed_by(finding, entries):
            reasons.append(f"confirmed {kind} has no cited probe/test/"
                           "snippet result with a matching outcome")
        finding["verified"] = not reasons
        finding["verify_reason"] = "; ".join(reasons)
        findings.append(finding)
    out["findings"] = findings
    triage = []
    for entry in inv.get("triage") or []:
        entry = dict(entry)
        _, missing = _cited(trace_by_id, entry.get("evidence") or [])
        entry["verified"] = not missing
        entry["verify_reason"] = "; ".join(
            f"evidence {cid!r} is not in the tool trace" for cid in missing)
        triage.append(entry)
    out["triage"] = triage
    return out
