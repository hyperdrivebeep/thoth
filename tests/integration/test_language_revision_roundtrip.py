"""New requests can change language without mutating earlier request bytes or thread identity."""

from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.domain.canonical import canonical_payload
from thoth.domain.research_codec import decode_research_record


@pytest.mark.asyncio
async def test_legacy_ko_en_ko_revisions_round_trip_and_preserve_the_legacy_snapshot(
    tmp_path: Path,
):
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    thread_id = None
    saved = []
    try:
        for index, language in enumerate((None, "ko", "en", "ko")):
            payload = {
                "project_id": "p",
                "contract_version": 2,
                **({"response_language": language} if language else {}),
            }
            payload.update(
                {"thread_id": thread_id, "instruction": f"Follow-up {index}"}
                if thread_id
                else {"problem": "Original question"}
            )
            accepted = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/input" if thread_id else "thread/start",
                        f"language:{index}",
                        payload,
                    )
                )
            )
            thread_id = accepted["thread_id"]
            await runtime.bus.drain()
            view = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/read",
                        f"read:{index}",
                        {
                            "project_id": "p",
                            "thread_id": thread_id,
                            "view": "FULL",
                            "expected_result_contract": "v4",
                        },
                    )
                )
            )
            record = view["request"]
            assert record["request_epoch"] == index + 1
            assert record["thread_id"] == thread_id
            if language is None:
                assert record["schema_version"] == "2.0.0" and "response_language" not in record
            else:
                assert (
                    record["schema_version"] == "2.1.0" and record["response_language"] == language
                )
                assert view["current_result"]["result"]["response_language"] == language
            encoded = canonical_payload(record)
            assert canonical_payload(decode_research_record(record)) == encoded
            saved.append(encoded)
        revisions = runtime.ledger.read_revisions("p", "THREAD", f"request:{thread_id}")
        snapshots = [runtime.ledger.read_snapshot(revision.snapshot_id) for revision in revisions]
        assert len(snapshots) == 4
        assert {canonical_payload(snapshot.content) for snapshot in snapshots} == set(saved)
        assert all(
            canonical_payload(decode_research_record(dict(snapshot.content))) in saved
            for snapshot in snapshots
        )
    finally:
        runtime.close()
