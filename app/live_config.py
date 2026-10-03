"""DeepSeek configuration without client initialization or credential logging."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
LIVE_UNAVAILABLE = "DeepSeek live 不可用，请检查本地配置或切回 AGENT_MODE=langgraph。"
MISSING_KEY = "DeepSeek live 未配置 DEEPSEEK_API_KEY；请使用 AGENT_MODE=langgraph，或由用户自行配置后启用。"


class LiveModeConfigurationError(RuntimeError):
    """Only fixed, sanitized messages may be exposed by the API."""


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL

    @classmethod
    def from_env(cls):
        return cls(api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
                   base_url=os.getenv("DEEPSEEK_BASE_URL", "").strip() or DEFAULT_BASE_URL,
                   model=os.getenv("DEEPSEEK_MODEL", "").strip() or DEFAULT_MODEL)

    def validate(self):
        if not self.api_key:
            raise LiveModeConfigurationError(MISSING_KEY)
        url = urlsplit(self.base_url)
        if url.scheme != "https" or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise LiveModeConfigurationError(LIVE_UNAVAILABLE)

    def client_parameters(self):
        # Used only at the client boundary, never in health/audit/logs/results.
        return {"api_key": self.api_key, "base_url": self.base_url, "model": self.model,
                "temperature": 0, "max_retries": 0, "timeout": 30,
                # ChatOpenAI does not round-trip DeepSeek reasoning_content.
                # Use the documented non-thinking tool protocol explicitly.
                "extra_body": {"thinking": {"type": "disabled"}}}


def live_ready():
    try:
        DeepSeekConfig.from_env().validate()
        return os.getenv("KNOWLEDGE_RETRIEVER", "candidate") == "candidate"
    except (ValueError, LiveModeConfigurationError):
        return False
