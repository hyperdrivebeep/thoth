"""Structure groups: the shapes a source already shows, derived from its context observation.

A group is a title (spans that name it) and a body (the spans it holds). New retrieval lanes rank
groups instead of single lines, so a row of numbers is scored with the version title, table head
or heading above it. Nothing here reads a model, changes a source or enters a source digest.

Invariants, held for every source version the groups are derived for:
- every span of the source is in the body of exactly one group, and only once; titles may repeat;
- a group never holds spans of two sources (it is derived per source version);
- a group that was cut for size keeps the title of the group it was cut from.

Rules (GROUPING_VERSION changes when any of them or a limit changes):
- FENCE: one fenced code region. Inside it every separator line (===, --- or ### followed by text)
  starts a sub-group, and that line is part of the sub-group's title as well as its body. The title
  is also the nearest heading and the paragraph that leads into the fence.
- LIST: the spans of one top-level list. A top-level item that holds a nested list is a sub-group of
  its own; the other items form one group. The title is the nearest heading.
- SECTION: a heading and the plain paragraphs under it (not fenced code, not lists); its title is
  the headings above it and itself. Paragraphs before the first heading form an untitled
  section, and spans no region claims are collected the same way.
- BLOCK: for sources without an observation, spans of one structured block (same parent pointer).
"""

import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from thoth.domain.context_observation import ContextRegion

GROUPING_VERSION = "structure-groups:1.0.0"
MAX_GROUP_SPANS = 24
MAX_GROUP_CHARS = 3000
SEPARATOR = re.compile(r"^\s*(?:={3,}|-{3,}|#{3,})\s*\S")

type GroupKind = Literal["SECTION", "FENCE", "LIST", "BLOCK"]


@dataclass(frozen=True)
class CatalogGroup:
    kind: GroupKind
    title_refs: tuple[str, ...]
    body_refs: tuple[str, ...]


def block_key(pointer: str | None) -> str | None:
    """The key path that groups a structured line with the lines under the same parent key."""
    if not pointer or not pointer.startswith("/"):
        return None
    parent, _, _ = pointer.rpartition("/")
    return parent or pointer


def _chunks(refs: Sequence[str], texts: Mapping[str, str]) -> list[list[str]]:
    out: list[list[str]] = []
    current: list[str] = []
    size = 0
    for ref in refs:
        if current and (
            len(current) >= MAX_GROUP_SPANS or size + len(texts[ref]) > MAX_GROUP_CHARS
        ):
            out.append(current)
            current, size = [], 0
        current.append(ref)
        size += len(texts[ref])
    if current:
        out.append(current)
    return out


def _fence_pieces(refs: Sequence[str], texts: Mapping[str, str]) -> list[list[str]]:
    pieces: list[list[str]] = []
    current: list[str] = []
    for ref in refs:
        if current and SEPARATOR.match(texts[ref]):
            pieces.append(current)
            current = []
        current.append(ref)
    if current:
        pieces.append(current)
    return pieces


class _Builder:
    def __init__(self, texts: Mapping[str, str]) -> None:
        self.texts = texts
        self.taken: set[str] = set()
        self.groups: list[CatalogGroup] = []

    def add(self, kind: GroupKind, title: Sequence[str], body: Sequence[str]) -> None:
        fresh = [ref for ref in dict.fromkeys(body) if ref not in self.taken]
        self.taken.update(fresh)
        for chunk in _chunks(fresh, self.texts):
            self.groups.append(CatalogGroup(kind, tuple(dict.fromkeys(title)), tuple(chunk)))


def derive_groups(
    regions: Sequence[ContextRegion],
    span_of_node: Mapping[str, str],
    texts: Mapping[str, str],
    order: Sequence[str],
) -> tuple[CatalogGroup, ...]:
    """Groups of one source version. `order` is its spans in document order."""
    by_id = {r.region_id: r for r in regions}

    def spans(region: ContextRegion) -> list[str]:
        return [span_of_node[n] for n in region.node_refs if n in span_of_node]

    def heading_spans(region: ContextRegion) -> list[str]:
        return [x for ref in region.heading_refs[-1:] for x in spans(by_id[ref])]

    nested_items = {
        (r.container_path[0], r.container_path[1])
        for r in regions
        if len(r.container_path) > 2
        and r.container_path[0].startswith(("bullet_list", "ordered_list"))
    }
    builder = _Builder(texts)
    sections: OrderedDict[str | None, tuple[list[str], list[str]]] = OrderedDict()
    lists: OrderedDict[str, tuple[list[str], list[str], OrderedDict[str, list[str]]]] = (
        OrderedDict()
    )
    for region in regions:
        if region.kind == "BLANK_INTERVAL":
            continue
        own = spans(region)
        top = region.container_path[0] if region.container_path else ""
        if region.kind == "ATX_HEADING":
            ancestors = [x for ref in region.heading_refs for x in spans(by_id[ref])]
            sections[region.region_id] = ([*ancestors, *own], list(own))
        elif region.kind == "FENCED_CODE":
            lead = [x for ref in region.leading_refs for x in spans(by_id[ref])]
            for piece in _fence_pieces(own, texts):
                head = piece[0] if SEPARATOR.match(texts[piece[0]]) else None
                title = [*heading_spans(region), *lead, *([head] if head else [])]
                builder.add("FENCE", title, piece)
        elif top.startswith(("bullet_list", "ordered_list")):
            entry = lists.setdefault(top, (heading_spans(region), [], OrderedDict()))
            item = region.container_path[1] if len(region.container_path) > 1 else ""
            if (top, item) in nested_items:
                entry[2].setdefault(item, []).extend(own)
            else:
                entry[1].extend(own)
        else:
            nearest = region.heading_refs[-1] if region.heading_refs else None
            key = nearest if nearest in sections else None
            sections.setdefault(key, ([], []))[1].extend(own)
    for title, flat, nested in lists.values():
        for body in nested.values():
            builder.add("LIST", title, body)
        builder.add("LIST", title, flat)
    for title, body in sections.values():
        builder.add("SECTION", title, body)
    builder.add("SECTION", [], [ref for ref in order if ref not in builder.taken])
    position = {ref: index for index, ref in enumerate(order)}
    ordered = sorted(builder.groups, key=lambda g: position[g.body_refs[0]])
    return tuple(ordered)


def derive_block_groups(
    pointers: Sequence[tuple[str, str | None]], texts: Mapping[str, str]
) -> tuple[CatalogGroup, ...]:
    """Groups of a source with no observation: spans under the same parent pointer, two or more."""
    blocks: OrderedDict[str, list[str]] = OrderedDict()
    for ref, pointer in pointers:
        key = block_key(pointer)
        if key is not None:
            blocks.setdefault(key, []).append(ref)
    builder = _Builder(texts)
    for body in blocks.values():
        if len(body) >= 2:
            builder.add("BLOCK", [], body)
    return tuple(builder.groups)
