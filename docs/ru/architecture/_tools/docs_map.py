#!/usr/bin/env python3
"""docs-map — link map for architecture docs + cross-link validator.

Scans ``docs/architecture/**/*.html``, collects anchors (``id=``) and links
(``<a href>``), builds a module-level connection graph and checks:

* broken anchors / missing target files  -> error  (exit 1)
* filename-case mismatches in hrefs      -> error  (exit 1; APFS would hide them)
* one-way cross-module links             -> warning (exit 0; --strict: error)
* deep-links whose target has no flash   -> warning (exit 0; --strict: error)
* miscolored xref links (class ≠ target) -> warning (exit 0; --strict: error)

The map is *generated* from the HTML, so it stays in sync with the docs by
construction. Hrefs are percent-decoded before resolution.

Output is **JSON by default** — this tool's primary consumer is tooling
(the ``vs-audit-module`` skill parses it). ``--human`` renders the pretty
text map instead; ``make docs-map`` wires that flag for human use.

Flags combine freely; ``--full`` and ``--strict`` apply to both formats:

* ``--human``  — pretty text instead of JSON
* ``--full``   — add per-file anchor listing (``anchors_by_file`` in JSON)
* ``--strict`` — escalate one-way links, duplicate ids, unflashable anchors
  and miscolored xref links to errors (exit 1)

Usage:
    python3 docs/architecture/_tools/docs_map.py             # JSON (default)
    python3 docs/architecture/_tools/docs_map.py --human     # pretty text
    python3 docs/architecture/_tools/docs_map.py --full      # JSON + anchors
    python3 docs/architecture/_tools/docs_map.py --strict    # gate on warnings too
    make docs-map                                            # = --human

Exit code is 1 when broken links/anchors exist (validation gate), 0 otherwise
— regardless of output format; ``ok`` in the JSON mirrors the same gate.
``--strict`` additionally fails on one-way links, duplicate ids, anchors whose
target element has no ``:target`` flash rule, and miscolored xref links (whose
class claims a different destination category than the link actually reaches).
Anchor-depth asymmetry — one side links to a section, the other only to the
module's front door — is reported as ``shallow``: always advisory, never
gates. stdlib-only, no dependencies.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from functools import cache, cached_property
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urldefrag

ARCH_ROOT = Path(__file__).resolve().parents[1]  # docs/architecture/
REPO_ROOT = ARCH_ROOT.parents[1]


@dataclass(frozen=True)
class Link:
    """One ``<a href>``: its target and the classes that style it — so the
    validator can check the xref hue matches where the link actually reaches."""

    href: str
    classes: frozenset[str]


class _DocParser(HTMLParser):
    """Collect ``id`` anchors and ``<a href>`` targets from one HTML file."""

    def __init__(self) -> None:
        super().__init__()
        self.anchors: set[str] = set()
        self.duplicate_ids: list[str] = []
        self.links: list[Link] = []
        # id -> (tag, frozenset(classes)) of the element bearing it; lets the
        # validator tell whether a deep-link target gets a :target flash.
        self.id_host: dict[str, tuple[str, frozenset[str]]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if anchor := attr.get("id"):
            if anchor in self.anchors:
                self.duplicate_ids.append(anchor)
            self.anchors.add(anchor)
            self.id_host[anchor] = (tag, frozenset((attr.get("class") or "").split()))
        if tag == "a" and (href := attr.get("href")):
            self.links.append(Link(href, frozenset((attr.get("class") or "").split())))


class Doc:
    """One parsed HTML doc: its path, anchors and outgoing links."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rel = path.relative_to(ARCH_ROOT)
        parser = _DocParser()
        parser.feed(path.read_text(encoding="utf-8"))
        parser.close()
        self.anchors = parser.anchors
        self.duplicate_ids = parser.duplicate_ids
        self.links = parser.links
        self.id_host = parser.id_host

    @cached_property
    def module(self) -> str:
        """Module a file belongs to. ``modules/<name>/...`` and the stub
        ``modules/<name>.html`` both map to ``<name>``; the hub and anything
        else map to ``(scheme)`` / ``(other)`` and are excluded from the graph."""
        parts = self.rel.parts
        if parts[0] == "modules":
            return parts[1].removesuffix(".html")
        if self.rel.name == "architecture-scheme.html":
            return "(scheme)"
        return "(other)"


def _is_external(url: str) -> bool:
    return url.startswith(("http://", "https://", "mailto:", "tel:"))


# A deep-link should visually land: clicking it flashes the target block. That
# flash is a CSS `<selector>:target` rule, so the docs' own CSS is the single
# source of truth for which elements highlight. We parse those rules into
# (tag, classes) matchers and check every anchor target against them — no
# hand-maintained allow-list to drift out of sync.
Matcher = tuple[str | None, frozenset[str]]

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_RULE_HEAD = re.compile(r"([^{}]+)\{")
_SEL_CLASS = re.compile(r"\.([A-Za-z0-9_-]+)")
_SEL_TAG = re.compile(r"^([a-zA-Z][a-zA-Z0-9]*)")
_COMBINATOR = re.compile(r"\s*[>+~]\s*|\s+")


def flash_matchers(css_files: list[Path]) -> list[Matcher]:
    """Extract (tag, required-classes) matchers from every ``…:target`` rule.

    From ``.pipe-station[id]:target .pipe-card`` we keep the *compound that
    carries* ``:target`` (``.pipe-station[id]``) — that is the element the URL
    fragment names — and reduce it to its tag and class names. ``.section`` ->
    ``(None, {section})``; ``.cn-table tr:target`` -> ``(tr, set())``."""
    matchers: set[Matcher] = set()
    for path in css_files:
        try:
            text = _CSS_COMMENT.sub("", path.read_text(encoding="utf-8"))
        except OSError:
            continue
        for head in _RULE_HEAD.findall(text):
            if ":target" not in head:
                continue
            for selector in head.split(","):
                if ":target" not in selector:
                    continue
                compound = next(
                    (c for c in _COMBINATOR.split(selector.strip()) if ":target" in c),
                    None,
                )
                if compound is None:
                    continue
                tag_match = _SEL_TAG.match(compound)
                matchers.add(
                    (
                        tag_match.group(1) if tag_match else None,
                        frozenset(_SEL_CLASS.findall(compound)),
                    )
                )
    return sorted(matchers, key=lambda m: (m[0] or "", sorted(m[1])))


def _is_flashable(host: tuple[str, frozenset[str]], matchers: list[Matcher]) -> bool:
    """A host flashes if it satisfies any matcher: same tag (or tag-agnostic)
    and all of the matcher's required classes are present on the element."""
    tag, classes = host
    return any(
        (m_tag is None or m_tag == tag) and m_classes <= classes
        for m_tag, m_classes in matchers
    )


@cache
def _dir_names(parent: Path) -> frozenset[str]:
    try:
        return frozenset(p.name for p in parent.iterdir())
    except OSError:
        return frozenset()


def _exists_exact(path: Path) -> bool:
    """Case-sensitive ``exists()``. APFS is case-insensitive, so a wrong-case
    href passes plain ``exists()`` locally yet breaks on a case-sensitive FS;
    components above the repo root come from the machine, not from hrefs, so
    the walk stops there."""
    if not path.exists():
        return False
    current = path
    while current != REPO_ROOT and current != current.parent:
        if current.name not in _dir_names(current.parent):
            return False
        current = current.parent
    return True


def _broken_reason(target: Path) -> str | None:
    """Why an unresolved href target is broken — or ``None`` when it is a
    valid file outside architecture/ (no anchors to check there)."""
    if not target.exists():
        return "file_not_found"
    if not _exists_exact(target):
        # plain exists() passed only thanks to APFS case folding —
        # broken on a case-sensitive filesystem
        return "case_mismatch"
    return None


# Each xref class claims a destination category — a hue that must match where
# the link actually reaches (README §Cross-references): module (accent) → another
# module, page (olive) → another page here, anchor (dark) → a block on this page.
# A class that mismatches its target renders the link the wrong color yet resolves
# fine, so this structural check is the only thing that can catch it.
_XREF_CLAIM = {
    "xref-module": "module",
    "xref-page": "page",
    "xref-anchor": "anchor",
}


def _claimed_category(classes: frozenset[str]) -> str | None:
    """The destination an xref link's styling claims, or ``None`` when the link
    carries no xref styling (a plain ``<a>``, a wireframe control, a flow stop)
    or is a pending badge (a ``<span>`` whose destination isn't linkable yet)."""
    for cls, claim in _XREF_CLAIM.items():
        if cls in classes:
            return claim
    if "xref-badge" in classes:
        if "xref-badge--pending" in classes:
            return None
        return "page" if "xref-badge--page" in classes else "module"
    return None


def _actual_category(src: Doc, target: Doc) -> str | None:
    """Where a resolved link actually reaches, in the same vocabulary — ``None``
    when either end sits outside the module taxonomy (the hub, an
    ``open-questions`` / README target), where the hue rule doesn't apply."""
    if target is src:  # same file (empty href or self filename) → an anchor link
        return "anchor"
    if src.module.startswith("(") or target.module.startswith("("):
        return None
    return "page" if target.module == src.module else "module"


def _crosses_modules(src: Doc, dst: Doc) -> bool:
    """Only links between two real modules become graph edges — the hub
    (``(scheme)``) and stray files (``(other)``) stay out of the graph."""
    return (
        src.module != dst.module
        and not src.module.startswith("(")
        and not dst.module.startswith("(")
    )


@dataclass
class Report:
    """Structured findings — the source of truth for both output formats."""

    docs: list[Doc]
    # reason: file_not_found | case_mismatch | missing_anchor
    broken: list[dict[str, str]]
    graph: list[dict[str, object]]
    one_way: list[dict[str, str]]  # direction that exists, lacking reciprocal
    # Both directions exist, but one links to a section and the reverse only to
    # the front door — bidirectional at module level, asymmetric at anchor level.
    shallow: list[dict[str, str]]
    duplicate_ids: list[dict[str, str]]
    # Deep-links whose target element gets no :target flash (resolves fine, but
    # lands without the highlight that orients the reader). Advisory.
    unflashable: list[dict[str, str]]
    # Links whose xref class claims one destination category but the target is
    # another — the link renders the wrong hue yet resolves fine (miscolored).
    miscolored: list[dict[str, str]]
    cross_links: int

    @property
    def n_modules(self) -> int:
        return len({d.module for d in self.docs if not d.module.startswith("(")})

    @property
    def n_anchors(self) -> int:
        return sum(len(d.anchors) for d in self.docs)

    def failed(self, *, strict: bool) -> bool:
        """The validation gate — drives both the exit code and JSON ``ok``."""
        return bool(self.broken) or (
            strict
            and bool(
                self.one_way
                or self.duplicate_ids
                or self.unflashable
                or self.miscolored
            )
        )


def build_report(docs: list[Doc], matchers: list[Matcher]) -> Report:
    """Resolve every link, collect broken ones and the cross-module edges."""
    by_path = {d.path.resolve(): d for d in docs}
    broken: list[dict[str, str]] = []
    edges: Counter[tuple[str, str]] = Counter()  # (from_module, to_module)
    # Of those, how many carry a fragment (link to a specific section, not just
    # the module's front door) — drives the anchor-depth symmetry check.
    anchored: Counter[tuple[str, str]] = Counter()
    unflashable: list[dict[str, str]] = []
    # One target can be linked from many places; report each dead anchor once.
    seen_unflashable: set[tuple[str, str]] = set()
    miscolored: list[dict[str, str]] = []

    for doc in docs:
        for link in doc.links:
            href = link.href
            if _is_external(href):
                continue
            url, frag = urldefrag(href)
            if url == "":
                target = doc  # same-page anchor
            else:
                tgt_path = (doc.path.parent / unquote(url)).resolve()
                resolved = by_path.get(tgt_path)
                if resolved is None:
                    if reason := _broken_reason(tgt_path):
                        broken.append(
                            {
                                "file": str(doc.rel),
                                "href": href,
                                "fragment": frag,
                                "reason": reason,
                            }
                        )
                    continue
                target = resolved

            if frag and frag not in target.anchors:
                broken.append(
                    {
                        "file": str(doc.rel),
                        "href": href,
                        "fragment": frag,
                        "reason": "missing_anchor",
                    }
                )
            elif frag and (host := target.id_host.get(frag)):
                key = (str(target.rel), frag)
                if not _is_flashable(host, matchers) and key not in seen_unflashable:
                    seen_unflashable.add(key)
                    tag, classes = host
                    unflashable.append(
                        {
                            "target": str(target.rel),
                            "fragment": frag,
                            "host": tag
                            + ("." + ".".join(sorted(classes)) if classes else ""),
                        }
                    )

            if _crosses_modules(doc, target):
                edges[doc.module, target.module] += 1
                if frag:
                    anchored[doc.module, target.module] += 1

            claimed = _claimed_category(link.classes)
            if claimed is not None:
                actual = _actual_category(doc, target)
                if actual is not None and actual != claimed:
                    miscolored.append(
                        {
                            "file": str(doc.rel),
                            "href": href,
                            "claimed": claimed,
                            "actual": actual,
                        }
                    )

    graph, one_way, shallow = _pair_graph(edges, anchored)
    return Report(
        docs=docs,
        broken=broken,
        graph=graph,
        one_way=one_way,
        shallow=shallow,
        duplicate_ids=_duplicate_ids(docs),
        unflashable=unflashable,
        miscolored=miscolored,
        cross_links=sum(edges.values()),
    )


def _pair_graph(
    edges: Counter[tuple[str, str]],
    anchored: Counter[tuple[str, str]],
) -> tuple[list[dict[str, object]], list[dict[str, str]], list[dict[str, str]]]:
    """Collapse directed edges into undirected pairs to judge bidirectionality."""
    pairs: dict[frozenset[str], dict[tuple[str, str], int]] = {}
    for (src, dst), count in edges.items():
        pairs.setdefault(frozenset((src, dst)), {})[src, dst] = count

    graph: list[dict[str, object]] = []
    one_way: list[dict[str, str]] = []
    shallow: list[dict[str, str]] = []
    for key in sorted(pairs, key=sorted):
        m1, m2 = sorted(key)
        fwd = pairs[key].get((m1, m2), 0)
        bwd = pairs[key].get((m2, m1), 0)
        fwd_anc = anchored.get((m1, m2), 0)
        bwd_anc = anchored.get((m2, m1), 0)
        graph.append(
            {
                "a": m1,
                "b": m2,
                "a_to_b": fwd,
                "b_to_a": bwd,
                "a_to_b_anchored": fwd_anc,
                "b_to_a_anchored": bwd_anc,
                "bidirectional": bool(fwd and bwd),
            }
        )
        if fwd and not bwd:
            one_way.append({"from": m1, "to": m2})
        elif bwd and not fwd:
            one_way.append({"from": m2, "to": m1})
        elif fwd and bwd:  # both ways present — check anchor-depth symmetry
            if fwd_anc and not bwd_anc:
                shallow.append({"from": m2, "to": m1, "reason": "no_anchor_back"})
            elif bwd_anc and not fwd_anc:
                shallow.append({"from": m1, "to": m2, "reason": "no_anchor_back"})
    return graph, one_way, shallow


def _duplicate_ids(docs: list[Doc]) -> list[dict[str, str]]:
    """Duplicate ids within one file — valid HTML requires ids unique per page,
    and a stale duplicate can silently send a cross-link to the wrong spot."""
    return [
        {"file": str(doc.rel), "id": dup}
        for doc in docs
        for dup in sorted(set(doc.duplicate_ids))
    ]


_BROKEN_SUFFIX = {
    "file_not_found": "(target file not found)",
    "case_mismatch": "(filename case differs from file on disk)",
}


def _print_human(report: Report, *, full: bool, failed: bool) -> None:
    print("docs-map — architecture doc link map\n")
    print(
        f"Files: {len(report.docs)}  |  Modules: {report.n_modules}  |  "
        f"Anchors: {report.n_anchors}  |  Cross-module links: {report.cross_links}\n"
    )

    print("Connection graph (by module):")
    if report.graph:
        for e in report.graph:
            m1, m2, fwd, bwd = e["a"], e["b"], e["a_to_b"], e["b_to_a"]
            if e["bidirectional"]:
                print(f"  {m1} ↔ {m2}   ({m1}→{m2}: {fwd}, {m2}→{m1}: {bwd})")
            elif fwd:
                print(f"  {m1} → {m2}   ({fwd})  ⚠ one-way")
            else:
                print(f"  {m2} → {m1}   ({bwd})  ⚠ one-way")
    else:
        print("  (no cross-module links)")
    print()

    if full:
        print("Anchors by file:")
        for doc in report.docs:
            if doc.anchors:
                print(f"  {doc.rel}")
                for anchor in sorted(doc.anchors):
                    print(f"    #{anchor}")
        print()

    if report.broken:
        print(f"✗ Broken links/anchors ({len(report.broken)}):")
        for b in report.broken:
            suffix = _BROKEN_SUFFIX.get(
                b["reason"], f'(no id="{b["fragment"]}" in target)'
            )
            print(f"  {b['file']} → {b['href']}  {suffix}")
        print()
    if report.duplicate_ids:
        print(f"⚠ Duplicate ids ({len(report.duplicate_ids)}):")
        for d in report.duplicate_ids:
            print(f'  {d["file"]}: duplicate id="{d["id"]}"')
        print()
    if report.one_way:
        print(f"⚠ One-way links ({len(report.one_way)}):")
        for w in report.one_way:
            print(
                f"  {w['from']} → {w['to']}: no reciprocal link {w['to']} → {w['from']}"
            )
        print()
    if report.shallow:
        print(f"⚠ Shallow back-links ({len(report.shallow)}):")
        for s in report.shallow:
            print(
                f"  {s['from']} → {s['to']}: links carry no anchor, "
                f"while {s['to']} → {s['from']} link to a section"
            )
        print()
    if report.unflashable:
        print(f"⚠ Anchor targets without a :target flash ({len(report.unflashable)}):")
        for u in report.unflashable:
            print(f"  {u['target']}#{u['fragment']}  (<{u['host']}> — no :target rule)")
        print()
    if report.miscolored:
        print(f"⚠ Miscolored xref links ({len(report.miscolored)}):")
        for mc in report.miscolored:
            print(
                f"  {mc['file']} → {mc['href']}  "
                f"(styled as {mc['claimed']}, target is {mc['actual']})"
            )
        print()

    has_warnings = (
        report.one_way
        or report.duplicate_ids
        or report.shallow
        or report.unflashable
        or report.miscolored
    )
    if report.broken:
        print("✗ Broken links found — fix required")
    elif failed:  # only --strict escalation can fail without broken links
        print(
            "✗ --strict: one-way / duplicate ids / unflashable / miscolored links "
            "are errors"
        )
    elif has_warnings:
        print("✓ Anchors valid (warnings above — review)")
    else:
        print("✓ All anchors valid, all cross-module links bidirectional")


def _print_json(report: Report, *, full: bool, failed: bool) -> None:
    payload: dict[str, object] = {
        "summary": {
            "files": len(report.docs),
            "modules": report.n_modules,
            "anchors": report.n_anchors,
            "cross_module_links": report.cross_links,
        },
        "graph": report.graph,
        "broken": report.broken,
        "duplicate_ids": report.duplicate_ids,
        "one_way": report.one_way,
        "shallow": report.shallow,
        "unflashable": report.unflashable,
        "miscolored": report.miscolored,
        "ok": not failed,
    }
    if full:
        payload["anchors_by_file"] = {
            str(d.rel): sorted(d.anchors) for d in report.docs if d.anchors
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    cli = argparse.ArgumentParser(description=(__doc__ or "").partition("\n")[0])
    cli.add_argument("--human", action="store_true", help="pretty text instead of JSON")
    cli.add_argument("--full", action="store_true", help="add per-file anchor listing")
    cli.add_argument(
        "--strict",
        action="store_true",
        help="escalate one-way / duplicate ids / unflashable / miscolored links to errors",
    )
    opts = cli.parse_args()

    docs = sorted(
        (Doc(p) for p in ARCH_ROOT.rglob("*.html")),
        key=lambda d: str(d.rel),
    )
    # The flash allow-list is derived from the stylesheets the docs actually
    # load: every *.css under architecture/ plus the shared sheet one level up.
    css_files = [*ARCH_ROOT.rglob("*.css"), REPO_ROOT / "docs" / "shared.css"]
    report = build_report(docs, flash_matchers(css_files))
    failed = report.failed(strict=opts.strict)

    render = _print_human if opts.human else _print_json
    render(report, full=opts.full, failed=failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
