"""Assess one observed event; never mutate graph state or execute actions."""

from dataclasses import dataclass
from importlib.resources import files

from app.agents.llm import Prompt, ReasoningBoundary, ReasoningResult
from app.agents.mapping import MappingContext, map_verify
from app.agents.provider_schemas import VerifyDraft
from app.agents.verifier_schemas import EvidenceVerificationDraft
from app.graph.evidence_validation import validate_evidence_result, validate_verifier_request
from app.graph.schemas import VerifyEventRequest, VerifyEventResponse

VERIFIER_VERSION = "verifier-v1"


@dataclass(frozen=True)
class EvidenceVerifier:
    boundary: ReasoningBoundary

    async def verify(self, request: VerifyEventRequest) -> VerifyEventResponse:
        return (await self.verify_with_metadata(request)).value

    async def verify_with_metadata(
        self, request: VerifyEventRequest
    ) -> ReasoningResult[VerifyEventResponse]:
        snapshot = VerifyEventRequest.model_validate_json(request.model_dump_json())
        validate_verifier_request(snapshot)

        def convert(draft: EvidenceVerificationDraft, context: MappingContext):
            response = map_verify(
                VerifyDraft(
                    decisions=[d.model_dump(exclude={"citations"}) for d in draft.decisions],
                    requires_replan=draft.requires_replan,
                ),
                {node.id for node in snapshot.nodes},
            )
            validate_evidence_result(response, snapshot, draft, context.now, context.timezone)
            for decision, source in zip(response.decisions, draft.decisions, strict=True):
                decision.extracted_fields["_citations"] = [
                    citation.model_dump(mode="json") for citation in source.citations
                ]
            return response

        return await self.boundary.run(
            task="verify",
            prompt=Prompt(
                VERIFIER_VERSION,
                files("app.prompts").joinpath("verifier.md").read_text(encoding="utf-8"),
            ),
            request=snapshot,
            output_type=EvidenceVerificationDraft,
            convert=convert,
            # Conversion validates against the same trusted clock used in the prompt.
            validate=lambda response: response,
        )
