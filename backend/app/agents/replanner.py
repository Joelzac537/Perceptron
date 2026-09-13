"""Validated repair proposals with an explicit, caller-owned full-state context."""

from dataclasses import dataclass
from importlib.resources import files

from app.agents.llm import Prompt, ReasoningBoundary, ReasoningResult
from app.agents.provider_schemas import ReplanDraft
from app.graph.repair_schemas import RepairInput, ReplanContext
from app.graph.repair_validation import map_repair, validate_repair_input
from app.graph.schemas import ReplanRequest, ReplanResponse
from app.prompts import BOUNDARY_VERSION

REPLANNER_VERSION = "replanner-v1"


@dataclass(frozen=True)
class Replanner:
    boundary: ReasoningBoundary

    async def replan(self, request: ReplanRequest, *, context: ReplanContext) -> ReplanResponse:
        return (await self.replan_with_metadata(request, context=context)).value

    async def replan_with_metadata(
        self, request: ReplanRequest, *, context: ReplanContext
    ) -> ReasoningResult[ReplanResponse]:
        snapshot = RepairInput.model_validate_json(
            RepairInput(request=request, context=context).model_dump_json()
        )
        validate_repair_input(snapshot)
        if snapshot.request.triggering_event.id in snapshot.context.applied_event_ids:
            return ReasoningResult(
                ReplanResponse(
                    operations=[], proposed_actions=[], summary="Event already applied."
                ),
                "replan",
                REPLANNER_VERSION,
                BOUNDARY_VERSION,
                (),
            )
        return await self.boundary.run(
            task="replan",
            prompt=Prompt(
                REPLANNER_VERSION,
                files("app.prompts").joinpath("replanner.md").read_text(encoding="utf-8"),
            ),
            request=snapshot,
            output_type=ReplanDraft,
            convert=lambda draft, mapping: map_repair(draft, snapshot, mapping),
            validate=lambda response: response,
        )
