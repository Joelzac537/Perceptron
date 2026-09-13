"""Verifier-specific provenance, without changing the shared response DTO."""

from typing import Literal

from app.agents.provider_schemas import DecisionDraft, ProviderModel
from app.graph.schemas import NonEmpty


class EvidenceCitation(ProviderModel):
    field: NonEmpty
    source: Literal["event.actor", "event.subject", "event.content", "attachment"]
    attachment_id: NonEmpty | None
    quote: NonEmpty


class EvidenceDecisionDraft(DecisionDraft):
    citations: list[EvidenceCitation]


class EvidenceVerificationDraft(ProviderModel):
    decisions: list[EvidenceDecisionDraft]
    requires_replan: bool
