import os


class Settings:
    """Application settings loaded from environment variables."""

    # API metadata
    API_TITLE: str = "Stateful Compliance Auditor"
    API_VERSION: str = "1.0.0"
    API_DESCRIPTION: str = "On-premise financial risk and compliance auditing system"

    # Ollama inference engine
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://ollama:11434")
    OLLAMA_TIMEOUT: int = int(os.getenv("OLLAMA_TIMEOUT", "120"))

    # Runtime flags
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    # Request guardrails
    MAX_REQUEST_SIZE: int = 10_000_000  # 10 MB

    @classmethod
    def get_settings(cls) -> "Settings":
        return cls()


settings = Settings.get_settings()
