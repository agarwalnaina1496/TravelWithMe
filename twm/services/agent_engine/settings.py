"""Immutable configuration boundary for selectable agent engines."""

from dataclasses import dataclass
from ...shared.properties import property_loader


@dataclass(frozen=True)
class AgentEngineSettings:
    engine: str
    environment: str
    n8n_scout_webhook_url: str | None = None
    n8n_meridian_webhook_url: str | None = None
    langgraph_model_provider: str | None = None
    langgraph_api_key: str | None = None
    langgraph_model: str = "openai/gpt-oss-120b"
    langgraph_temperature: float = 0.7
    langgraph_timeout_seconds: int = 60

    @classmethod
    def load(cls) -> "AgentEngineSettings":
        engine = property_loader.get_string_property_with_default(
            "agent_engine", "n8n"
        ).strip().lower()
        environment = property_loader.get_environment()

        if engine == "n8n":
            return cls(
                engine=engine,
                environment=environment,
                n8n_scout_webhook_url=_required("n8n_scout_webhook_url"),
                n8n_meridian_webhook_url=_required("n8n_meridian_webhook_url"),
            )

        if engine == "langgraph":
            timeout = _positive_int("langgraph_timeout_seconds", 60)
            temperature = _temperature()
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
                langgraph_temperature=temperature,
                langgraph_timeout_seconds=timeout,
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


def _temperature() -> float:
    try:
        value = float(
            property_loader.get_string_property_with_default(
                "langgraph_temperature", "0.7"
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "LANGGRAPH_TEMPERATURE must be a number between 0 and 2"
        ) from exc
    if not 0 <= value <= 2:
        raise ValueError("LANGGRAPH_TEMPERATURE must be a number between 0 and 2")
    return value
