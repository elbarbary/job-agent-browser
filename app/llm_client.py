"""Small localhost-only Ollama client used for status and safe text assistance."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .config import Settings


class LocalLLMError(RuntimeError):
    """Raised when the configured local model is unavailable."""


PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

PROVIDER_MODEL_ENV = {
    "openai": "OPENAI_MODEL",
    "anthropic": "ANTHROPIC_MODEL",
    "gemini": "GEMINI_MODEL",
    "deepseek": "DEEPSEEK_MODEL",
}


def _read_provider_settings(settings: Settings) -> dict[str, Any]:
    path = settings.profile_dir / "llm_providers.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LocalLLMError(f"Invalid LLM provider settings: {path}") from exc


class LocalLLMClient:
    def __init__(self, settings: Settings, timeout: float = 120.0) -> None:
        settings.validate()
        self.settings = settings
        self.timeout = timeout
        provider_settings = _read_provider_settings(settings)
        self.provider = os.environ.get("JOB_AGENT_LLM_PROVIDER") or provider_settings.get("active_provider") or "ollama"
        self.provider = str(self.provider).strip().lower()
        self.models = provider_settings.get("models") or {}
        if self.provider == "ollama":
            self.client = httpx.Client(base_url=settings.ollama_base_url, timeout=timeout)
        else:
            self.client = httpx.Client(timeout=timeout)

    def _external_model(self) -> str:
        env_name = PROVIDER_MODEL_ENV.get(self.provider)
        model = os.environ.get(env_name or "") or self.models.get(self.provider)
        if not model:
            raise LocalLLMError(f"Missing model for provider {self.provider}; set it in the dashboard or {env_name}.")
        return str(model)

    def _external_key(self) -> str:
        env_name = PROVIDER_KEY_ENV.get(self.provider)
        key = os.environ.get(env_name or "")
        if not key:
            raise LocalLLMError(f"Missing API key for provider {self.provider}; set {env_name} in your private environment.")
        return key

    def status(self) -> dict[str, Any]:
        if self.provider != "ollama":
            env_name = PROVIDER_KEY_ENV.get(self.provider, "")
            return {
                "provider": self.provider,
                "model": self.models.get(self.provider) or os.environ.get(PROVIDER_MODEL_ENV.get(self.provider, "")),
                "api_key_env": env_name,
                "api_key_present": bool(os.environ.get(env_name)),
                "privacy": "External provider mode may send CV/job text to that provider.",
            }
        try:
            version = self.client.get("/api/version")
            version.raise_for_status()
            tags = self.client.get("/api/tags")
            tags.raise_for_status()
        except httpx.HTTPError as exc:
            raise LocalLLMError(f"Ollama is unavailable at the local endpoint: {exc}") from exc
        models = [model.get("name") for model in tags.json().get("models", [])]
        return {"provider": "ollama", "endpoint": self.settings.ollama_base_url, "version": version.json(), "models": models}

    def chat(self, prompt: str) -> str:
        if self.provider == "openai":
            return self._openai_compatible_chat(
                base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                api_key=self._external_key(),
                model=self._external_model(),
                prompt=prompt,
            )
        if self.provider == "deepseek":
            return self._openai_compatible_chat(
                base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                api_key=self._external_key(),
                model=self._external_model(),
                prompt=prompt,
            )
        if self.provider == "anthropic":
            return self._anthropic_chat(prompt)
        if self.provider == "gemini":
            return self._gemini_chat(prompt)
        if self.provider != "ollama":
            raise LocalLLMError(f"Unsupported LLM provider: {self.provider}")
        payload = {
            "model": self.settings.ollama_model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            response = self.client.post("/api/chat", json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LocalLLMError(f"Local generation failed: {exc}") from exc
        return str(response.json().get("message", {}).get("content", "")).strip()

    def _openai_compatible_chat(self, *, base_url: str, api_key: str, model: str, prompt: str) -> str:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        try:
            response = self.client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LocalLLMError(f"{self.provider} generation failed: {exc}") from exc
        return str(response.json().get("choices", [{}])[0].get("message", {}).get("content", "")).strip()

    def _anthropic_chat(self, prompt: str) -> str:
        payload = {
            "model": self._external_model(),
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            response = self.client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._external_key(),
                    "anthropic-version": os.environ.get("ANTHROPIC_VERSION", "2023-06-01"),
                },
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LocalLLMError(f"Anthropic generation failed: {exc}") from exc
        parts = response.json().get("content") or []
        return "\n".join(str(part.get("text", "")) for part in parts if isinstance(part, dict)).strip()

    def _gemini_chat(self, prompt: str) -> str:
        model = self._external_model()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        try:
            response = self.client.post(url, params={"key": self._external_key()}, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LocalLLMError(f"Gemini generation failed: {exc}") from exc
        candidates = response.json().get("candidates") or []
        parts = ((candidates[0] or {}).get("content") or {}).get("parts") if candidates else []
        return "\n".join(str(part.get("text", "")) for part in (parts or []) if isinstance(part, dict)).strip()

    def smoke_test(self) -> str:
        return self.chat("Reply with exactly: LOCAL_MODEL_OK")

    def grounded_job_advisory(self, profile: dict[str, Any], job: dict[str, Any]) -> str:
        prompt = (
            "You are an advisory reviewer for a job application draft. The CV profile is the "
            "only source of candidate facts. Never infer or invent education, employment, "
            "certification, work authorization, salary, or location. If a fact is absent, say "
            "needs_user_answer. Provide concise match strengths, missing requirements, and "
            "questions only; do not instruct submission.\n\n"
            f"CV PROFILE:\n{json.dumps(profile, ensure_ascii=True)}\n\n"
            f"JOB:\n{json.dumps(job, ensure_ascii=True)}"
        )
        return self.chat(prompt)

    def browser_use_adapter(self) -> Any:
        """Provide the official native adapter for future audited agent operations."""
        if self.provider == "ollama":
            from browser_use import ChatOllama

            return ChatOllama(model=self.settings.ollama_model, host=self.settings.ollama_base_url)
        if self.provider == "openai":
            from browser_use import ChatOpenAI

            return ChatOpenAI(
                model=self._external_model(),
                api_key=self._external_key(),
                base_url=os.environ.get("OPENAI_BASE_URL"),
            )
        if self.provider == "deepseek":
            from browser_use import ChatOpenAI

            return ChatOpenAI(
                model=self._external_model(),
                api_key=self._external_key(),
                base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            )
        if self.provider == "anthropic":
            from browser_use import ChatAnthropic

            return ChatAnthropic(model=self._external_model(), api_key=self._external_key())
        if self.provider == "gemini":
            from browser_use import ChatGoogle

            return ChatGoogle(model=self._external_model(), api_key=self._external_key())
        raise LocalLLMError(f"Unsupported LLM provider: {self.provider}")
