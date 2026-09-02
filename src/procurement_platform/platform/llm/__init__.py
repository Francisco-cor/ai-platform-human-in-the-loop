"""
Platform LLM — generic provider abstraction (Fase 11).

Adapter, factory (Gemini → DeepSeek → Fake), prompt registry (hash), cache,
per-tenant budgets, llm_matrix.

No domain import.
"""

from __future__ import annotations

from procurement_platform.agents.factory import LLMFactory
from procurement_platform.agents.adapter import LLMRequest, LLMResponse
from procurement_platform.agents.prompts import get_prompt, get_prompt_hash

__all__ = ["LLMFactory", "LLMRequest", "LLMResponse", "get_prompt", "get_prompt_hash"]
