"""Model-agnostic AI gateway (master plan Sec 5: "Create an AI gateway abstraction so the
model can be replaced later. Do not hard-code business logic around one model."). Every
caller depends on the LLMClient protocol, not a specific model or serving engine - tests
inject a fake client instead of needing a real local LLM server, same pattern as the Oracle
connector and the email sender.

The LLM is never the source of truth (Sec 5) - it only ever sees evidence assembled by the
tool layer (app/ai/tools.py) from this platform's own deterministic data. This module has
no knowledge of Oracle credentials or SQL and never will.
"""

from typing import Protocol, TypedDict

from app.logging_config import get_logger

logger = get_logger(__name__)


class ChatMessage(TypedDict):
    role: str
    content: str


class AIGatewayError(Exception):
    CATEGORY_UNAVAILABLE = "unavailable"
    CATEGORY_UNKNOWN = "unknown_error"

    def __init__(self, category: str, message: str):
        self.category = category
        self.message = message
        super().__init__(message)


class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        json_mode: bool = False,
    ) -> tuple[str, str]:
        """Returns (content, model_name_that_served_it)."""
        ...


class OpenAICompatibleClient:
    """Tries the primary model first, falls back to the secondary model on failure - works
    against any OpenAI-compatible local server (vLLM/Ollama/llama.cpp)."""

    def __init__(self, *, base_url: str, api_key: str, primary_model: str, fallback_model: str, timeout_seconds: int):
        from openai import NOT_GIVEN, AsyncOpenAI

        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout_seconds)
        self._not_given = NOT_GIVEN
        self.primary_model = primary_model
        self.fallback_model = fallback_model

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        json_mode: bool = False,
    ) -> tuple[str, str]:
        candidates = [self.primary_model]
        if self.fallback_model and self.fallback_model != self.primary_model:
            candidates.append(self.fallback_model)

        last_error: Exception | None = None
        for index, model in enumerate(candidates):
            try:
                response = await self._client.chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature,
                    max_tokens=max_tokens,
                    # Prompt-only "respond with exactly this JSON shape" instructions are not
                    # reliably followed by every real model - verified against a real local
                    # 7B model (Enterprise Readiness Plan A.6), which added a conversational
                    # preamble and invented its own JSON structure despite an explicit "no
                    # other text" instruction. response_format=json_object is a standard
                    # OpenAI-compatible server feature (Ollama/vLLM/llama.cpp all support it)
                    # that constrains the raw output to valid JSON - it doesn't guarantee the
                    # exact schema, so parse_rca_response's validation is still load-bearing.
                    response_format={"type": "json_object"} if json_mode else self._not_given,
                )
                content = response.choices[0].message.content
                if content:
                    if index > 0:
                        logger.warning("ai_gateway_fell_back_to_secondary_model", model=model)
                    return content, model
            except Exception as exc:
                last_error = exc
                logger.warning("ai_gateway_model_call_failed", model=model, error_type=type(exc).__name__)

        raise AIGatewayError(
            AIGatewayError.CATEGORY_UNAVAILABLE, f"No configured model responded: {last_error}"
        ) from last_error


def build_llm_client(
    *, base_url: str, api_key: str, primary_model: str, fallback_model: str, timeout_seconds: int
) -> LLMClient:
    return OpenAICompatibleClient(
        base_url=base_url,
        api_key=api_key,
        primary_model=primary_model,
        fallback_model=fallback_model,
        timeout_seconds=timeout_seconds,
    )


def get_llm_client() -> LLMClient | None:
    """FastAPI dependency provider. None when AI_ENABLED=false (the default - no local model
    is assumed to exist). Overridden with a fake in tests, same pattern as
    get_oracle_connector."""
    from app.config import get_settings

    settings = get_settings()
    if not settings.ai_enabled:
        return None
    return build_llm_client(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        primary_model=settings.ai_primary_model,
        fallback_model=settings.ai_fallback_model,
        timeout_seconds=settings.ai_request_timeout_seconds,
    )


def get_copilot_llm_client() -> LLMClient | None:
    """FastAPI dependency provider for the Copilot chat route only - RcaService keeps using
    get_llm_client (settings.ai_primary_model) unchanged. Falls back to ai_primary_model, not
    ai_fallback_model, if the dedicated chat model is unavailable."""
    from app.config import get_settings

    settings = get_settings()
    if not settings.ai_enabled:
        return None
    return build_llm_client(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        primary_model=settings.ai_copilot_model,
        fallback_model=settings.ai_primary_model,
        timeout_seconds=settings.ai_request_timeout_seconds,
    )
