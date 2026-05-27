"""Small localhost-only Ollama client used for status and safe text assistance."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .config import Settings


class LocalLLMError(RuntimeError):
    """Raised when the configured local model is unavailable."""


class LocalLLMClient:
    def __init__(self, settings: Settings, timeout: float = 120.0) -> None:
        settings.validate()
        self.settings = settings
        self.client = httpx.Client(base_url=settings.ollama_base_url, timeout=timeout)

    def status(self) -> dict[str, Any]:
        try:
            version = self.client.get("/api/version")
            version.raise_for_status()
            tags = self.client.get("/api/tags")
            tags.raise_for_status()
        except httpx.HTTPError as exc:
            raise LocalLLMError(f"Ollama is unavailable at the local endpoint: {exc}") from exc
        models = [model.get("name") for model in tags.json().get("models", [])]
        return {"endpoint": self.settings.ollama_base_url, "version": version.json(), "models": models}

    def chat(self, prompt: str) -> str:
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
        from browser_use import ChatOllama

        return ChatOllama(model=self.settings.ollama_model, host=self.settings.ollama_base_url)
