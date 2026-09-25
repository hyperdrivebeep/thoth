from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a02_autonomous_acquisition import prepare_thread

from thoth.apps.runtime import AppRuntime

TARGET = {
    "metric_definition": "latency",
    "formula": "elapsed time",
    "unit": "ms",
    "denominator": "per-request",
    "population": "declared test population",
    "environment": "controlled test environment",
    "time_window": "trial-v1",
    "measurement_method": "elapsed-time observation",
}


@dataclass
class ReferenceHarness:
    runtime: AppRuntime
    project: str
    thread: str
    criterion: dict[str, Any]
    request: dict[str, Any]

    async def input(self, key: str, **extra: object):
        return await self.runtime.bus.dispatch(
            request(
                "thread/input", key, {"project_id": self.project, "thread_id": self.thread, **extra}
            )
        )

    async def read(self, key: str = "read-current") -> dict[str, Any]:
        return value(
            await self.runtime.bus.dispatch(
                request(
                    "criteria/read",
                    key,
                    {"project_id": self.project, "criterion_id": self.criterion["criterion_id"]},
                )
            )
        )["criterion"]


@asynccontextmanager
async def reference_harness(
    tmp_path: Path,
    *,
    target: dict[str, str] | None = None,
    observations: tuple[tuple[str, str, str, str | None], ...] = (
        ("reference-a.md", "120", "ms", None),
        ("reference-b.md", "0.14", "s", None),
    ),
) -> AsyncGenerator[ReferenceHarness]:
    runtime, connector, project = await prepare_thread(tmp_path, allow_connector=True)
    thread = f"thread:{project}"
    measurements: list[dict[str, Any]] = []
    target_context = TARGET if target is None else target
    try:
        for name, number, unit, variable in observations:
            context = {**target_context, "unit": unit}
            lines = [f"{field}: {value}" for field, value in context.items()]
            value_quote = f"{variable or 'value'}: {number} {unit}"
            text = "# Observed measurement\n\n" + "\n".join((*lines, value_quote)) + "\n"
            connector.payloads[name] = text.encode()
            value(
                await runtime.bus.dispatch(
                    request(
                        "project/source/connect",
                        "connect-" + name,
                        {
                            "project_id": project,
                            "connector_id": "a02-readonly",
                            "selector": {"relative_path": name},
                            "media_type": "text/markdown",
                            "authority": "OFFICIAL",
                            "cutoff_state": "ELIGIBLE",
                            "security_class": "INTERNAL",
                        },
                    )
                )
            )
            evidence = value(
                await runtime.bus.dispatch(
                    request("evidence/list", "list-" + name, {"project_id": project})
                )
            )["spans"]
            span = next(s for s in evidence if value_quote in s["exact_text"])
            field_spans = {
                field: next(
                    s["span_id"]
                    for s in evidence
                    if s["artifact_id"] == span["artifact_id"]
                    and f"{field}: {v}" in s["exact_text"]
                )
                for field, v in context.items()
            }
            measurements.append(
                {
                    "span_id": span["span_id"],
                    "value": number,
                    "context": context,
                    "quotes": {field: f"{field}: {v}" for field, v in context.items()},
                    "field_span_ids": field_spans,
                    "value_quote": value_quote,
                    "variable": variable,
                }
            )
        refs = list(
            dict.fromkeys(
                ref for m in measurements for ref in (m["span_id"], *m["field_span_ids"].values())
            )
        )
        criterion = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/compile",
                    "compile-reference",
                    {
                        "project_id": project,
                        "thread_id": thread,
                        "source_span_ids": refs,
                        "profile_refs": ["GENERAL_RND"],
                    },
                )
            )
        )["criterion"]
        body = {
            "criterion_id": criterion["criterion_id"],
            "expected_revision_digest": criterion["revision_digest"],
            "lane": "REFERENCE_RANGE_CANDIDATE",
            "source_refs": refs,
            "calculator_id": "observed-range",
            "calculator_version": "1.0.0",
            "target": dict(target_context),
            "measurements": measurements,
        }
        yield ReferenceHarness(runtime, project, thread, criterion, body)
    finally:
        runtime.close()
