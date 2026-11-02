"""Canonicalization for everything written under runs/.

Invariant: bytes on disk contain no secrets, no absolute user paths,
and no platform-specific separators — so report.json is byte-identical
across operating systems and runs/presets/ is safe to commit. Gateways
return exactly the scrubbed bytes they record, so live and replay
stages see identical inputs.
"""

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
KEY_ENV = "MODEL_API_KEY"

ROOT_PLACEHOLDER = "<root>"
HOME_PLACEHOLDER = "<home>"
TMP_PLACEHOLDER = "<tmp>"
KEY_PLACEHOLDER = "<redacted-key>"

# Environment names created by scripts/make_target_envs.py. Recognized
# even when the envs were never built here (judges replay without them)
# and even under a different checkout root (symlinks, other machines).
KNOWN_ENVS = ("numpy1", "numpy2", "sponsorfit")


def env_placeholder(name: str) -> str:
    return f"<env:{name}>"


def canonical_json(obj) -> str:
    """Pretty canonical JSON (manifests, artifacts)."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)


def canonical_line(obj) -> str:
    """Single-line canonical JSON (JSONL records)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _loose_pattern(needle: str):
    # Match the path regardless of separator mix (E:\a/b matches E:/a\b).
    return re.compile("".join(
        r"[\\/]" if ch in "\\/" else re.escape(ch) for ch in needle))


def _replace_path_token(value: str, needle: str, repl: str) -> str:
    """Swap an absolute prefix for its placeholder, forward-slashing the
    rest of the same path token (up to whitespace/quote). Backslashes
    outside replaced paths (model JSON escapes, regexes) stay intact."""
    out = []
    i = 0
    for m in _loose_pattern(needle).finditer(value):
        if m.start() < i:
            continue
        out.append(value[i:m.start()])
        k = m.end()
        while k < len(value) and value[k] not in " \t\n\r\"'":
            k += 1
        out.append(repl + value[m.end():k].replace("\\", "/"))
        i = k
    out.append(value[i:])
    return "".join(out)


def _replace_env_token(value: str, name: str) -> str:
    """Rewrite any `<abs>/envs/<name>/...` span to `<env:name>/...`.

    Catches environment paths under a different checkout root than ours
    (symlinks, other machines) that the absolute needles miss. Bare
    relative `envs/<name>` references are left alone.
    """
    rx = re.compile(r"[^\s\"']*?[\\/]envs[\\/]" + re.escape(name)
                    + r"(?=[\\/]|$)")
    out = []
    i = 0
    for m in rx.finditer(value):
        if m.start() < i:
            continue
        out.append(value[i:m.start()])
        k = m.end()
        while k < len(value) and value[k] not in " \t\n\r\"'":
            k += 1
        out.append(env_placeholder(name)
                   + value[m.end():k].replace("\\", "/"))
        i = k
    out.append(value[i:])
    return "".join(out)


# Windows `Lib/site-packages` vs Linux/Mac `lib/python3.x/site-packages`
# (or bare `lib/site-packages`): collapse all so recorded tracebacks are
# byte-identical across OSes. `/site-packages` sits outside the
# alternation: matching bare `Lib/` would duplicate it on Windows.
_SITE_PACKAGES_RX = re.compile(
    r"(<env:[^>]+>)/(?:Lib|lib(?:/python3\.\d+)?)/site-packages/")


def utc_now_iso() -> str:
    return (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            + "Z")


@dataclass(frozen=True)
class Scrubber:
    replacements: tuple  # ((needle, placeholder), ...) longest needle first

    @classmethod
    def default(cls) -> "Scrubber":
        pairs = []
        envs = ROOT / "envs"
        try:
            names = sorted(p.name for p in envs.iterdir() if p.is_dir())
        except OSError:
            names = []
        for name in names:
            pairs.append((str(envs / name), env_placeholder(name)))
        pairs.append((str(ROOT), ROOT_PLACEHOLDER))
        try:
            home = os.path.expanduser("~")
        except Exception:  # pragma: no cover - expanduser rarely fails
            home = ""
        if home:
            pairs.append((home, HOME_PLACEHOLDER))
        try:
            tmp = tempfile.gettempdir()
        except Exception:  # pragma: no cover - gettempdir rarely fails
            tmp = ""
        if tmp:
            pairs.append((tmp, TMP_PLACEHOLDER))
        # Longest first so nested roots win (envs/ lives under the
        # repo root; uv pythons under home). Matching is separator-
        # insensitive, so mixed E:\a/b spellings match too.
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        return cls(tuple(pairs))

    def text(self, value: str) -> str:
        if not isinstance(value, str) or not value:
            return value
        key = os.environ.get(KEY_ENV, "")
        if key and key in value:
            value = value.replace(key, KEY_PLACEHOLDER)
        for needle, repl in self.replacements:
            if needle:
                value = _replace_path_token(value, needle, repl)
        for name in KNOWN_ENVS:
            value = _replace_env_token(value, name)
        value = _SITE_PACKAGES_RX.sub(r"\1/site-packages/", value)
        return value.replace("\r\n", "\n").replace("\r", "\n")

    def untext(self, value: str) -> str:
        """Restore live absolute paths from placeholders.

        Lets stages parse recorded text that names files (tracebacks)
        identically on every machine: placeholders map back to the
        local paths. Key redaction is one-way and stays redacted.
        """
        if not isinstance(value, str) or not value:
            return value
        for needle, repl in sorted(self.replacements,
                                   key=lambda p: len(p[1]), reverse=True):
            if repl and repl != KEY_PLACEHOLDER and repl in value:
                value = value.replace(repl, needle)
        return value

    def obj(self, node):
        if isinstance(node, str):
            return self.text(node)
        if isinstance(node, dict):
            return {key: self.obj(val) for key, val in node.items()}
        if isinstance(node, (list, tuple)):
            return [self.obj(val) for val in node]
        return node
