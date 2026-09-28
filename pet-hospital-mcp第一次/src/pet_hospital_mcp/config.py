from dataclasses import dataclass
import os


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    base_url: str = "http://127.0.0.1:8080"
    timeout_seconds: float = 10.0
    retries: int = 2

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(
            host=os.getenv("MCP_HOST", defaults.host),
            port=int(os.getenv("MCP_PORT", str(defaults.port))),
            base_url=os.getenv("PET_HOSPITAL_BASE_URL", defaults.base_url).rstrip("/"),
        )