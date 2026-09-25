from pathlib import Path
from typing import cast

from pydantic import JsonValue
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.domain.user_activity import UserActivityEvent


async def test_normal_thread_read_returns_safe_typed_user_activity_without_replacing_old_fields(
    tmp_path: Path,
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=True)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "user-activity",
                    {
                        "project_id": "p",
                        "problem": "Compare the connected source with the planned condition",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        status = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "user-activity-read",
                    {
                        "project_id": "p",
                        "thread_id": accepted["thread_id"],
                        "contract_version": 2,
                    },
                )
            )
        )

        assert isinstance(status["completed_stages"], list)
        assert isinstance(status["activity_events"], list)
        assert isinstance(status["user_activity_events"], list)
        assert status["user_activity_events"]
        event_payloads = cast(list[dict[str, JsonValue]], status["user_activity_events"])
        assert [event["seq"] for event in event_payloads] == list(
            range(1, len(event_payloads) + 1)
        )
        validated = [
            UserActivityEvent.model_validate(event) for event in event_payloads
        ]
        assert any(event.activity_kind == "source" for event in validated)
        assert any(event.activity_kind == "model" for event in validated)
        assert all(
            event.tool is None or not event.tool.raw_command_available for event in validated
        )
        assert all(event.redaction.source_content_included is False for event in validated)
    finally:
        runtime.close()
