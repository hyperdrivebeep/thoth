"""Source-free investigation questions stay private to their creator."""

from pathlib import Path

from tests.integration.resource_scope_helpers import scope_harness, value


async def test_private_investigation_question_and_audit_are_not_peer_visible(
    tmp_path: Path,
) -> None:
    async with scope_harness(tmp_path) as h:
        value(
            await h.call(
                "alpha",
                "thread/start",
                "private-investigation",
                {
                    "thread_id": "thread:private-investigation",
                    "problem": "private investigation marker",
                    "scope": {"workstream": "alpha"},
                },
            )
        )
        own = value(await h.call("alpha", "investigation/list", "own-list", {}))["investigations"]
        assert len(own) == 1
        query = {"investigation_id": own[0]["investigation_id"]}
        peer = await h.call("beta", "investigation/read", "peer-read", query)
        assert "error" in peer.json(), peer.json()
        assert not value(await h.call("beta", "investigation/list", "peer-list", {}))[
            "investigations"
        ]
        audit = await h.call("beta", "investigation/audit/read", "peer-audit", query)
        assert "error" in audit.json(), audit.json()
