import json

import httpx2
import pytest
from openai import AsyncOpenAI

from app.agents.llm import InvalidOutput, LLMError, OpenAIProvider, ProviderCall
from app.agents.provider_schemas import VerifyDraft
from app.config import Settings


def response_body(*, text='{"decisions": [], "requires_replan": false}', status="completed"):
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1789308000,
        "status": status,
        "model": "gpt-5.4-mini-test-snapshot",
        "output": [
            {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 2},
        },
    }


def make_provider(handler):
    client = AsyncOpenAI(
        api_key="unit-test-placeholder",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return OpenAIProvider(Settings(), client=client)


def call(task="verify"):
    return ProviderCall(task, "gpt-5.4-mini", "Trusted instructions", "{}", 1, 1000)


@pytest.mark.parametrize(
    "task,effort", [("compile", "medium"), ("verify", "low"), ("replan", "medium")]
)
async def test_actual_sdk_request_and_parse_without_network(task, effort):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=response_body())

    async with make_provider(handler) as provider:
        result = await provider.generate(call(task), VerifyDraft)
    body = requests[0]
    assert body["model"] == "gpt-5.4-mini"
    assert body["reasoning"] == {"effort": effort}
    assert body["text"]["format"]["strict"] is True
    assert body["store"] is False
    assert "tools" not in body and "temperature" not in body
    assert body["input"][0]["role"] == "user"
    assert result.output.decisions == []
    assert result.stats.model == "gpt-5.4-mini-test-snapshot"
    assert result.stats.input_tokens == 10
    assert result.stats.output_tokens == 5
    assert provider.client.is_closed()


@pytest.mark.parametrize("text", ["{broken", '{"decisions": "bad", "requires_replan": false}'])
async def test_invalid_output_from_actual_sdk_is_retryable(text):
    async with make_provider(
        lambda _: httpx2.Response(200, json=response_body(text=text))
    ) as provider:
        with pytest.raises(InvalidOutput) as error:
            await provider.generate(call(), VerifyDraft)
    assert error.value.stats.input_tokens == 10
    assert "{broken" not in str(error.value)


async def test_truncated_incomplete_json_is_not_invalid_output():
    body = response_body(text='{"decisions": [', status="incomplete")
    body["incomplete_details"] = {"reason": "max_output_tokens"}
    async with make_provider(lambda _: httpx2.Response(200, json=body)) as provider:
        with pytest.raises(LLMError) as error:
            await provider.generate(call(), VerifyDraft)
    assert error.value.code == "LLM_INCOMPLETE"


async def test_refusal_is_distinct_and_not_exposed():
    body = response_body()
    body["output"][0]["content"] = [{"type": "refusal", "refusal": "Sensitive refusal text"}]
    async with make_provider(lambda _: httpx2.Response(200, json=body)) as provider:
        with pytest.raises(LLMError) as error:
            await provider.generate(call(), VerifyDraft)
    assert error.value.code == "LLM_REFUSED"
    assert "Sensitive" not in str(error.value)


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "LLM_REQUEST_REJECTED"),
        (401, "LLM_ACCESS_DENIED"),
        (403, "LLM_ACCESS_DENIED"),
        (429, "LLM_RATE_LIMITED"),
        (500, "LLM_UNAVAILABLE"),
    ],
)
async def test_http_errors_do_not_retry_or_leak_provider_body(status, code):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(status, json={"error": {"message": "sensitive provider text"}})

    async with make_provider(handler) as provider:
        with pytest.raises(LLMError) as error:
            await provider.generate(call(), VerifyDraft)
    assert error.value.code == code
    assert len(requests) == 1
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize(
    "exception,code",
    [
        (httpx2.ReadTimeout, "LLM_TIMEOUT"),
        (httpx2.ConnectError, "LLM_UNAVAILABLE"),
    ],
)
async def test_transport_errors(exception, code):
    def handler(request):
        raise exception("sensitive transport details", request=request)

    async with make_provider(handler) as provider:
        with pytest.raises(LLMError) as error:
            await provider.generate(call(), VerifyDraft)
    assert error.value.code == code


def test_missing_key_fails_before_client_creation():
    with pytest.raises(LLMError) as error:
        OpenAIProvider(Settings())
    assert error.value.code == "LLM_CREDENTIALS_MISSING"


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"status": "failed", "output": None},
        {"status": "completed", "output": None},
        {"status": "completed", "output": ["invalid"]},
        {"status": "completed", "output": [{"type": "message", "content": None}]},
    ],
)
async def test_failed_or_malformed_response_has_distinct_error(body):
    async with make_provider(lambda _: httpx2.Response(200, json=body)) as provider:
        with pytest.raises(LLMError) as error:
            await provider.generate(call(), VerifyDraft)
    assert error.value.code == "LLM_RESPONSE_FAILED"


async def test_absent_structured_output_is_invalid():
    body = response_body()
    body["output"] = []
    async with make_provider(lambda _: httpx2.Response(200, json=body)) as provider:
        with pytest.raises(InvalidOutput):
            await provider.generate(call(), VerifyDraft)
