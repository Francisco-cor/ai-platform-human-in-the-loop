"""
Platform core — generic agentic platform (Fase 11).

This package contains reusable, domain-agnostic primitives:
- workflow: durable execution engine, checkpoint, transitions
- gateway: tool execution boundary (allowlist, budgets, idempotency)
- approvals: human-in-the-loop snapshot, scope_hash, SLA
- audit: append-only event bus with trace correlation
- rag: secure retrieval (tenant, vigencia, injection quarantine)
- llm: provider abstraction (Gemini → DeepSeek → Fake), prompt registry, cache
- evals: harness generic for any domain

No domain import (inventory, suppliers, procurement policies) at top level.
Domains (procurement, expense) depend on platform, not vice versa.
"""

from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("procurement-platform")
except Exception:
    __version__ = "0.1.0"

__all__ = ["__version__"]

# Lazy exports to avoid importing domains at platform import time
def get_platform_info() -> dict:
    return {
        "version": __version__,
        "modules": ["workflow", "gateway", "approvals", "audit", "rag", "llm", "evals"],
        "contract": "platform core is domain-agnostic; domains import from platform",
    }
