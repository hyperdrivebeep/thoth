from __future__ import annotations

import hashlib
import shlex
from pathlib import Path

from pydantic import JsonValue

from thoth.application.services.model_settings_conversation import parse_model_command
from thoth.domain.conversation import (
    ConversationIntent,
    ConversationIntentCandidate,
    ConversationIntentState,
    TuiSessionState,
)


class ConversationRouter:
    def route(self, raw: str, session: TuiSessionState) -> ConversationIntentCandidate:
        text = raw.strip()
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if not text:
            return self._hold(digest, "Input is empty.")
        if text.startswith("/"):
            return self._slash(text, session, digest)
        if session.active_project_id is None:
            return self._hold(
                digest,
                "Select a project with /project <project_id> before starting work.",
                missing=("active_project_id",),
            )
        if session.active_thread_id is None:
            return self._ready(
                intent=ConversationIntent.START_THREAD,
                method="thread/start",
                arguments={
                    "project_id": session.active_project_id,
                    "problem": text,
                    "scope": {},
                    "contract_version": 2,
                },
                digest=digest,
                message="Start a new Thread from this problem statement.",
            )
        return self._ready(
            intent=ConversationIntent.CONTINUE_THREAD,
            method="thread/input",
            arguments={
                "project_id": session.active_project_id,
                "thread_id": session.active_thread_id,
                "instruction": text,
                "contract_version": 2,
            },
            digest=digest,
            message="Continue the active Thread with this instruction.",
        )

    def _slash(
        self,
        text: str,
        session: TuiSessionState,
        digest: str,
    ) -> ConversationIntentCandidate:
        try:
            parts = shlex.split(text, posix=True)
        except ValueError:
            return self._hold(digest, "Command quoting is invalid.")
        command = parts[0].casefold()
        args = parts[1:]
        if command in {"/model", "/reasoning"}:
            try:
                intent, method, arguments = parse_model_command(command, args, session)
                return self._ready(
                    intent=intent,
                    method=method,
                    arguments=arguments,
                    digest=digest,
                    message="Model settings for subsequent research requests.",
                )
            except ValueError as exc:
                return self._hold(digest, str(exc))
        if command == "/project":
            return self._project_command(args, digest)
        if command == "/thread":
            if session.active_project_id is None:
                return self._hold(
                    digest,
                    "Select a project before selecting or starting a Thread.",
                    missing=("active_project_id",),
                )
            if len(args) == 2 and args[0] == "use":
                return self._ready(
                    intent=ConversationIntent.SELECT_THREAD,
                    method="thread/read",
                    arguments={
                        "project_id": session.active_project_id,
                        "thread_id": args[1],
                    },
                    digest=digest,
                    message="Select an existing Thread.",
                )
            problem = " ".join(args).strip()
            if problem:
                return self._ready(
                    intent=ConversationIntent.START_THREAD,
                    method="thread/start",
                    arguments={
                        "project_id": session.active_project_id,
                        "problem": problem,
                        "scope": {},
                        "contract_version": 2,
                    },
                    digest=digest,
                    message="Start a new Thread.",
                )
            return self._hold(digest, "Use /thread <problem> or /thread use <thread_id>.")
        if command == "/new" and not args:
            if session.active_project_id is None:
                return self._hold(
                    digest,
                    "Select a project before starting a new Thread.",
                    missing=("active_project_id",),
                )
            return self._ready(
                intent=ConversationIntent.NEW_THREAD_CONTEXT,
                method=None,
                arguments={"project_id": session.active_project_id},
                digest=digest,
                message="Start a new Thread with the next problem statement.",
            )
        if command == "/source":
            if session.active_project_id is None:
                return self._hold(digest, "Select a project before connecting a source.")
            if not args or any(
                flag not in {"--project-shared", "--eligible", "--informal"} for flag in args[1:]
            ):
                return self._hold(
                    digest, "Use /source <path> [--project-shared] [--eligible] [--informal]."
                )
            return self._source_command(args, session.active_project_id, digest)
        scoped = self._scoped(session, digest)
        if scoped is not None:
            return scoped
        project_id = session.active_project_id
        thread_id = session.active_thread_id
        assert project_id is not None
        assert thread_id is not None
        if command == "/status" and not args:
            return self._thread_command(
                ConversationIntent.ASK_STATUS,
                "thread/read",
                project_id,
                thread_id,
                digest,
                "Read active Thread status.",
            )
        if command == "/history" and not args:
            return self._thread_command(
                ConversationIntent.ASK_STATUS,
                "thread/activity/list",
                project_id,
                thread_id,
                digest,
                "Read active Thread activity history.",
            )
        if command == "/compare" and len(args) == 2:
            return self._ready(
                intent=ConversationIntent.COMPARE_REVISION,
                method="revision/diff/read",
                arguments={
                    "project_id": project_id,
                    "from_revision_digest": args[0],
                    "to_revision_digest": args[1],
                },
                digest=digest,
                message="Compare two immutable revisions.",
            )
        if command == "/restore" and len(args) == 3:
            return self._ready(
                intent=ConversationIntent.RESTORE_PREVIEW,
                method="revision/restore/preview",
                arguments={
                    "project_id": project_id,
                    "aggregate_id": args[0],
                    "target_revision_digest": args[1],
                    "current_head_digest": args[2],
                },
                digest=digest,
                message="Preview restore as a new revision; no restore is committed.",
            )
        if command == "/pause" and not args:
            return self._thread_command(
                ConversationIntent.PAUSE_THREAD,
                "thread/pause",
                project_id,
                thread_id,
                digest,
                "Pause the active Thread.",
            )
        if command == "/resume" and not args:
            return self._thread_command(
                ConversationIntent.RESUME_THREAD,
                "thread/resume",
                project_id,
                thread_id,
                digest,
                "Resume the active Thread.",
            )
        if command == "/stop" and not args:
            return self._thread_command(
                ConversationIntent.STOP_THREAD,
                "thread/stop",
                project_id,
                thread_id,
                digest,
                "Stop the active Thread and record a checkpoint.",
            )
        if command == "/retry" and not args:
            return self._ready(
                intent=ConversationIntent.RETRY_THREAD,
                method="thread/read",
                arguments={"project_id": project_id, "thread_id": thread_id},
                digest=digest,
                message="Retry the stored question as a new attempt without appending text.",
            )
        if command == "/usage" and not args:
            return self._ready(
                intent=ConversationIntent.REFRESH_USAGE,
                method="thread/read",
                arguments={
                    "project_id": project_id,
                    "thread_id": thread_id,
                    "refresh_account_quota": True,
                },
                digest=digest,
                message="Refresh observed account quota without starting research.",
            )
        if command == "/export" and not args:
            return self._ready(
                intent=ConversationIntent.EXPORT_LOCAL,
                method="export/list",
                arguments={"project_id": project_id},
                digest=digest,
                message="List local export packages. External release is not performed.",
            )
        return self._hold(digest, "Unknown or incomplete command. Use /help.")

    def _source_command(
        self, args: list[str], project_id: str, digest: str
    ) -> ConversationIntentCandidate:
        suffix = Path(args[0]).suffix.casefold()
        media_type = (
            "text/markdown"
            if suffix in {".md", ".markdown"}
            else "text/html"
            if suffix in {".html", ".htm"}
            else "text/plain"
        )
        return self._ready(
            intent=ConversationIntent.ADD_SOURCE,
            method="project/source/connect",
            arguments={
                "project_id": project_id,
                "connector_id": "local-file-upload",
                "relative_path": args[0],
                "media_type": media_type,
                "authority": "INFORMAL" if "--informal" in args else "UNCLASSIFIED",
                "cutoff_state": "ELIGIBLE" if "--eligible" in args else "UNKNOWN_TIME",
                "security_class": "INTERNAL",
                **(
                    {
                        "resource_scope": {
                            "owner_kind": "PROJECT",
                            "visibility": "PROJECT_SHARED",
                        }
                    }
                    if "--project-shared" in args
                    else {}
                ),
            },
            digest=digest,
            message="Connect a bounded local source; policy still decides eligibility.",
        )

    def _project_command(self, args: list[str], digest: str) -> ConversationIntentCandidate:
        if args == ["list"] or not args:
            return self._ready(
                intent=ConversationIntent.ASK_STATUS,
                method="project/list",
                arguments={"project_id": "system:projects"},
                digest=digest,
                message="List local projects.",
            )
        if args[0] == "create":
            if len(args) != 4:
                return self._hold(
                    digest,
                    'Use /project create <project_id> "<name>" <cutoff-iso8601>.',
                )
            return self._ready(
                intent=ConversationIntent.SELECT_PROJECT,
                method="project/create",
                arguments={"project_id": args[1], "name": args[2], "cutoff_at": args[3]},
                digest=digest,
                message="Create and select a local project.",
            )
        if len(args) == 1:
            return self._ready(
                intent=ConversationIntent.SELECT_PROJECT,
                method="project/read",
                arguments={"project_id": args[0]},
                digest=digest,
                message="Select an existing project.",
            )
        return self._hold(digest, "Use /project <project_id>, /project list, or /project create.")

    @staticmethod
    def _scoped(
        session: TuiSessionState,
        digest: str,
    ) -> ConversationIntentCandidate | None:
        missing = tuple(
            field
            for field, value in (
                ("active_project_id", session.active_project_id),
                ("active_thread_id", session.active_thread_id),
            )
            if value is None
        )
        if not missing:
            return None
        return ConversationRouter._hold(
            digest,
            "Select an active project and Thread before using this command.",
            missing=missing,
        )

    @staticmethod
    def _thread_command(
        intent: ConversationIntent,
        method: str,
        project_id: str,
        thread_id: str,
        digest: str,
        message: str,
    ) -> ConversationIntentCandidate:
        return ConversationRouter._ready(
            intent=intent,
            method=method,
            arguments={"project_id": project_id, "thread_id": thread_id},
            digest=digest,
            message=message,
        )

    @staticmethod
    def _ready(
        *,
        intent: ConversationIntent,
        method: str | None,
        arguments: dict[str, JsonValue],
        digest: str,
        message: str,
    ) -> ConversationIntentCandidate:
        return ConversationIntentCandidate(
            intent=intent,
            state=ConversationIntentState.READY,
            method=method,
            arguments=arguments,
            display_message=message,
            raw_input_digest=digest,
        )

    @staticmethod
    def _hold(
        digest: str,
        message: str,
        *,
        missing: tuple[str, ...] = (),
    ) -> ConversationIntentCandidate:
        return ConversationIntentCandidate(
            intent=ConversationIntent.UNKNOWN_HOLD,
            state=ConversationIntentState.HOLD,
            missing_fields=missing,
            display_message=message,
            raw_input_digest=digest,
        )
