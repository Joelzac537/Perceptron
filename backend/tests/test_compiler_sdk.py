import json

import httpx2
import pytest
from openai import AsyncOpenAI
from test_compiler import NOW, fixture_case
from test_openai_provider import response_body

from app.agents.compiler import OutcomeCompiler
from app.agents.llm import OpenAIProvider, ReasoningBoundary
from app.config import Settings


async def test_compiler_through_sdk_repairs_bad_reference_without_network():
    request, _, valid = fixture_case()
    invalid = valid.model_copy(deep=True)
    invalid.root_ref = "invented_root"
    outputs = [invalid, valid]
    requests = []

    def handler(http_request):
        requests.append(json.loads(http_request.content))
        return httpx2.Response(200, json=response_body(text=outputs.pop(0).model_dump_json()))

    client = AsyncOpenAI(
        api_key="test-placeholder",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    async with OpenAIProvider(Settings(), client=client) as provider:
        result = await OutcomeCompiler(
            ReasoningBoundary(provider, clock=lambda: NOW)
        ).compile_with_metadata(request)
    assert len(requests) == 2
    assert requests[0]["text"]["format"]["name"] == "CompileDraft"
    assert requests[0]["text"]["format"]["strict"] is True
    assert requests[0]["reasoning"] == {"effort": "medium"}
    assert "tools" not in requests[0]
    assert json.loads(requests[1]["input"][0]["content"])["validation_feedback"]
    assert [attempt.outcome for attempt in result.attempts] == ["invalid", "validated"]
    assert result.value.loop.root_node_id in result.value.loop.node_ids
    assert client.is_closed()


def test_compiler_cli_requires_explicit_live_flag(monkeypatch):
    from app.agents.compile_goal import main

    monkeypatch.setattr("sys.argv", ["compile_goal", "--request", "not_read_without_live.json"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
