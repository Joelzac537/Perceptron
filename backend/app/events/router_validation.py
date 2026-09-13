"""Pure validation of a routing decision; never mutates or executes it.

This checks the shape and internal consistency of a `RouteEventResponse` — that it only
names loops it was allowed to consider, that it is ordered, that it does not contradict
itself. It does not check whether the routing decision was *correct*; nothing here knows
what the event said. Deciding whether the event actually proves anything about a loop is
the Evidence Verifier's job.

Follows the house pattern in `app/graph/semantic_validation.py`: collect every issue, then
raise once with the full list, so a caller sees all problems in one pass.
"""

from collections.abc import Collection

from app.graph.schemas import RouteEventResponse


class RoutingValidationError(ValueError):
    """All detected routing issues, raised as one list rather than one at a time."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(issues))


def validate_route_response(
    response: RouteEventResponse, candidate_loop_ids: Collection[str]
) -> RouteEventResponse:
    """Return the unchanged response or raise with every deterministic issue found.

    `candidate_loop_ids` is the set the router was entitled to choose from. For a normal
    route that is the routable loops returned by the repository; for the pre-linked
    short-circuit it is the single `linked_loop_id`. Passing it explicitly is what makes
    "the router invented a loop id" and "the router leaked another tenant's loop"
    detectable here rather than three layers downstream.
    """
    issues: list[str] = []
    allowed = set(candidate_loop_ids)

    def check(condition: bool, message: str) -> None:
        if not condition:
            issues.append(message)

    seen: set[str] = set()
    for position, match in enumerate(response.matches):
        check(
            match.loop_id in allowed,
            f"matches[{position}] names loop {match.loop_id!r}, which was not a candidate",
        )
        check(
            match.loop_id not in seen,
            f"matches[{position}] repeats loop {match.loop_id!r}",
        )
        seen.add(match.loop_id)
        # LoopMatch.reason is NonEmpty with pattern \S, so a blank reason cannot survive
        # construction. Checked anyway: this is the invariant that keeps a match
        # explainable to a user, and it should fail here rather than at the wire edge.
        check(
            bool(match.reason.strip()),
            f"matches[{position}] for loop {match.loop_id!r} has a blank reason",
        )

    confidences = [match.confidence for match in response.matches]
    for position, (earlier, later) in enumerate(zip(confidences, confidences[1:])):
        check(
            earlier >= later,
            f"matches are not sorted by confidence descending: "
            f"matches[{position}]={earlier} precedes matches[{position + 1}]={later}",
        )

    check(
        not (response.matches and response.create_new_loop_candidate),
        "create_new_loop_candidate must be False when matches is non-empty: an event that "
        "belongs to an existing loop is not also a new obligation",
    )

    if issues:
        raise RoutingValidationError(issues)
    return response
