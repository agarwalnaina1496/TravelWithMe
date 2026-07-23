"""Immutable configuration boundary for selectable agent engines."""

from dataclasses import dataclass
from ...shared.properties import property_loader
from .contracts import GenerationConfig

N8N_WORKFLOW_TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class AgentEngineSettings:
    engine: str
    environment: str
    n8n_scout_webhook_url: str | None = None
    n8n_meridian_webhook_url: str | None = None
    n8n_timeout_seconds: int = 185
    langgraph_model_provider: str | None = None
    langgraph_api_key: str | None = None
    langgraph_model: str = "openai/gpt-oss-120b"
    generation_max_output_tokens: int = 16_384
    generation_temperature: float = 0.2
    generation_timeout_seconds: int = 180

    @property
    def generation_config(self) -> GenerationConfig:
        return GenerationConfig(
            max_output_tokens=self.generation_max_output_tokens,
            temperature=self.generation_temperature,
            timeout_seconds=self.generation_timeout_seconds,
        )

    @classmethod
    def load(cls) -> "AgentEngineSettings":
        engine = property_loader.get_string_property_with_default(
            "agent_engine", "n8n"
        ).strip().lower()
        environment = property_loader.get_environment()
        generation_max_output_tokens = _positive_int(
            "generation_max_output_tokens", 16_384
        )
        generation_temperature = _temperature(
            "generation_temperature", "0.2"
        )
        generation_timeout_seconds = _positive_int(
            "generation_timeout_seconds", 180
        )

        if engine == "n8n":
            n8n_timeout_seconds = _positive_int("n8n_timeout_seconds", 185)
            if generation_timeout_seconds != N8N_WORKFLOW_TIMEOUT_SECONDS:
                raise ValueError(
                    "GENERATION_TIMEOUT_SECONDS must be 180 for n8n to match "
                    "the versioned workflow execution timeout"
                )
            if n8n_timeout_seconds <= generation_timeout_seconds:
                raise ValueError(
                    "N8N_TIMEOUT_SECONDS must exceed "
                    "GENERATION_TIMEOUT_SECONDS"
                )
            return cls(
                engine=engine,
                environment=environment,
                n8n_scout_webhook_url=_required("n8n_scout_webhook_url"),
                n8n_meridian_webhook_url=_required("n8n_meridian_webhook_url"),
                n8n_timeout_seconds=n8n_timeout_seconds,
                generation_max_output_tokens=generation_max_output_tokens,
                generation_temperature=generation_temperature,
                generation_timeout_seconds=generation_timeout_seconds,
            )

        if engine == "langgraph":
            return cls(
                engine=engine,
                environment=environment,
                langgraph_model_provider=_required_with_default(
                    "langgraph_model_provider", "groq"
                ),
                langgraph_api_key=_required("langgraph_api_key"),
                langgraph_model=_required_with_default(
                    "langgraph_model", "openai/gpt-oss-120b"
                ),
                generation_max_output_tokens=generation_max_output_tokens,
                generation_temperature=generation_temperature,
                generation_timeout_seconds=generation_timeout_seconds,
            )

        raise ValueError(
            f"Unsupported AGENT_ENGINE: {engine or '<empty>'}. Expected n8n or langgraph."
        )


def _required(key: str) -> str:
    try:
        value = property_loader.get_string_property(key).strip()
    except Exception as exc:
        raise ValueError(f"{key.upper()} is required for the selected engine") from exc
    if not value:
        raise ValueError(f"{key.upper()} is required for the selected engine")
    return value


def _required_with_default(key: str, default: str) -> str:
    value = property_loader.get_string_property_with_default(key, default).strip()
    if not value:
        raise ValueError(f"{key.upper()} is required for the selected engine")
    return value


def _positive_int(key: str, default: int) -> int:
    try:
        value = property_loader.get_int_property_with_default(key, default)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key.upper()} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{key.upper()} must be a positive integer")
    return value


def _temperature(key: str, default: str) -> float:
    try:
        value = float(
            property_loader.get_string_property_with_default(key, default)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{key.upper()} must be a number between 0 and 2"
        ) from exc
    if not 0 <= value <= 2:
        raise ValueError(f"{key.upper()} must be a number between 0 and 2")
    return value
