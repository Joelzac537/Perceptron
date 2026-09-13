import json

import httpx2
from openai import AsyncOpenAI
from test_compiler import NOW
from test_openai_provider import response_body
from test_replanner import deadline_plan, repair_case

from app.agents.llm import OpenAIProvider, ReasoningBoundary
from app.agents.replanner import Replanner
from app.config import Settings


async def test_replanner_through_sdk_repairs_an_unknown_target_without_network():
    data = repair_case()
    good = deadline_plan(data)
    bad = good.model_copy(deep=True)
    bad.operations[0].target_id = "unknown_target"
    outputs, calls = [bad, good], []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx2.Response(200, json=response_body(text=outputs.pop(0).model_dump_json()))

    client = AsyncOpenAI(
        api_key="test-placeholder",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    async with OpenAIProvider(Settings(), client=client) as provider:
        result = await Replanner(
            ReasoningBoundary(provider, clock=lambda: NOW)
        ).replan_with_metadata(data.request, context=data.context)
    assert calls[0]["text"]["format"]["name"] == "ReplanDraft"
    assert calls[0]["text"]["format"]["strict"] is True
    assert calls[0]["reasoning"] == {"effort": "medium"}
    assert "tools" not in calls[0] and calls[0]["store"] is False
    assert len(calls) == 2
    assert json.loads(calls[1]["input"][0]["content"])["validation_feedback"]
    assert [a.outcome for a in result.attempts] == ["invalid", "validated"]
    assert result.value.operations[0].target_id == data.request.nodes[0].id
    assert client.is_closed()
