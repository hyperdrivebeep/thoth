"""The public methods that only read: they are served by the query route, never the command bus."""

from __future__ import annotations

READ_QUERY_METHODS = frozenset(
    {
        "revision/timeline/read",
        "revision/restore/preview",
        "revision/diff/read",
        "revision/history/read",
        "revision/read",
        "revision/content/read",
        "revision/head/read",
        "revision/timeline/item/read",
        "thread/result/read",
        "thread/result/compare/read",
        "thread/read",
        "thread/list",
        "thread/activity/list",
        "model/settings/read",
        "model/credential/list",
        "model/credential/login/status",
        "trace/read",
        "trace/history",
        "trace/export",
        "trace/importPreview",
        "trace/closure/list",
        "trace/lesson/list",
        "hypothesis/link/list",
        "hypothesis/test/result/list",
        "hypothesis/same/list",
        "workspace/setup/read",
        "workspace/ready",
        "project/read",
        "project/list",
        "project/review/list",
        "project/source/list",
        "evidence/list",
        "operation/read",
        "operation/result/read",
        "operation/checkpoint/read",
    }
)
