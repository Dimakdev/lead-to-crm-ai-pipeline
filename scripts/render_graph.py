#!/usr/bin/env python3
"""Draw workflow.json as an SVG you can actually read.

    python scripts/render_graph.py

A screenshot of the n8n canvas goes stale the moment a node moves, and it is a dark picture with half
the names cut off. These are generated from the same file the workflow is built from, so they cannot
drift from the graph, they stay sharp at any zoom, and they live in git as text you can diff.

Four labelled chains in one picture: the common intake, the two scoring branches, and the shared
output. The canvas lays this out on 6,300 pixels, which is seven times wider than a page.
"""
from __future__ import annotations

import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"

# One colour per kind of work, so the shape reads before any label does.
COLOURS = {
    "trigger": ("#2f6f4f", "#dcf2e6"),
    "code": ("#8a5a00", "#ffeccc"),
    "http": ("#2b5f9e", "#dbe9fb"),
    "model": ("#6b3fa0", "#eee2f8"),
    "telegram": ("#1d7fa8", "#d9f1fa"),
    "data": ("#1f7a5a", "#d8f2e8"),
    "mail": ("#a03a3a", "#fbe0e0"),
    "control": ("#596070", "#eaecf0"),
}
LEGEND = {"trigger": "trigger", "data": "Airtable", "mail": "Gmail", "code": "code",
          "model": "model", "telegram": "alert", "control": "control"}
FONT = "ui-sans-serif, -apple-system, 'Segoe UI', Roboto, sans-serif"

# The classes are a bonus for dark mode. Every element also carries an explicit fill, because a
# renderer that drops the <style> block would otherwise leave a diagram with no background at all.
STYLE = ('<style>.bg{fill:#fff}.t{fill:#111827}.s{fill:#6b7280}'
         '@media (prefers-color-scheme: dark){.bg{fill:#0d1117}.t{fill:#e6edf3}.s{fill:#9198a1}'
         '.box{filter:brightness(.8) saturate(1.1)}}</style>')


def kind(node: dict) -> str:
    t = node["type"]
    if "rigger" in t or t.endswith("webhook"):
        return "trigger"
    if t.endswith(".code"):
        return "code"
    if t.endswith("airtable"):
        return "data"
    if t.endswith("gmail") or t.endswith("gmailTrigger"):
        return "mail"
    if t.endswith("httpRequest"):
        return "http"
    if "langchain" in t:
        return "model"
    if t.endswith("telegram"):
        return "telegram"
    return "control"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap(name: str, width: int) -> list[str]:
    lines, cur = [], ""
    for w in name.split():
        if len(cur) + len(w) + 1 <= width:
            cur = f"{cur} {w}".strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:2]


def legend(parts: list[str], x: float, y: float):
    for k, label in LEGEND.items():
        stroke, fill = COLOURS[k]
        parts.append(f'<rect x="{x}" y="{y}" width="11" height="11" rx="3" fill="{fill}" '
                     f'stroke="{stroke}" stroke-width="1.4"/>')
        parts.append(f'<text class="s" x="{x + 17}" y="{y + 9.5}" font-size="11.5" fill="#6b7280">{label}</text>')
        x += 24 + len(label) * 6.7


def order_from(wf: dict, start: str, stop_at: set[str]) -> list[str]:
    """Execution order, depth first, which is how a person reads a chain."""
    nodes = {n["name"]: n for n in wf["nodes"]}
    adj = collections.defaultdict(list)
    for src, kinds in wf["connections"].items():
        for kind_, groups in kinds.items():
            if kind_ != "main":
                continue
            for g in groups:
                for link in g:
                    if link["node"] in nodes:
                        adj[src].append(link["node"])
    out, seen = [], set()

    def walk(n):
        if n in seen or n in stop_at or n not in nodes:
            return
        seen.add(n)
        out.append(n)
        for nxt in adj.get(n, []):
            walk(nxt)

    walk(start)
    return out


# ---------------------------------------------------------------- wrapped, readable
def render_flow(wf: dict, names: list[str], title: str, subtitle: str, per_row: int = 4) -> str:
    """Lay the chain out in rows instead of one endless line. A README image has to be about as wide
    as a page, not nine times wider, or every label shrinks into a smudge."""
    nodes = {n["name"]: n for n in wf["nodes"]}
    BW, BH, GX, GY, M = 212, 62, 44, 42, 26
    rows = [names[i:i + per_row] for i in range(0, len(names), per_row)]
    w = M * 2 + per_row * BW + (per_row - 1) * GX
    h = 92 + len(rows) * (BH + GY) + 30

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
         f'font-family="{FONT}">', STYLE,
         f'<rect class="bg" width="{w}" height="{h}" fill="#ffffff"/>',
         f'<text class="t" x="{M}" y="34" font-size="20" font-weight="600" fill="#111827">{esc(title)}</text>',
         f'<text class="s" x="{M}" y="56" font-size="13" fill="#6b7280">{esc(subtitle)}</text>']

    pos = {}
    for r, row in enumerate(rows):
        for c, name in enumerate(row):
            pos[name] = (M + c * (BW + GX), 92 + r * (BH + GY))

    for i, name in enumerate(names[:-1]):
        x1, y1 = pos[name]
        x2, y2 = pos[names[i + 1]]
        if y1 == y2:                                   # same row: straight across
            p.append(f'<path d="M{x1 + BW},{y1 + BH / 2} L{x2 - 8},{y2 + BH / 2}" fill="none" '
                     f'stroke="#9aa4b2" stroke-width="1.8" marker-end="url(#a)"/>')
        else:                                          # wrap: down the right, back along the left
            p.append(f'<path d="M{x1 + BW / 2},{y1 + BH} L{x1 + BW / 2},{y1 + BH + GY / 2} '
                     f'L{x2 + BW / 2},{y1 + BH + GY / 2} L{x2 + BW / 2},{y2 - 8}" fill="none" '
                     f'stroke="#9aa4b2" stroke-width="1.8" stroke-dasharray="5 4" marker-end="url(#a)"/>')

    p.insert(2, '<defs><marker id="a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" '
                'markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#9aa4b2"/>'
                '</marker></defs>')

    for i, name in enumerate(names, 1):
        n = nodes[name]
        stroke, fill = COLOURS[kind(n)]
        x, y = pos[name]
        p.append(f'<rect class="box" x="{x}" y="{y}" width="{BW}" height="{BH}" rx="9" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="1.7"/>')
        p.append(f'<text x="{x + 9}" y="{y + 15}" font-size="10" fill="{stroke}" opacity=".65">{i}</text>')
        lines = wrap(name, 25)
        top = y + BH / 2 - (len(lines) - 1) * 7.5 + 4
        for j, line in enumerate(lines):
            p.append(f'<text x="{x + BW / 2}" y="{top + j * 15}" text-anchor="middle" font-size="13.5" '
                     f'fill="{stroke}" font-weight="600">{esc(line)}</text>')

    legend(p, M, h - 24)
    p.append("</svg>")
    return "\n".join(p)


def render_branches(wf: dict, groups: list[tuple[str, list[str]]], title: str, subtitle: str,
                    per_row: int = 4) -> str:
    """Several short chains stacked, each with its own heading. Keeps them comparable and keeps every
    label the same size as the main diagram."""
    nodes = {n["name"]: n for n in wf["nodes"]}
    BW, BH, GX, GY, M = 212, 62, 44, 42, 26
    w = M * 2 + per_row * BW + (per_row - 1) * GX

    blocks, y = [], 92
    for heading, names in groups:
        rows = [names[i:i + per_row] for i in range(0, len(names), per_row)]
        blocks.append((heading, names, rows, y))
        y += 34 + len(rows) * (BH + GY)
    h = y + 26

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
         f'font-family="{FONT}">', STYLE,
         '<defs><marker id="a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" '
         'markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#9aa4b2"/></marker></defs>',
         f'<rect class="bg" width="{w}" height="{h}" fill="#ffffff"/>',
         f'<text class="t" x="{M}" y="34" font-size="20" font-weight="600" fill="#111827">{esc(title)}</text>',
         f'<text class="s" x="{M}" y="56" font-size="13" fill="#6b7280">{esc(subtitle)}</text>']

    for heading, names, rows, top in blocks:
        p.append(f'<text class="t" x="{M}" y="{top + 16}" font-size="14" font-weight="600" '
                 f'fill="#111827">{esc(heading)}</text>')
        pos = {}
        for r, row in enumerate(rows):
            for c, name in enumerate(row):
                pos[name] = (M + c * (BW + GX), top + 34 + r * (BH + GY))
        for i, name in enumerate(names[:-1]):
            x1, y1 = pos[name]
            x2, y2 = pos[names[i + 1]]
            if y1 == y2:
                p.append(f'<path d="M{x1 + BW},{y1 + BH / 2} L{x2 - 8},{y2 + BH / 2}" fill="none" '
                         f'stroke="#9aa4b2" stroke-width="1.8" marker-end="url(#a)"/>')
            else:
                p.append(f'<path d="M{x1 + BW / 2},{y1 + BH} L{x1 + BW / 2},{y1 + BH + GY / 2} '
                         f'L{x2 + BW / 2},{y1 + BH + GY / 2} L{x2 + BW / 2},{y2 - 8}" fill="none" '
                         f'stroke="#9aa4b2" stroke-width="1.8" stroke-dasharray="5 4" marker-end="url(#a)"/>')
        for i, name in enumerate(names, 1):
            stroke, fill = COLOURS[kind(nodes[name])]
            x, y = pos[name]
            p.append(f'<rect class="box" x="{x}" y="{y}" width="{BW}" height="{BH}" rx="9" '
                     f'fill="{fill}" stroke="{stroke}" stroke-width="1.7"/>')
            p.append(f'<text x="{x + 9}" y="{y + 15}" font-size="10" fill="{stroke}" opacity=".65">{i}</text>')
            lines = wrap(name, 25)
            ty = y + BH / 2 - (len(lines) - 1) * 7.5 + 4
            for j, line in enumerate(lines):
                p.append(f'<text x="{x + BW / 2}" y="{ty + j * 15}" text-anchor="middle" '
                         f'font-size="13.5" fill="{stroke}" font-weight="600">{esc(line)}</text>')

    legend(p, M, h - 22)
    p.append("</svg>")
    return "\n".join(p)


# ---------------------------------------------------------------- faithful to the canvas
def render_canvas(wf: dict, title: str, subtitle: str) -> str:
    nodes = [n for n in wf["nodes"] if not n["type"].endswith("stickyNote")]
    BW, BH, PAD = 150, 48, 90
    pos = {n["name"]: tuple(n["position"]) for n in nodes}
    minx = min(p[0] for p in pos.values()) - PAD
    miny = min(p[1] for p in pos.values()) - PAD
    w = max(p[0] for p in pos.values()) - minx + BW + PAD
    h = max(p[1] for p in pos.values()) - miny + BH + PAD + 70

    X = lambda v: round(v - minx, 1)
    Y = lambda v: round(v - miny + 60, 1)

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {round(w)} {round(h)}" '
         f'width="{round(w)}" height="{round(h)}" font-family="{FONT}">', STYLE,
         f'<rect class="bg" width="{round(w)}" height="{round(h)}" fill="#ffffff"/>',
         f'<text class="t" x="16" y="32" font-size="20" font-weight="600" fill="#111827">{esc(title)}</text>',
         f'<text class="s" x="16" y="52" font-size="13" fill="#6b7280">{esc(subtitle)}</text>']

    names = set(pos)
    for src, kinds in wf["connections"].items():
        if src not in names:
            continue
        for groups in kinds.values():
            for g in groups:
                for link in g:
                    if link["node"] not in names:
                        continue
                    x1, y1 = X(pos[src][0]) + BW, Y(pos[src][1]) + BH / 2
                    x2, y2 = X(pos[link["node"]][0]), Y(pos[link["node"]][1]) + BH / 2
                    mid = (x1 + x2) / 2
                    p.append(f'<path d="M{x1},{y1} C{mid},{y1} {mid},{y2} {x2},{y2}" fill="none" '
                             f'stroke="#9aa4b2" stroke-width="1.6" opacity=".8"/>')

    for n in nodes:
        stroke, fill = COLOURS[kind(n)]
        x, y = X(pos[n["name"]][0]), Y(pos[n["name"]][1])
        p.append(f'<rect class="box" x="{x}" y="{y}" width="{BW}" height="{BH}" rx="8" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="1.6"/>')
        lines = wrap(n["name"], 19)
        top = y + BH / 2 - (len(lines) - 1) * 7 + 4
        for i, line in enumerate(lines):
            p.append(f'<text x="{x + BW / 2}" y="{top + i * 14}" text-anchor="middle" font-size="11.5" '
                     f'fill="{stroke}" font-weight="600">{esc(line)}</text>')

    legend(p, 16, h - 26)
    p.append("</svg>")
    return "\n".join(p)


def used_kinds(wf):
    return {kind(n) for n in wf["nodes"] if not n["type"].endswith("stickyNote")}


def main():
    wf = json.loads((ROOT / "workflow.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)

    # Drawn as four short chains rather than one line. The canvas puts this on 6,300 pixels, which is
    # seven times wider than a page and unreadable once GitHub scales it down.
    groups = [
        ("Three ways in, one shape out", [
            "Form trigger", "Gmail trigger", "Webhook lead intake",
            "Normalize lead", "Validate input", "Is valid", "Register lead", "Lead state",
            "Route by lead type"]),
        ("Thin: a rule decides, no model is called", [
            "Rule scoring", "Prepare routing"]),
        ("Rich: read the company site, then ask the model", [
            "Fetch company site", "Process fetch result", "Retry fetch", "Wait fetch backoff",
            "Build prompt", "AI qualify", "Validate contract", "Classify AI error", "Retry AI",
            "Wait AI backoff", "Resolve contract"]),
        ("Same six fields, whichever branch produced them", [
            "Prepare routing", "Save qualification", "Alert needed", "Telegram alert",
            "Route by heat", "Gmail draft hot", "Build run row", "Log run", "Webhook response"]),
    ]
    names = {n["name"] for n in wf["nodes"]}
    groups = [(h, [n for n in ns if n in names]) for h, ns in groups]

    (OUT / "workflow.svg").write_text(
        render_branches(wf, groups, "Lead to CRM, in the order it runs",
                        "Every branch returns the same six fields, so everything after the split is "
                        "one path. Retries and waits are drawn where they actually sit."),
        encoding="utf-8")

    kb = (OUT / "workflow.svg").stat().st_size // 1024
    print(f"docs/images/workflow.svg  {kb} KB")


if __name__ == "__main__":
    main()
