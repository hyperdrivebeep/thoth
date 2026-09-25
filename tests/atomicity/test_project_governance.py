from pathlib import Path

from sqlalchemy import inspect, text
from tests.integration.storage_coverage_helpers import prepare_project, request, value


async def test_project_metadata_retains_authoritative_before_after_history(tmp_path: Path) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        before = value(
            await runtime.bus.query(request("project/read", "before", {"project_id": project}))
        )
        after = value(
            await runtime.bus.dispatch(
                request(
                    "project/metadata/update",
                    "rename",
                    {
                        "project_id": project,
                        "expected_revision": before["revision"],
                        "name": "Renamed project",
                    },
                )
            )
        )
        assert after["name"] == "Renamed project"
        assert "governance_revisions" in inspect(runtime.ledger.engine).get_table_names(), (
            "Project metadata needs authoritative history; operation results are not its owner"
        )
        with runtime.ledger.engine.connect() as connection:
            history = (
                connection.execute(
                    text(
                        "SELECT content_json FROM governance_revisions "
                        "WHERE project_id=:project AND record_kind='PROJECT' ORDER BY revision"
                    ),
                    {"project": project},
                )
                .scalars()
                .all()
            )
        assert len(history) == 2
        assert before["name"] in history[0] and "Renamed project" in history[1]
    finally:
        runtime.close()


async def test_project_archive_hides_from_default_list_but_preserves_readback(
    tmp_path: Path,
) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        before = value(
            await runtime.bus.query(
                request("project/read", "archive-before", {"project_id": project})
            )
        )
        archived = value(
            await runtime.bus.dispatch(
                request(
                    "project/archive",
                    "archive-project",
                    {"project_id": project, "expected_revision": before["revision"]},
                )
            )
        )
        assert archived["lifecycle"] == "ARCHIVED_READ_ONLY"
        assert archived["revision"] == before["revision"] + 1

        default_list = value(
            await runtime.bus.query(
                request("project/list", "list-default", {"project_id": "system:projects"})
            )
        )
        assert default_list["projects"] == []

        archived_list = value(
            await runtime.bus.query(
                request(
                    "project/list",
                    "list-archived",
                    {"project_id": "system:projects", "include_archived": True},
                )
            )
        )
        assert [item["project_id"] for item in archived_list["projects"]] == [project]

        readback = value(
            await runtime.bus.query(
                request("project/read", "archive-readback", {"project_id": project})
            )
        )
        assert readback["lifecycle"] == "ARCHIVED_READ_ONLY"
    finally:
        runtime.close()


async def test_archived_project_rejects_metadata_mutation(tmp_path: Path) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        before = value(
            await runtime.bus.query(
                request("project/read", "archive-before", {"project_id": project})
            )
        )
        archived = value(
            await runtime.bus.dispatch(
                request(
                    "project/archive",
                    "archive-project",
                    {"project_id": project, "expected_revision": before["revision"]},
                )
            )
        )

        rejected = await runtime.bus.dispatch(
            request(
                "project/metadata/update",
                "rename-archived",
                {
                    "project_id": project,
                    "expected_revision": archived["revision"],
                    "name": "Should not change",
                },
            )
        )

        assert rejected.error is not None
        assert rejected.error.message == "ARCHIVED_READ_ONLY"
        readback = value(
            await runtime.bus.query(
                request("project/read", "archive-readback", {"project_id": project})
            )
        )
        assert readback["name"] == before["name"]
        assert readback["revision"] == archived["revision"]
        assert readback["lifecycle"] == "ARCHIVED_READ_ONLY"
    finally:
        runtime.close()
