"""Input validation — Fase 7.

Valida raw_intent y documentos para injection directa/indirecta y PII.
Separa validación de la lógica de orquestación.
"""

from __future__ import annotations

from typing import Any

from procurement_platform.rag.security import detect_prompt_injection
from procurement_platform.security.pii import detect_pii, redact_pii


def validate_raw_intent(raw_intent: str | None) -> dict[str, Any]:
    """Valida raw_intent para injection directa.

    Retorna dict con: is_valid, is_malicious, severity, hits, pii, reason, should_block
    should_block True si is_malicious high/medium.
    """
    if not raw_intent:
        return {
            "is_valid": True,
            "is_malicious": False,
            "severity": "none",
            "hits": [],
            "pii": {"has_pii": False},
            "reason": "empty",
            "should_block": False,
        }
    inj = detect_prompt_injection(raw_intent)
    pii = detect_pii(raw_intent)
    # pii en raw_intent no bloquea, pero se debe redactar antes de LLM/logging
    is_malicious = inj["is_malicious"]
    severity = inj["severity"]
    should_block = is_malicious and severity in ("high", "medium")
    # también bloquear si contiene instrucciones de exfiltrate/leak aunque sea low pero con is_malicious
    if is_malicious and not should_block and severity == "low":
        # si es low pero contiene approve supplier, también bloquear (alta sensibilidad)
        if any("approve" in h.lower() for h in inj["hits"]):
            should_block = True
    return {
        "is_valid": not should_block,
        "is_malicious": is_malicious,
        "severity": severity,
        "hits": inj["hits"],
        "pii": pii,
        "reason": "direct_injection" if should_block else "ok",
        "should_block": should_block,
    }


def sanitize_for_llm(text: str) -> str:
    """Redacta PII y envuelve contenido no confiable para LLM.

    No modifica instrucciones del sistema, solo sanitiza user content.
    """
    if not text:
        return text
    redacted, _ = redact_pii(text)
    # Envolver en delimitadores para separación de instrucciones (defensa prompt injection)
    # El LLM debe tratar el contenido como DATA, no como instrucción.
    return f"<user_data>{redacted}</user_data>"


def validate_document_content(content: str) -> dict[str, Any]:
    """Valida contenido de documento para RAG.

    Combina injection + pii. No bloquea por pii, solo redacción.
    """
    inj = detect_prompt_injection(content)
    pii = detect_pii(content)
    return {
        "is_malicious": inj["is_malicious"],
        "severity": inj["severity"],
        "hits": inj["hits"],
        "has_pii": pii["has_pii"],
        "pii_count": pii["count"],
        "should_quarantine": inj["is_malicious"],
        "should_redact_pii": pii["has_pii"],
    }
