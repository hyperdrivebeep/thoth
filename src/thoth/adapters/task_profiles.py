"""Explicit registration of the initial task shapes; additions need no compiler branch."""

from thoth.domain.task_profile import TaskProfileRecord, TaskProfileRule


class TaskProfileRegistry:
    def __init__(self, profiles: tuple[TaskProfileRecord, ...] = ()) -> None:
        self._profiles: dict[str, TaskProfileRecord] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: TaskProfileRecord) -> None:
        if profile.profile_ref in self._profiles:
            raise ValueError("TASK_PROFILE_ALREADY_REGISTERED")
        self._profiles[profile.profile_ref] = profile

    def profiles(self) -> tuple[TaskProfileRecord, ...]:
        return tuple(self._profiles.values())


def default_task_profiles() -> TaskProfileRegistry:
    definitions = {
        "DOCUMENT_QUESTION:1": (
            ("answer", "Requested target, fields, source locator and adjacent context"),
            ("time", "Requested time and source applicability"),
        ),
        "CONDITION_COMPARISON:1": (
            ("comparison", "Both records' method, conditions, units and period"),
            ("conversion", "Authority and validity of any required conversion"),
        ),
        "CAUSE_EXPLORATION:1": (
            ("observation", "Problem and observed scope"),
            ("counterevidence", "Discriminating predictions and counterevidence"),
            ("test", "Applicable test profile before causal promotion"),
        ),
    }
    return TaskProfileRegistry(
        tuple(
            TaskProfileRecord(
                profile_ref=ref,
                version=1,
                authority_ref="WORK_ORDER:U04:initial-task-shapes",
                rules=tuple(
                    TaskProfileRule(
                        rule_id=target,
                        target=target,
                        question=question,
                        counterevidence_required=target == "counterevidence",
                    )
                    for target, question in rules
                ),
            )
            for ref, rules in definitions.items()
        )
    )
