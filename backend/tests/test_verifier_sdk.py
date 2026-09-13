import json

import httpx2
from openai import AsyncOpenAI
from openai.lib._parsing._responses import type_to_text_format_param
from test_compiler import NOW
from test_openai_provider import response_body
from test_verifier import decision_draft, request_for

from app.agents.llm import OpenAIProvider, ReasoningBoundary
from app.agents.provider_schemas import assert_closed_schema
from app.agents.verifier import EvidenceVerifier
from app.agents.verifier_schemas import EvidenceVerificationDraft
from app.config import Settings


def test_verifier_schema_is_closed_and_accepted_by_sdk():
    assert_closed_schema(EvidenceVerificationDraft)
    assert type_to_text_format_param(EvidenceVerificationDraft)["strict"] is True


async def test_verifier_through_sdk_repairs_invalid_relationship_without_network():
    request = request_for()
    before = request.model_dump_json()
    valid = decision_draft(request).model_dump(mode="json")
    invalid = json.loads(json.dumps(valid))
    invalid["decisions"][0]["relationship"] = "COMPLETED"
    outputs, requests = [invalid, valid], []

    def handler(http_request):
        requests.append(json.loads(http_request.content))
        return httpx2.Response(200, json=response_body(text=json.dumps(outputs.pop(0))))

    client = AsyncOpenAI(
        api_key="test-placeholder",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    async with OpenAIProvider(Settings(), client=client) as provider:
        result = await EvidenceVerifier(
            ReasoningBoundary(provider, clock=lambda: NOW)
        ).verify_with_metadata(request)
    assert len(requests) == 2
    assert requests[0]["text"]["format"]["name"] == "EvidenceVerificationDraft"
    assert requests[0]["text"]["format"]["strict"] is True
    assert requests[0]["reasoning"] == {"effort": "low"}
    assert "tools" not in requests[0] and requests[0]["store"] is False
    assert json.loads(requests[1]["input"][0]["content"])["validation_feedback"]
    assert [s.outcome for s in result.attempts] == ["invalid", "validated"]
    assert result.value.decisions[0].evidence_satisfies_requirement
    assert request.model_dump_json() == before
    assert client.is_closed()
