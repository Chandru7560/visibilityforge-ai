from dataclasses import dataclass
from typing import Protocol


class IntegrationUnavailable(RuntimeError):
    """Raised when collection was not actually performed by an authorized API."""


class SearchMeasurementAdapter(Protocol):
    engine: str

    @property
    def configured(self) -> bool: ...

    def collect(self, query: str, market: str, location: str) -> str: ...


@dataclass(frozen=True)
class ManualEvidenceAdapter:
    engine: str

    @property
    def configured(self) -> bool:
        return False

    def collect(self, query: str, market: str, location: str) -> str:
        raise IntegrationUnavailable(
            f"No authorized API integration is configured for {self.engine}. "
            "Use manual evidence entry after observing the result yourself."
        )


SUPPORTED_PROFILES = (
    "ChatGPT",
    "Gemini",
    "Perplexity",
    "Claude",
    "Copilot",
    "Google AI Overviews",
    "Google Search",
)


def get_adapter(engine: str) -> SearchMeasurementAdapter:
    if engine not in SUPPORTED_PROFILES:
        raise ValueError(f"Unsupported search profile: {engine}")
    return ManualEvidenceAdapter(engine=engine)
