import os

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

OLLAMA_MODEL = "qwen3:8b"
CLAUDE_MODEL = "claude-sonnet-4-6"


def local_model() -> OpenAIChatModel:
    return OpenAIChatModel(
        OLLAMA_MODEL,
        provider=OpenAIProvider(base_url="http://localhost:11434/v1"),
    )


def claude_model():
    from pydantic_ai.models.anthropic import AnthropicModel
    return AnthropicModel(CLAUDE_MODEL)


def get_model():
    mode = os.getenv("MODEL_MODE", "local").lower()
    if mode == "claude":
        return claude_model()
    return local_model()
