from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from app.config import Settings


@dataclass(slots=True)
class LocalAI:
    settings: Settings

    def generate(self, prompt: str, *, quality: bool = False, json_mode: bool = False) -> str:
        model = self.settings.ollama_quality_model if quality else self.settings.ollama_fast_model
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.settings.ai_keep_alive,
            "options": {"temperature": 0.2, "num_ctx": 4096},
        }
        if json_mode:
            payload["format"] = "json"

        timeout = httpx.Timeout(
            connect=self.settings.ai_connect_timeout,
            read=self.settings.ai_generation_timeout,
            write=30.0,
            pool=30.0,
        )
        last_error: Exception | None = None
        for attempt in range(self.settings.ai_max_retries + 1):
            try:
                response = httpx.post(
                    f"{self.settings.ollama_base_url.rstrip('/')}/api/generate",
                    json=payload,
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json().get("response", "").strip()
            except Exception as exc:
                last_error = exc
                if attempt < self.settings.ai_max_retries:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"Local AI failed after retries: {last_error}")

    def health(self) -> dict[str, object]:
        try:
            response = httpx.get(
                f"{self.settings.ollama_base_url.rstrip('/')}/api/tags",
                timeout=self.settings.ai_connect_timeout,
            )
            response.raise_for_status()
            models = [item.get("name") for item in response.json().get("models", [])]
            return {"ok": True, "models": models}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def evaluate_job(self, compact_resume: str, job_text: str) -> dict[str, object]:
        prompt = f"""You are a strict job-fit evaluator. Never invent qualifications.
Return JSON only with keys: score, recommendation, strengths, gaps, risk_flags, positioning_strategy.
Score from 0 to 100. recommendation must be apply, review, or reject.

RESUME FACTS:
{compact_resume[:5000]}

JOB REQUIREMENTS:
{job_text[:7000]}
"""
        raw = self.generate(prompt, quality=False, json_mode=True)
        return json.loads(raw)

    def draft_materials(self, compact_resume: str, job_text: str) -> dict[str, object]:
        prompt = f"""Create truthful application materials using only the supplied facts.
Return JSON only with keys: summary, resume_bullets, cover_letter, screening_answers.
Do not invent dates, titles, certifications, employers, metrics, or skills.

RESUME FACTS:
{compact_resume[:6000]}

JOB:
{job_text[:7000]}
"""
        raw = self.generate(prompt, quality=True, json_mode=True)
        return json.loads(raw)
