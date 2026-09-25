"""Shared pure ActionPlan validation for authoring and historical restoration."""


def validate_action_plan_dag(
    steps: tuple[dict[str, object], ...], edges: tuple[dict[str, str], ...]
) -> None:
    ids = {str(item["step_id"]) for item in steps}
    if len(ids) != len(steps):
        raise ValueError("ActionPlan step IDs must be unique")
    adjacency: dict[str, list[str]] = {item: [] for item in ids}
    indegree = {item: 0 for item in ids}
    for edge in edges:
        source = edge.get("from")
        target = edge.get("to")
        if source not in ids or target not in ids or source == target:
            raise ValueError("ActionPlan dependency edge is invalid")
        adjacency[source].append(target)
        indegree[target] += 1
    queue = [item for item, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        for target in adjacency[node]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(ids):
        raise ValueError("ActionPlan must be an acyclic DAG")
