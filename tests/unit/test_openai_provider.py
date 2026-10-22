"""OpenAI provider against a mocked HTTP layer: request shape, parsing, usage, error mapping."""

from __future__ import annotations

import json

import httpx2
import openai
import pytest
from app.agents.contracts import ReviewOutput, SpecialistOutput, SynthesisOutput
from app.providers.llm.openai_provider import OpenAIProvider
from marketlens_core.config import Settings
from marketlens_core.errors import ErrorCode, MarketLensError

pytestmark = pytest.mark.anyio
KEY = "sk-test-0123456789"
VALID = SpecialistOutput(
    specialist="market_data", status="completed", summary="ok", findings=[], missing_evidence=[], warnings=[]
)


def response_body(content: list[dict]) -> dict:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1760000000,
        "model": "test-model-x",
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "status": "completed",
        "output": [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "content": content}],
        "usage": {
            "input_tokens": 120,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 45,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 165,
        },
    }


def text_content(text: str) -> list[dict]:
    return [{"type": "output_text", "text": text, "annotations": []}]


def provider(handler) -> OpenAIProvider:
    settings = Settings(
        _env_file=None, app_mode="live", llm_mode="openai", openai_api_key=KEY, openai_model="test-model-x"
    )
    client = openai.AsyncOpenAI(
        api_key=KEY, max_retries=0, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    return OpenAIProvider(settings, client=client)


async def call(p: OpenAIProvider):
    return await p.generate_structured(
        task="market_specialist",
        system_prompt="SYSTEM RULES",
        payload={"evidence": [{"evidence_id": "ev_x"}]},
        output_model=SpecialistOutput,
    )


async def test_request_uses_strict_schema_no_storage_and_parses_usage() -> None:
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=response_body(text_content(VALID.model_dump_json())))

    result = await call(provider(handler))
    assert result.output == VALID and result.model == "test-model-x"
    assert (result.input_tokens, result.output_tokens) == (120, 45)
    body = seen["body"]
    assert body["model"] == "test-model-x" and body["store"] is False and body["instructions"] == "SYSTEM RULES"
    assert body["text"]["format"]["type"] == "json_schema" and body["text"]["format"]["strict"] is True
    assert "untrusted_source_text" in body["input"] and '"ev_x"' in body["input"]


@pytest.mark.parametrize(
    "status, retryable",
    [(401, False), (400, False), (429, True), (500, True), (503, True)],
)
async def test_http_errors_map_to_llm_unavailable(status: int, retryable: bool) -> None:
    with pytest.raises(MarketLensError) as err:
        await call(provider(lambda r: httpx2.Response(status, json={"error": {"message": "x", "type": "x"}})))
    assert err.value.code == ErrorCode.LLM_UNAVAILABLE and err.value.retryable is retryable
    assert KEY not in str(err.value) and KEY not in json.dumps(err.value.details)


async def test_timeout_refusal_and_schema_mismatch() -> None:
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(MarketLensError) as err:
        await call(provider(timeout))
    assert err.value.retryable is True
    refusal = [{"type": "refusal", "refusal": "I can't help with that."}]
    with pytest.raises(MarketLensError) as err:
        await call(provider(lambda r: httpx2.Response(200, json=response_body(refusal))))
    assert "no structured output" in err.value.message
    with pytest.raises(MarketLensError) as err:
        await call(provider(lambda r: httpx2.Response(200, json=response_body(text_content('{"not": "valid"}')))))
    assert err.value.code == ErrorCode.LLM_UNAVAILABLE and "schema" in err.value.message


def test_provider_requires_key_and_model() -> None:
    with pytest.raises(MarketLensError):
        OpenAIProvider(Settings(_env_file=None, openai_api_key=KEY))


def test_local_endpoint_needs_a_model_but_no_key() -> None:
    local = Settings(_env_file=None, openai_base_url="http://127.0.0.1:11434/v1", openai_model="qwen2.5:7b")
    assert local.openai_configured and OpenAIProvider(local).model == "qwen2.5:7b"
    assert str(OpenAIProvider(local).client.base_url).startswith("http://127.0.0.1:11434/v1")
    # A remote endpoint still needs a key, and a local one still needs a model.
    assert not Settings(_env_file=None, openai_base_url="https://example.com/v1", openai_model="m").openai_configured
    assert not Settings(_env_file=None, openai_base_url="http://localhost:11434/v1").openai_configured


def _objects(schema: object) -> list[dict]:
    if isinstance(schema, dict):
        found = [schema] if schema.get("type") == "object" else []
        return found + [o for v in schema.values() for o in _objects(v)]
    if isinstance(schema, list):
        return [o for v in schema for o in _objects(v)]
    return []


@pytest.mark.parametrize("model", [SpecialistOutput, SynthesisOutput, ReviewOutput])
def test_agent_contracts_are_valid_strict_structured_output_schemas(model: type) -> None:
    """Strict mode needs closed objects with every property required and no defaults."""
    schema = model.model_json_schema()
    objects = _objects(schema)
    assert objects
    for obj in objects:
        assert obj.get("additionalProperties") is False, obj.get("title")
        assert set(obj.get("required", [])) == set(obj.get("properties", {})), obj.get("title")
    assert '"default"' not in json.dumps(schema)


async def test_a_missing_model_is_a_plain_sentence_with_the_detail_kept_for_the_operator() -> None:
    """The page gets one plain sentence; what the provider said goes to the details and logs (D-107)."""
    body = {
        "error": {
            "message": "The model `qwen2.5:7b` does not exist or you do not have access to it.",
            "type": "invalid_request_error",
            "code": "model_not_found",
        }
    }
    with pytest.raises(MarketLensError) as err:
        await call(provider(lambda r: httpx2.Response(404, json=body)))
    assert err.value.message == "The AI model set up for this site isn't available."
    assert err.value.retryable is False
    assert err.value.details["provider_status"] == 404
    assert "does not exist" in err.value.details["provider_message"]


async def test_no_credit_is_not_reported_as_a_rate_limit() -> None:
    body = {
        "error": {
            "message": "You have no credits remaining.",
            "type": "insufficient_quota",
            "code": "insufficient_quota",
        }
    }
    with pytest.raises(MarketLensError) as err:
        await call(provider(lambda r: httpx2.Response(429, json=body)))
    assert err.value.message == "The AI service account has run out of credit." and err.value.retryable is False


async def test_a_rejected_key_never_shows_the_key_masked_or_not() -> None:
    """OpenAI echoes a masked key in 401s ("sk-proj-****genK"); neither it nor the real key reaches anyone."""
    masked = "sk-proj-" + "*" * 120 + "genK"
    body = {
        "error": {
            "message": f"Incorrect API key provided: {masked}. You can find your API key at x.",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        }
    }
    with pytest.raises(MarketLensError) as err:
        await call(provider(lambda r: httpx2.Response(401, json=body)))
    assert err.value.message == "The AI service didn't accept the site's API key."
    detail = err.value.details["provider_message"]
    assert "sk-proj" not in detail and "****" not in detail and KEY not in detail and "[key]" in detail
