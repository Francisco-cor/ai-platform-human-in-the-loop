"""Secret manager adapter — F3-3 env vs GCP.

In ci/local, reads from env or .env.
In staging/production with GCP, fetches from Secret Manager if google-cloud-secret-manager installed and credentials available.
"""

from __future__ import annotations

import os
from functools import lru_cache


class SecretProvider:
    """Abstraction for secrets."""

    def __init__(self, use_gcp: bool = False, project_id: str | None = None) -> None:
        self.use_gcp = use_gcp
        self.project_id = (
            project_id or os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
        )
        self._cache: dict[str, str | None] = {}
        self._client = None
        if use_gcp:
            try:
                from google.cloud import secretmanager  # type: ignore

                self._client = secretmanager.SecretManagerServiceClient()
            except Exception:
                self._client = None
                self.use_gcp = False

    def get(self, name: str, default: str | None = None) -> str | None:
        """Get secret by name. Name can be env-var style (GEMINI_API_KEY) or secret id (gemini_api_key)."""
        # check cache
        if name in self._cache:
            return self._cache[name] if self._cache[name] is not None else default
        # try env first
        env_val = os.getenv(name) or os.getenv(name.upper()) or os.getenv(name.lower())
        if env_val:
            self._cache[name] = env_val
            return env_val
        # try gcp if enabled
        if self.use_gcp and self._client and self.project_id:
            try:
                # secret id is lowercased with underscores
                secret_id = name.lower().replace("_", "-")
                resource = f"projects/{self.project_id}/secrets/{secret_id}/versions/latest"
                response = self._client.access_secret_version(request={"name": resource})
                val = response.payload.data.decode()
                self._cache[name] = val
                return val
            except Exception:
                pass
        self._cache[name] = None
        return default

    def clear(self) -> None:
        self._cache.clear()


@lru_cache(maxsize=1)
def get_secret_provider() -> SecretProvider:
    # auto-detect: if GCP_PROJECT_ID set and not ci, use gcp
    from procurement_platform.config.settings import get_settings

    settings = get_settings()
    use_gcp = settings.app_env in ("staging", "production") and bool(os.getenv("GCP_PROJECT_ID"))
    return SecretProvider(use_gcp=use_gcp)


def reset_secret_provider() -> None:
    get_secret_provider.cache_clear()
