from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO


XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
IX_NAMESPACES = {
    "http://www.xbrl.org/2008/inlineXBRL",
    "http://www.xbrl.org/2013/inlineXBRL",
}
_WHITESPACE = re.compile(r"\s+")


def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


def _text(element: ET.Element) -> str:
    return _WHITESPACE.sub(" ", "".join(element.itertext())).strip()


@dataclass(slots=True)
class Context:
    context_id: str
    source_file: str = ""
    entity_identifier: str | None = None
    entity_scheme: str | None = None
    period_type: str | None = None
    instant: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    dimensions: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Fact:
    source_file: str
    concept: str
    namespace_uri: str
    local_name: str
    context_ref: str
    unit_ref: str | None
    decimals: str | None
    precision: str | None
    value: str | None
    is_nil: bool


@dataclass(slots=True)
class ParsedXbrl:
    facts: list[Fact] = field(default_factory=list)
    contexts: dict[tuple[str, str], Context] = field(default_factory=dict)
    units: dict[tuple[str, str], str] = field(default_factory=dict)
    source_files: list[str] = field(default_factory=list)


def _parse_context(element: ET.Element, source_file: str) -> Context:
    context = Context(context_id=element.attrib["id"], source_file=source_file)
    identifier = element.find(f".//{{{XBRLI}}}identifier")
    if identifier is not None:
        context.entity_identifier = _text(identifier)
        context.entity_scheme = identifier.attrib.get("scheme")

    period = element.find(f"./{{{XBRLI}}}period")
    if period is not None:
        instant = period.find(f"./{{{XBRLI}}}instant")
        start = period.find(f"./{{{XBRLI}}}startDate")
        end = period.find(f"./{{{XBRLI}}}endDate")
        forever = period.find(f"./{{{XBRLI}}}forever")
        if instant is not None:
            context.period_type = "instant"
            context.instant = _text(instant)
        elif start is not None and end is not None:
            context.period_type = "duration"
            context.start_date = _text(start)
            context.end_date = _text(end)
        elif forever is not None:
            context.period_type = "forever"

    for member in element.findall(f".//{{{XBRLDI}}}explicitMember"):
        dimension = member.attrib.get("dimension", "")
        context.dimensions[dimension] = _text(member)
    for member in element.findall(f".//{{{XBRLDI}}}typedMember"):
        dimension = member.attrib.get("dimension", "")
        context.dimensions[dimension] = _text(member)
    return context


def _parse_unit(element: ET.Element) -> str:
    measures = [_text(item) for item in element.findall(f"./{{{XBRLI}}}measure")]
    if measures:
        return " * ".join(measures)
    numerator = [
        _text(item)
        for item in element.findall(f".//{{{XBRLI}}}unitNumerator/{{{XBRLI}}}measure")
    ]
    denominator = [
        _text(item)
        for item in element.findall(
            f".//{{{XBRLI}}}unitDenominator/{{{XBRLI}}}measure"
        )
    ]
    return f"{' * '.join(numerator)} / {' * '.join(denominator)}"


def parse_xbrl(stream: BinaryIO, source_file: str = "<stream>") -> ParsedXbrl:
    """Parse facts, contexts, and units from a standard XBRL instance."""
    tree = ET.parse(stream)
    root = tree.getroot()
    parsed = ParsedXbrl(source_files=[source_file])

    for child in root:
        namespace, local_name = _split_tag(child.tag)
        if namespace == XBRLI and local_name == "context":
            context = _parse_context(child, source_file)
            parsed.contexts[(source_file, context.context_id)] = context
        elif namespace == XBRLI and local_name == "unit":
            parsed.units[(source_file, child.attrib["id"])] = _parse_unit(child)

    for element in root.iter():
        context_ref = element.attrib.get("contextRef")
        if not context_ref:
            continue
        namespace, local_name = _split_tag(element.tag)
        if namespace in IX_NAMESPACES:
            # Inline XBRL is handled separately when no instance is present.
            continue
        nil = element.attrib.get(f"{{{XSI}}}nil", "false").lower() in {"true", "1"}
        parsed.facts.append(
            Fact(
                source_file=source_file,
                concept=element.tag,
                namespace_uri=namespace,
                local_name=local_name,
                context_ref=context_ref,
                unit_ref=element.attrib.get("unitRef"),
                decimals=element.attrib.get("decimals"),
                precision=element.attrib.get("precision"),
                value=None if nil else _text(element),
                is_nil=nil,
            )
        )
    return parsed


def _merge(target: ParsedXbrl, incoming: ParsedXbrl) -> None:
    target.facts.extend(incoming.facts)
    target.contexts.update(incoming.contexts)
    target.units.update(incoming.units)
    target.source_files.extend(incoming.source_files)


def parse_zip(path: Path) -> ParsedXbrl:
    """Parse all XBRL instance files under XBRL/PublicDoc in an EDINET ZIP."""
    result = ParsedXbrl()
    with zipfile.ZipFile(path) as archive:
        candidates = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".xbrl")
            and "/publicdoc/" in f"/{name.lower()}"
            and not name.endswith("/")
        ]
        if not candidates:
            candidates = [
                name
                for name in archive.namelist()
                if name.lower().endswith(".xbrl") and not name.endswith("/")
            ]
        if not candidates:
            raise ValueError(f"{path}: XBRLインスタンスがZIP内に見つかりません")
        for name in candidates:
            with archive.open(name) as member:
                _merge(result, parse_xbrl(member, name))
    return result


def context_dimensions_json(context: Context) -> str:
    return json.dumps(context.dimensions, ensure_ascii=False, sort_keys=True)
