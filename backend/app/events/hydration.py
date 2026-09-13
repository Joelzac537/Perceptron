"""Turn stored rows into a `VerifyEventRequest` the Evidence Verifier will accept.

The router answers "which loop", in terms of a deliberately flat `LoopSummary`. The
verifier needs something else entirely: the authoritative `Loop`, the candidate nodes, and
every requirement those nodes reference. This module is the bridge, and it is the reason
the router can stay flat.

Three rules from the schema review (`docs/LoopGraph_08_Schema_Review.md`) govern the
mapping, and each is a way to build a graph that looks authoritative while lying:

  * `Loop.node_ids`, `OutcomeNode.depends_on` and the requirement/evidence/action id arrays
    are **derived**. They are not columns. Rebuild them from the child tables and the edges
    or they will disagree with the authoritative graph.
  * A `DEPENDS_ON` edge points from the dependent to its prerequisite, so `depends_on` is
    collected from edges whose *source* is the node.
  * The `events` table carries an extra `created_at` that the strict `Event` DTO forbids.
    It must be dropped, not passed through.

Read-only and synchronous once the rows are in hand: no writes, no clock, no model.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Final, Protocol, runtime_checkable

from app.constants import EdgeType
from app.graph.schemas import (
    Event,
    EvidenceRequirement,
    Loop,
    OutcomeNode,
    VerifyEventRequest,
)

# Columns the SQL carries that the strict DTOs forbid. `extra="forbid"` turns each of
# these into a hard error rather than a silently ignored key, which is the point.
EVENT_ONLY_COLUMNS: Final[frozenset[str]] = frozenset({"created_at"})

# Node fields the caller must not supply, because this module derives them. Anything a row
# claims here is stale by construction.
DERIVED_NODE_FIELDS: Final[frozenset[str]] = frozenset(
    {"depends_on", "evidence_requirement_ids", "evidence_ids", "action_ids"}
)

# Loop fields derived the same way.
DERIVED_LOOP_FIELDS: Final[frozenset[str]] = frozenset({"node_ids", "source_event_ids"})


class HydrationError(ValueError):
    """All detected hydration issues, raised once with the full list.

    Mirrors `GraphValidationError` and `RoutingValidationError`; the runtime should treat
    it as a caller-side input error, never as something a model can repair.
    """

    def __init__(self, issues: list[str]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(issues))


@runtime_checkable
class LoopGraphSource(Protocol):
    """Loads one loop's full graph. Separate from `LoopRepository` on purpose.

    `LoopRepository` serves routing: many loops, shallow, no graph. This serves
    verification: one loop, complete. Keeping them apart is what stops routing paying for
    a graph reconstruction it never reads.
    """

    async def load_loop_graph(self, user_id: str, loop_id: str) -> dict[str, Any] | None:
        """Every row for one loop, or None when it does not exist for this user.

        Shape: `{"loop": {...}, "nodes": [...], "edges": [...], "requirements": [...]}`.

            select l.*, n.*, e.*, r.*
            from loops l
            left join outcome_nodes n on n.loop_id = l.id
            left join edges e on e.loop_id = l.id
            left join evidence_requirements r on r.node_id = n.id
            where l.id = $1 and l.user_id = $2

        The `user_id` filter is not optional. `Event` carries no user, so this is the only
        place tenancy can be enforced on the verification path.
        """
        ...


def _without(row: Mapping[str, Any], drop: frozenset[str]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in drop}


def build_event(row: Mapping[str, Any]) -> Event:
    """Build the strict `Event` DTO from an `events` row.

    Drops `EVENT_ONLY_COLUMNS`. Every other unknown key is left in place so that
    `extra="forbid"` still reports it — silently discarding unrecognised columns would hide
    a schema drift that someone needs to see.
    """
    return Event.model_validate(_without(row, EVENT_ONLY_COLUMNS))


def build_verify_request(
    rows: Mapping[str, Any],
    event: Event,
    *,
    candidate_node_ids: Sequence[str] | None = None,
) -> VerifyEventRequest:
    """Assemble a `VerifyEventRequest` from one loop's rows.

    `candidate_node_ids` narrows which nodes are assessed; the default is every node in the
    loop. Whatever the selection, **all** requirements referenced by those nodes are
    included, because the verifier rejects a partial requirement set.

    Raises `HydrationError` with every problem found, in the house collect-then-raise
    style, rather than producing a graph that disagrees with its own edges.
    """
    issues: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            issues.append(message)

    loop_row = rows.get("loop") or {}
    node_rows: list[Mapping[str, Any]] = list(rows.get("nodes") or [])
    edge_rows: list[Mapping[str, Any]] = list(rows.get("edges") or [])
    requirement_rows: list[Mapping[str, Any]] = list(rows.get("requirements") or [])

    loop_id = loop_row.get("id")
    check(bool(loop_id), "Loop row has no id")
    check(bool(node_rows), "A loop with no nodes cannot be verified")

    all_node_ids = [row["id"] for row in node_rows]
    check(len(all_node_ids) == len(set(all_node_ids)), "Duplicate node ids in the loop")

    root_node_id = loop_row.get("root_node_id")
    check(
        root_node_id in all_node_ids,
        f"Root node {root_node_id!r} is not among the loop's nodes",
    )

    # A DEPENDS_ON edge runs dependent -> prerequisite, so the dependent is the source.
    depends_on: dict[str, list[str]] = {node_id: [] for node_id in all_node_ids}
    for edge in edge_rows:
        if edge.get("relationship") != EdgeType.DEPENDS_ON:
            continue
        source, target = edge.get("source_node_id"), edge.get("target_node_id")
        if source not in depends_on:
            check(False, f"Edge {edge.get('id')!r} starts outside the loop: {source!r}")
            continue
        if target not in depends_on:
            check(False, f"Edge {edge.get('id')!r} ends outside the loop: {target!r}")
            continue
        if target not in depends_on[source]:
            depends_on[source].append(target)

    requirements_by_node: dict[str, list[Mapping[str, Any]]] = {}
    for requirement in requirement_rows:
        node_id = requirement.get("node_id")
        if node_id not in depends_on:
            check(
                False,
                f"Requirement {requirement.get('id')!r} belongs to unknown node {node_id!r}",
            )
            continue
        requirements_by_node.setdefault(node_id, []).append(requirement)
        # The verifier rejects this before any model call, so failing here gives a caller
        # a precise reason instead of a VerifierInputError three layers away.
        check(
            requirement.get("must_all_match", True),
            f"Requirement {requirement.get('id')!r} sets must_all_match=false, which needs "
            "an optional-field policy agreed with the verifier owner",
        )

    selected = list(candidate_node_ids) if candidate_node_ids is not None else all_node_ids
    unknown = [node_id for node_id in selected if node_id not in depends_on]
    check(not unknown, f"Candidate nodes are not in this loop: {unknown}")
    check(bool(selected), "Supply at least one candidate node")

    check(
        event.linked_loop_id in (None, loop_id),
        f"Event is linked to {event.linked_loop_id!r}, not to {loop_id!r}",
    )

    if issues:
        raise HydrationError(issues)

    loop = Loop.model_validate(
        {
            **_without(loop_row, DERIVED_LOOP_FIELDS),
            "node_ids": all_node_ids,
            # Provenance lives in loop_source_events, not on the event's routing hint.
            # A caller that has loaded it can overwrite this afterwards.
            "source_event_ids": list(loop_row.get("source_event_ids") or []),
        }
    )

    nodes = [
        OutcomeNode.model_validate(
            {
                **_without(row, DERIVED_NODE_FIELDS),
                "depends_on": depends_on[row["id"]],
                "evidence_requirement_ids": [
                    requirement["id"] for requirement in requirements_by_node.get(row["id"], [])
                ],
            }
        )
        for row in node_rows
        if row["id"] in set(selected)
    ]

    requirements = [
        EvidenceRequirement.model_validate(requirement)
        for node in nodes
        for requirement in requirements_by_node.get(node.id, [])
    ]

    return VerifyEventRequest(loop=loop, nodes=nodes, requirements=requirements, event=event)


class FixtureLoopGraphSource:
    """A `LoopGraphSource` backed by row dicts, for tests and offline demos.

    Takes rows rather than a path so `app` never imports from `tests`, matching
    `FixtureLoopRepository`.
    """

    def __init__(self, graphs: Sequence[Mapping[str, Any]]) -> None:
        self._graphs = {graph["loop"]["id"]: graph for graph in graphs}

    async def load_loop_graph(self, user_id: str, loop_id: str) -> dict[str, Any] | None:
        graph = self._graphs.get(loop_id)
        if graph is None or graph["loop"].get("user_id") != user_id:
            return None
        return dict(graph)
