"""M3 source discovery + multi-release collection (spec §1-§2).

discover_sources() walks the spec's ordered steps — request URL, Meta
search grounding, PyPI project links, GitHub Releases, raw repo files —
and collect_release_notes() assembles per-release notes for every
release in (from_version, to_version].

Every network call goes through the run context gateways, so replay
reproduces the same sources offline. HTTP failures are recorded with
their status (ctx.fetch never raises them); only network errors fall
through to the next candidate, and every attempt lands in `tried`.
"""

import json
import re
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser

from packaging.version import InvalidVersion, Version

from chainreaction.generic import GuideNotFound
from chainreaction.runs.context import Cancelled, ReplayMiss

MIN_USABLE_CHARS = 200
MAX_SEARCH_FETCHES = 5

PYPI_JSON = "https://pypi.org/pypi/{package}/json"
GITHUB_RELEASES_API = "https://api.github.com/repos/{org}/{repo}/releases"
GITHUB_RAW = "https://raw.githubusercontent.com/{org}/{repo}/{tag}/{name}"

CHANGELOG_NAMES = (
    "CHANGELOG.md", "CHANGELOG.rst", "CHANGES.md", "CHANGES.rst",
    "HISTORY.md", "HISTORY.rst", "NEWS.md", "NEWS.rst",
    "docs/changelog.md", "docs/changelog.rst", "docs/releases.md",
    "docs/whatsnew.md", "docs/whatsnew.rst",
)

_PROJECT_URL_KEYS = ("changelog", "changes", "release notes", "history",
                     "what's new", "whats new", "migration")

_FETCH_ERRORS = (urllib.error.URLError, OSError)


@dataclass
class Source:
    url: str
    found_via: str  # guide_url|search|pypi_project_urls|github_releases|
    # repo_file|pypi_meta
    bytes: int
    sha256: str
    used: bool = False
    clean: str = "text"
    note: str = ""


@dataclass
class ReleaseNotes:
    version: str  # PyPI release key; becomes items' introduced_in
    source_url: str
    text: str


@dataclass
class Discovery:
    package: str
    from_version: str | None
    to_version: str
    sources: list = field(default_factory=list)  # [Source] attempted
    tried: list = field(default_factory=list)  # human-readable attempts
    pypi: dict = field(default_factory=dict)
    github_releases: list = field(default_factory=list)
    texts: dict = field(default_factory=dict)  # url -> structured text
    raw: dict = field(default_factory=dict)  # url -> raw body (HTML only)
    guide_url: str | None = None


# -- structured text --

_HTML_RE = re.compile(
    r"\s*<(?:!doctype|html|head|body|div|article|main|section|nav)\b", re.I)


class _StructuredExtractor(HTMLParser):
    """HTML → lines: headings as ## lines, items as bullets, prose split."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.skip = 0
        self.nolink = 0
        self._code: list[str] | None = None
        self._heading: list[str] | None = None

    def _emit(self, text: str) -> None:
        if self._code is not None:
            self._code.append(text)
        elif self._heading is not None:
            self._heading.append(text)
        else:
            self.chunks.append(text)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "a" and "headerlink" in dict(attrs).get("class", ""):
            # Sphinx "¶" permalinks: not quotable content.
            self.nolink += 1
        elif self.skip or self.nolink:
            pass
        elif tag == "code" and self._code is None:
            # Code spans keep backticks so literal extraction works on
            # rendered HTML exactly as on Markdown sources.
            self._code = []
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading = []
        elif tag in ("p", "div", "section", "article", "table", "tr",
                     "blockquote", "pre"):
            self.chunks.append("\n\n")
        elif tag in ("br", "li", "dt", "dd"):
            self.chunks.append("\n- " if tag == "li" else "\n")
        elif tag in ("td", "th"):
            self.chunks.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag == "a" and self.nolink:
            self.nolink = max(0, self.nolink - 1)
        elif tag == "code" and self._code is not None:
            text = "".join(self._code).strip()
            self._code = None
            if text:
                self._emit(f"`{text}`")
        elif self.skip or self.nolink:
            pass
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and \
                self._heading is not None:
            text = "".join(self._heading).strip()
            self._heading = None
            if text:
                self.chunks.append(f"\n\n## {text}\n\n")
        elif tag in ("p", "div", "section", "article", "table", "tr"):
            self.chunks.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self.skip or self.nolink:
            return
        self._emit(data)


def html_to_structured_text(page: str) -> str:
    """Clean HTML to quotable multi-line text (headings stay on lines)."""
    parser = _StructuredExtractor()
    parser.feed(page)
    lines = [line.rstrip() for line in "".join(parser.chunks).split("\n")]
    collapsed: list[str] = []
    blanks = 0
    for line in lines:
        if line.strip():
            blanks = 0
            collapsed.append(line.strip())
        else:
            blanks += 1
            if blanks <= 1:
                collapsed.append("")
    return "\n".join(collapsed).strip()


def coerce_text(body: str) -> str:
    """Decoded body → structured text (HTML cleaned, markdown passes)."""
    if _HTML_RE.match(body or ""):
        return html_to_structured_text(body)
    return (body or "").replace("\r\n", "\n").replace("\r", "\n")


# -- versions --

def _norm_version(raw) -> str | None:
    try:
        return str(Version(str(raw or "").strip().lstrip("vV")))
    except InvalidVersion:
        return None


def _versions_equal(left, right) -> bool:
    try:
        return Version(str(left)) == Version(str(right).lstrip("vV"))
    except InvalidVersion:
        return False


def _wanted_releases(discovery: Discovery) -> list[str]:
    """PyPI release keys in (from_version, to_version], ascending."""
    keys = list((discovery.pypi.get("releases") or {}).keys())
    if not keys:
        ver = _norm_version(discovery.to_version)
        return [ver] if ver else []
    try:
        to_ver = Version(discovery.to_version)
    except InvalidVersion:
        return []
    try:
        from_ver = (Version(discovery.from_version)
                    if discovery.from_version else None)
    except InvalidVersion:
        from_ver = None
    ranked = []
    for key in keys:
        try:
            ver = Version(key)
        except InvalidVersion:
            continue
        if ver.is_prerelease and not to_ver.is_prerelease:
            continue
        if from_ver is not None and ver <= from_ver:
            continue
        if ver > to_ver:
            continue
        ranked.append((ver, key))
    ranked.sort()
    return [key for _, key in ranked]


_HEADING_RE = re.compile(
    r"^(?:version|release)?\s*\[?v?(\d+\.\d[\w.\-+]*)", re.I)
_HEADING_REST_RE = re.compile(r"^[\]\)\(\-–—:\.]")


def _heading_version(line: str):
    """packaging Version when the line is a version heading, else None."""
    text = line.strip().strip("#").strip().strip("*").strip()
    text = text.rstrip("¶#").strip()
    match = _HEADING_RE.match(text)
    if not match:
        return None
    rest = text[match.end():].strip()
    if rest and not _HEADING_REST_RE.match(rest):
        return None
    try:
        return Version(match.group(1))
    except InvalidVersion:
        return None


def _version_headings(text: str) -> list:
    """(line index, Version) for every version heading (any version)."""
    lines = text.splitlines()
    bounds = []
    for i, line in enumerate(lines):
        # A version-like line is a heading on its own; an RST underline
        # below only confirms it (same parse either way).
        ver = _heading_version(line)
        if ver is not None:
            bounds.append((i, ver))
    return bounds


def split_changelog(text: str, wanted: list[str]) -> dict:
    """Split one changelog into {wanted release key: section text}."""
    targets = []
    for key in wanted:
        try:
            targets.append((Version(key), key))
        except InvalidVersion:
            continue
    lines = text.splitlines()
    bounds = _version_headings(text)
    sections: dict[str, str] = {}
    for pos, (index, ver) in enumerate(bounds):
        end = bounds[pos + 1][0] if pos + 1 < len(bounds) else len(lines)
        for target_ver, key in targets:
            if ver == target_ver and key not in sections:
                sections[key] = "\n".join(lines[index:end]).strip()
    return sections


def _covered_versions(found: Discovery, wanted: list[str]) -> set:
    """Wanted releases with notes from the discovery so far."""
    covered = set()
    for entry in found.github_releases:
        if not _noted(entry):
            continue
        for key in wanted:
            if _versions_equal(key, entry.get("tag")):
                covered.add(key)
    for url, text in found.texts.items():
        if url == found.guide_url:
            continue
        covered.update(split_changelog(text, wanted))
    return covered


def _missing_versions(found: Discovery) -> list[str]:
    """Wanted releases still without notes (a guide covers the jump)."""
    if found.guide_url:
        return []
    wanted = _wanted_releases(found)
    covered = _covered_versions(found, wanted)
    return [key for key in wanted if key not in covered]


# -- discovery --

def _package_hosts(pypi: dict) -> tuple[set, set]:
    """(hosts, github orgs) from PyPI home/project URLs."""
    info = pypi.get("info") or {}
    urls = [info.get("home_page") or ""]
    urls += list((info.get("project_urls") or {}).values())
    hosts, orgs = set(), set()
    for url in urls:
        if not isinstance(url, str):
            continue
        try:
            parsed = urllib.parse.urlparse(url)
        except ValueError:
            continue
        host = (parsed.hostname or "").lower()
        if not host:
            continue
        hosts.add(host)
        parts = parsed.path.strip("/").split("/")
        if host == "github.com" and len(parts) >= 2:
            orgs.add(parts[0].lower())
    return hosts, orgs


def _on_domain(url: str, hosts: set, orgs: set) -> bool:
    """True when the URL is on the package's own domains (§1 step 2)."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in ("github.com", "raw.githubusercontent.com"):
        # Host membership is not enough here: the org must match.
        parts = parsed.path.strip("/").split("/")
        return bool(parts) and parts[0].lower() in orgs
    return host in hosts or any(host.endswith("." + h) for h in hosts)


def _github_repo(pypi: dict):
    """(org, repo) from PyPI links, or None."""
    info = pypi.get("info") or {}
    urls = [info.get("home_page") or ""]
    urls += list((info.get("project_urls") or {}).values())
    for url in urls:
        if not isinstance(url, str):
            continue
        try:
            parsed = urllib.parse.urlparse(url)
        except ValueError:
            continue
        if (parsed.hostname or "").lower() != "github.com":
            continue
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
    return None


def _noted(entry: dict) -> bool:
    """True when a GitHub release entry carries usable notes."""
    return len((entry.get("body") or "").strip()) >= MIN_USABLE_CHARS


def _parse_releases(text: str) -> list:
    """GitHub Releases API body → [{tag, url, body}]."""
    try:
        entries = json.loads(text)
    except ValueError:
        return []
    if not isinstance(entries, list):
        return []
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        tag = entry.get("tag_name") or ""
        if tag:
            out.append({"tag": tag, "url": entry.get("html_url") or "",
                        "body": entry.get("body") or ""})
    return out


def discover_sources(ctx, package: str, from_version: str | None,
                     to_version: str,
                     guide_url: str | None = None) -> Discovery:
    """Walk §1 steps 1–5; raise GuideNotFound listing what was tried.

    Steps run in order, but discovery only stops early when every
    wanted release has notes: a multi-release jump whose notes span
    several pages (e.g. per-series changelogs) continues to the next
    step for the missing releases. Steps exhausted with partial
    coverage return what was found (missing versions noted in
    `tried`); only a total miss raises.
    """
    found = Discovery(package=package, from_version=from_version,
                      to_version=to_version)

    def record(url: str, via: str, fetched, *, clean: str,
               note: str = "") -> Source:
        src = Source(url=url, found_via=via,
                     bytes=getattr(fetched, "bytes", 0) or 0,
                     sha256=getattr(fetched, "sha256", "") or "",
                     clean=clean, note=note)
        found.sources.append(src)
        return src

    def attempt(url: str, via: str):
        try:
            fetched = ctx.fetch(url, timeout=60, clean="text")
        except _FETCH_ERRORS as exc:
            found.tried.append(f"{via}: {url} ({exc.__class__.__name__})")
            record(url, via, None, clean="text", note="network error")
            return None
        src = record(url, via, fetched, clean="text")
        raw_body = str(fetched)
        body = coerce_text(raw_body)
        if fetched.status == 200 and len(body.strip()) >= MIN_USABLE_CHARS:
            found.texts[url] = body
            if _HTML_RE.match(raw_body or ""):
                found.raw[url] = raw_body
            ctx.emit("log", {"message":
                             f"interpret: source found ({via}): {url}"})
            return body
        src.note = (f"unusable (status {fetched.status}, "
                    f"{len(body.strip())} chars)")
        found.tried.append(f"{via}: {url} (status {fetched.status}, "
                           f"{len(body.strip())} chars)")
        return None

    # PyPI metadata first: §2's release list plus step 2's domain filter.
    meta_url = PYPI_JSON.format(package=package)
    try:
        meta = ctx.fetch(meta_url, timeout=60, clean="text")
    except _FETCH_ERRORS as exc:
        found.tried.append(f"pypi meta: {meta_url} ({exc.__class__.__name__})")
        record(meta_url, "pypi_meta", None, clean="text", note="network error")
    else:
        meta_src = record(meta_url, "pypi_meta", meta, clean="text")
        if meta.status == 200:
            try:
                found.pypi = json.loads(str(meta))
            except ValueError:
                found.tried.append("pypi meta: unparseable JSON")
            else:
                meta_src.used = True
        else:
            found.tried.append(f"pypi meta: status {meta.status}")

    # Step 1: request guide_url (raw body recorded; quotes use the
    # structured text, tables use the raw HTML via Discovery.raw).
    if guide_url:
        if attempt(guide_url, "guide_url") is not None:
            found.guide_url = guide_url
            return found

    # Step 2: Meta search grounding, kept to the package's own domains.
    hosts, orgs = _package_hosts(found.pypi)
    query = (f"official migration guide OR changelog {package} "
             f"{to_version}")
    try:
        result = ctx.web_search(query)
    except Exception as exc:
        if isinstance(exc, (Cancelled, ReplayMiss)):
            raise
        found.tried.append(f"search: {exc.__class__.__name__}: {exc}")
    else:
        found.tried.append(f"search: {len(result.citations)} citation(s)")
        before = len(found.texts)
        seen, fetched = set(), 0
        for citation in result.citations:
            if citation.url in seen or fetched >= MAX_SEARCH_FETCHES:
                continue
            seen.add(citation.url)
            if not _on_domain(citation.url, hosts, orgs):
                found.tried.append(f"search: skip off-domain {citation.url}")
                continue
            fetched += 1
            attempt(citation.url, "search")
        if len(found.texts) > before and not _missing_versions(found):
            return found

    # Step 3: PyPI project links.
    info = found.pypi.get("info") or {}
    project_urls = info.get("project_urls") or {}
    before = len(found.texts)
    for key, url in project_urls.items():
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        if any(k in key.lower() for k in _PROJECT_URL_KEYS):
            attempt(url, "pypi_project_urls")
    if len(found.texts) > before:
        if not _missing_versions(found):
            return found
    else:
        found.tried.append("pypi project links: no usable changelog link")

    # Step 4: GitHub Releases API.
    repo = _github_repo(found.pypi)
    if repo:
        org, name = repo
        api_url = GITHUB_RELEASES_API.format(org=org, repo=name)
        try:
            api = ctx.fetch(api_url, timeout=60, clean="text")
        except _FETCH_ERRORS as exc:
            found.tried.append(
                f"github releases: {api_url} ({exc.__class__.__name__})")
            record(api_url, "github_releases", None, clean="text",
                   note="network error")
        else:
            api_src = record(api_url, "github_releases", api, clean="text")
            entries = (_parse_releases(str(api)) if api.status == 200
                       else [])
            noted = [e for e in entries if _noted(e)]
            if noted:
                api_src.used = True
                found.github_releases = entries
                found.tried.append(
                    f"github releases: {len(entries)} release(s), "
                    f"{len(noted)} with notes")
                ctx.emit("log", {"message":
                                 "interpret: source found "
                                 f"(github_releases): {api_url}"})
                if not _missing_versions(found):
                    return found
            else:
                api_src.note = f"unusable (status {api.status})"
                found.tried.append(f"github releases: {api_url} "
                                   f"(status {api.status})")
    else:
        found.tried.append("github releases: no GitHub repo in PyPI links")

    # Step 5: raw repo files at the to_version tag.
    if repo:
        org, repo_name = repo
        tags = [to_version] + ([f"v{to_version}"] if not
                               to_version.startswith("v") else [])
        for tag in tags:
            for fname in CHANGELOG_NAMES:
                attempt(GITHUB_RAW.format(org=org, repo=repo_name,
                                          tag=tag, name=fname),
                        "repo_file")
                if not _missing_versions(found):
                    return found
    else:
        found.tried.append("repo files: no GitHub repo in PyPI links")

    # Step 6: total miss raises; partial coverage returns what was found.
    if not found.texts and not found.github_releases:
        raise GuideNotFound(
            f"no changelog or migration guide found for {package} "
            f"{to_version}; tried: "
            f"{'; '.join(found.tried) or '(nothing)'}")
    missing = _missing_versions(found)
    if missing:
        found.tried.append(
            f"partial coverage: missing notes for {', '.join(missing)}")
    return found


# -- multi-release collection --

def collect_release_notes(ctx, discovery: Discovery) -> list[ReleaseNotes]:
    """Per-release notes for every wanted release with notes (§2).

    Per-release pages (GitHub) win over changelog sections; a request
    guide covers the jump as one document tagged with the newest
    wanted release. Marks consumed sources used.
    """
    wanted = _wanted_releases(discovery)
    notes: dict[str, ReleaseNotes] = {}
    api_url = next((s.url for s in discovery.sources
                    if s.found_via == "github_releases"), "")
    github_keys = set()
    for entry in discovery.github_releases:
        if not _noted(entry):
            continue
        targets = [key for key in wanted
                   if _versions_equal(key, entry.get("tag"))]
        for key in targets:
            if key not in notes:
                notes[key] = ReleaseNotes(
                    key, entry.get("url") or api_url,
                    coerce_text(entry.get("body") or ""))
                github_keys.add(key)
    if discovery.guide_url and discovery.guide_url in discovery.texts:
        key = wanted[-1] if wanted else (
            _norm_version(discovery.to_version) or discovery.to_version)
        notes[key] = ReleaseNotes(key, discovery.guide_url,
                                  discovery.texts[discovery.guide_url])
    for url, text in discovery.texts.items():
        if url == discovery.guide_url:
            continue
        for key, section in split_changelog(text, wanted).items():
            if key not in notes:
                notes[key] = ReleaseNotes(key, url, section)
    used_urls = {n.source_url for n in notes.values()}
    if github_keys and api_url:
        used_urls.add(api_url)
    if discovery.guide_url:
        used_urls.add(discovery.guide_url)
    for src in discovery.sources:
        if src.url in used_urls:
            src.used = True
    ordered = [notes[key] for key in wanted if key in notes]
    chain = " → ".join([discovery.from_version or "?"]
                       + [n.version for n in ordered])
    ctx.emit("log", {"message": f"interpret: releases covered: {chain}"})
    return ordered


def sources_record(discovery: Discovery) -> dict:
    """sources.json artifact content (§1)."""
    return {
        "package": discovery.package,
        "from_version": discovery.from_version,
        "to_version": discovery.to_version,
        "sources": [{"url": s.url, "found_via": s.found_via,
                     "bytes": s.bytes, "sha256": s.sha256,
                     "used": s.used, "clean": s.clean,
                     "note": s.note} for s in discovery.sources],
        "tried": list(discovery.tried),
    }
