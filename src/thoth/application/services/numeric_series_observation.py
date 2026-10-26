"""Conservative repeated-row observations over the actual admitted text input."""

import hashlib
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from thoth.application.services.research_selection_context import source_observation
from thoth.domain.artifact import StructuralDocument
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.context_observation import ContextRegion
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.numeric_series import (
    NUMERIC_GRAMMAR_VERSION,
    NumericDiagnostic,
    NumericPoint,
    NumericSeriesObservation,
    NumericSourceRef,
)
from thoth.domain.research_request import RevisionRef
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.document_calculator import DocumentCalculatorPort

_NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
_ROW = re.compile(
    r"^(?P<label>.+?)\s+(?P<size>\S+)\s+(?P<axis>[^\W\d][\w-]*)\s+"
    r"(?P<measurement>[+-]?(?:[0-9.]\S*|nan\S*|inf\S*)(?:[ \t]+[A-Za-z%µμ°]+)?)$",
    re.IGNORECASE,
)
_MEASUREMENT = re.compile(rf"^(?P<value>{_NUMBER})[ \t]*(?P<unit>[A-Za-z%µμ°]+)$")


class NumericObservationUnsupported(ValueError):
    """An identified unsupported row/group, not an arbitrary internal failure."""


def _number(text: str) -> Decimal:
    if re.fullmatch(_NUMBER, text) is None:
        raise NumericObservationUnsupported("NUMERIC_LITERAL_UNSUPPORTED")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise NumericObservationUnsupported("NUMERIC_LITERAL_UNSUPPORTED") from None
    parts = value.as_tuple()
    if (
        not value.is_finite()
        or len(parts.digits) > 128
        or not isinstance(parts.exponent, int)
        or abs(parts.exponent) > 1024
    ):
        raise NumericObservationUnsupported("NUMERIC_LITERAL_LIMIT")
    return value


def _source(span: EvidenceSpan) -> NumericSourceRef:
    return NumericSourceRef(
        span_id=span.span_id, text_sha256=span.text_sha256, locator=span.locator
    )


def _location(region: ContextRegion) -> dict[str, object]:
    return {
        key: getattr(region, key)
        for key in (
            "kind",
            "line_start",
            "line_end",
            "char_start",
            "char_end",
            "coordinate_basis",
        )
    }


def _series(
    request: RevisionRef,
    document: StructuralDocument,
    region: ContextRegion,
    conditions: tuple[EvidenceSpan, ...],
    rows: list[tuple[EvidenceSpan, re.Match[str]]],
    calculator: DocumentCalculatorPort,
) -> NumericSeriesObservation:
    label, axis = rows[0][1]["label"].strip(), rows[0][1]["axis"]
    points: list[NumericPoint] = []
    units: set[str] = set()
    for span, match in rows:
        measurement = _MEASUREMENT.fullmatch(match["measurement"])
        if measurement is None:
            raise NumericObservationUnsupported("NUMERIC_LITERAL_UNSUPPORTED")
        size, value = _number(match["size"]), _number(measurement["value"])
        if size <= 0:
            raise NumericObservationUnsupported("NUMERIC_SIZE_NONPOSITIVE")
        units.add(measurement["unit"])
        points.append(
            NumericPoint(
                source=_source(span),
                size_text=match["size"],
                value_text=measurement["value"],
                size=str(size),
                value=str(value),
            )
        )
    if len(units) != 1:
        raise NumericObservationUnsupported("NUMERIC_MIXED_UNITS")
    unit = next(iter(units))
    if not calculator.supports_unit(unit):
        raise NumericObservationUnsupported("NUMERIC_UNIT_UNSUPPORTED")
    if len({Decimal(p.size) for p in points}) != len(points):
        raise NumericObservationUnsupported("NUMERIC_DUPLICATE_SIZE")
    if len(points) < 2:
        raise NumericObservationUnsupported("NUMERIC_TOO_FEW_POINTS")
    context = [
        {
            "text": s.exact_text,
            "location": s.locator.model_dump(exclude={"structural_node_id", "file_path"}),
        }
        for s in conditions
    ]
    if document.context_observation is None:
        raise ValueError("NUMERIC_OBSERVATION_MISSING")
    content = {
        "source_sha256": document.artifact.byte_sha256,
        "parser": (document.artifact.parser_name, document.artifact.parser_version),
        "observer": document.context_observation.observer_version,
        "grammar": NUMERIC_GRAMMAR_VERSION,
        "calculator": calculator.version,
        "units": calculator.unit_registry_digest,
        "region": _location(region),
        "context": context,
        "label": label,
        "size_label": axis,
        "unit": unit,
    }
    series_digest = domain_digest("NUMERIC_SERIES", "1.0.0", canonical_payload(content))
    point_content = [
        {
            "size": p.size,
            "value": p.value,
            "size_text": p.size_text,
            "value_text": p.value_text,
            "text_sha256": p.source.text_sha256,
            "locator": p.source.locator.model_dump(exclude={"structural_node_id", "file_path"}),
        }
        for p in points
    ]
    points_digest = domain_digest(
        "NUMERIC_POINTS", "1.0.0", canonical_payload({"points": point_content})
    )
    source_refs = tuple(
        dict.fromkeys((*(_source(s) for s in conditions), *(p.source for p in points)))
    )
    binding = {
        "request": request,
        "project": document.artifact.project_id,
        "artifact": document.artifact.artifact_id,
        "version": rows[0][0].source_version_id,
        "series": series_digest,
        "points": points_digest,
        "source_refs": source_refs,
        "source_states": tuple(
            (s.span_id, s.authority_state, s.cutoff_state, s.verification_state) for s, _ in rows
        ),
    }
    return NumericSeriesObservation(
        project_id=document.artifact.project_id,
        artifact_id=document.artifact.artifact_id,
        source_version_id=rows[0][0].source_version_id,
        source_byte_sha256=document.artifact.byte_sha256,
        label=label,
        size_label=axis,
        unit=unit,
        parser_version=document.artifact.parser_version,
        observer_version=document.context_observation.observer_version,
        context_label=" / ".join(s.exact_text for s in conditions),
        region_ref=region.region_id,
        source_refs=source_refs,
        points=tuple(points),
        series_digest=series_digest,
        points_digest=points_digest,
        binding_digest=domain_digest("NUMERIC_BINDING", "1.0.0", canonical_payload(binding)),
        unit_registry_digest=calculator.unit_registry_digest,
    )


def _region_series(
    request: RevisionRef,
    document: StructuralDocument,
    region: ContextRegion,
    visible: dict[str, EvidenceSpan],
    regions: dict[str, ContextRegion],
    calculator: DocumentCalculatorPort,
) -> tuple[list[NumericSeriesObservation], list[NumericDiagnostic]]:
    members = [visible[n] for n in region.node_refs if n in visible]
    body = [
        s
        for s in members
        if not (
            region.kind == "FENCED_CODE"
            and (
                s.locator.line == region.line_start
                or (region.closure == "CLOSED" and s.locator.line == region.line_end - 1)
            )
        )
    ]
    matched = [(s, _ROW.fullmatch(s.exact_text)) for s in body]
    if not any(m is not None for _, m in matched):
        return [], []
    refs = tuple(s.span_id for s in members)
    required = tuple(
        dict.fromkeys(
            n for k in (*region.heading_refs, *region.leading_refs) for n in regions[k].node_refs
        )
    )
    reason = (
        "NUMERIC_BOUNDARY_UNCONFIRMED"
        if region.kind == "FENCED_CODE" and region.closure != "CLOSED"
        else "NUMERIC_CONTEXT_INCOMPLETE"
        if not set((*region.node_refs, *required)) <= visible.keys()
        else "NUMERIC_CONTEXT_LABEL_UNAVAILABLE"
        if not required
        else "NUMERIC_BLOCK_AMBIGUOUS"
        if any(m is None for _, m in matched)
        else None
    )
    if reason:
        return [], [NumericDiagnostic(reason=reason, source_refs=refs)]
    groups: dict[tuple[str, str], list[tuple[EvidenceSpan, re.Match[str]]]] = defaultdict(list)
    for span, match in matched:
        if match is not None:
            groups[(match["label"].strip(), match["axis"])].append((span, match))
    result: list[NumericSeriesObservation] = []
    diagnostics: list[NumericDiagnostic] = []
    for (label, _), rows in groups.items():
        try:
            result.append(
                _series(
                    request, document, region, tuple(visible[n] for n in required), rows, calculator
                )
            )
        except NumericObservationUnsupported as exc:
            diagnostics.append(
                NumericDiagnostic(
                    reason=str(exc), source_refs=tuple(s.span_id for s, _ in rows), label=label
                )
            )
    return result, diagnostics


def observe_numeric_series(
    request: RevisionRef,
    evidence: tuple[EvidenceSpan, ...],
    artifacts: ArtifactLedgerPort,
    calculator: DocumentCalculatorPort,
) -> tuple[tuple[NumericSeriesObservation, ...], tuple[NumericDiagnostic, ...]]:
    groups: dict[tuple[str, str, str], list[EvidenceSpan]] = defaultdict(list)
    for span in connected_retrieval_spans(evidence):
        if span.project_id != request.project_id:
            raise ValueError("NUMERIC_PROJECT_MISMATCH")
        groups[(span.project_id, span.artifact_id, span.source_version_id)].append(span)
    series: list[NumericSeriesObservation] = []
    diagnostics: list[NumericDiagnostic] = []
    for key, spans in sorted(groups.items()):
        document = artifacts.read_structure(*key)
        observation = source_observation(document, spans[0])
        if document is None or observation is None or observation.status != "OBSERVED":
            if any(_ROW.fullmatch(s.exact_text) for s in spans):
                diagnostics.append(
                    NumericDiagnostic(
                        reason="NUMERIC_CONTEXT_UNAVAILABLE",
                        source_refs=tuple(s.span_id for s in spans),
                    )
                )
            continue
        nodes = {n.node_id: n for n in document.nodes}
        if observation.warnings:
            if any(_ROW.fullmatch(s.exact_text) for s in spans):
                diagnostics.append(
                    NumericDiagnostic(
                        reason="NUMERIC_CONTEXT_UNCERTAIN",
                        source_refs=tuple(s.span_id for s in spans),
                    )
                )
            continue
        visible: dict[str, EvidenceSpan] = {}
        for span in spans:
            node = nodes.get(span.locator.structural_node_id or "")
            if (
                node is None
                or node.text != span.exact_text
                or hashlib.sha256(span.exact_text.encode()).hexdigest() != span.text_sha256
            ):
                raise ValueError("NUMERIC_SOURCE_BASIS_MISMATCH")
            visible[node.node_id] = span
        regions = {r.region_id: r for r in observation.regions}
        for region in sorted(observation.regions, key=lambda r: (r.line_start, r.line_end)):
            if region.kind not in {"FENCED_CODE", "PARAGRAPH"}:
                continue
            found, issues = _region_series(request, document, region, visible, regions, calculator)
            series.extend(found)
            diagnostics.extend(issues)
    return tuple(series), tuple(diagnostics)
