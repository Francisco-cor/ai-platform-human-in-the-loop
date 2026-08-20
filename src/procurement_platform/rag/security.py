"""Seguridad RAG — detección de injection, obsolescencia y conflictos (Fase 3 §10).

Defensa en múltiples capas: clasificación, separación de mensajes, allowlist, policy engine, pruebas adversariales.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# Patrones de prompt injection (directa e indirecta)
INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+previous\s+instructions", re.I),
    re.compile(r"ignore\s+all\s+previous", re.I),
    re.compile(r"system\s*:\s*", re.I),
    re.compile(r"you\s+are\s+now\s+", re.I),
    re.compile(r"approve\s+supplier\s+\w+", re.I),
    re.compile(r"disregard\s+.*policy", re.I),
    re.compile(r"exfiltrate|leak\s+data", re.I),
    re.compile(r"jailbreak", re.I),
    re.compile(r"\[INST\]", re.I),
    re.compile(r"<\|system\|>", re.I),
    # variaciones en español
    re.compile(r"ignora\s+instrucciones\s+previas", re.I),
    re.compile(r"aprueba\s+proveedor", re.I),
]

# Señales de contenido sospechoso
SUSPICIOUS_MARKERS = [
    "hidden instruction",
    "do not follow",
    "override policy",
]


def detect_prompt_injection(text: str) -> dict[str, Any]:
    """Detecta intento de controlar al agente. Retorna dict con flags."""
    hits: list[str] = []
    for pat in INJECTION_PATTERNS:
        if pat.search(text):
            hits.append(pat.pattern)
    # heurística adicional: longitud anómala con pocas palabras pero muchos tokens de instrucción
    lower = text.lower()
    suspicious_count = sum(1 for m in SUSPICIOUS_MARKERS if m.lower() in lower)
    is_malicious = len(hits) > 0 or suspicious_count > 0
    # severidad
    if len(hits) >= 2:
        severity = "high"
    elif len(hits) == 1:
        severity = "medium"
    elif suspicious_count > 0:
        severity = "low"
    else:
        severity = "none"

    return {
        "is_malicious": is_malicious,
        "hits": hits,
        "suspicious_count": suspicious_count,
        "severity": severity,
        "flags": ["prompt_injection"] if is_malicious else [],
    }


def classify_content(text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Clasifica contenido en normativo vs no confiable.

    Fase 3: separa contenido normativo de texto libre no confiable.
    """
    injection = detect_prompt_injection(text)
    # heurística: si contiene marcadores de sección normativa (ej: "Política", "§", "Artículo") es más confiable
    normative_markers = ["política", "policy", "artículo", "section", "§", "normativa"]
    lower = text.lower()
    is_normative = any(m.lower() in lower for m in normative_markers) and not injection["is_malicious"]

    return {
        "is_malicious": injection["is_malicious"],
        "is_normative": is_normative,
        "injection": injection,
        "reliability": "untrusted" if injection["is_malicious"] else ("high" if is_normative else "medium"),
        "flags": injection["flags"],
    }


def check_obsolescence(valid_from: datetime, valid_to: datetime | None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    is_expired = valid_to is not None and valid_to < now
    is_future = valid_from > now
    is_valid = not is_expired and not is_future
    return {
        "is_valid": is_valid,
        "is_expired": is_expired,
        "is_future": is_future,
        "now": now,
        "valid_from": valid_from,
        "valid_to": valid_to,
    }


def detect_conflict(policies: list[dict[str, Any]]) -> dict[str, Any]:
    """Detecta conflicto entre políticas autorizadas (ej: mismo tipo con valores distintos).

    Fase 3: si dos políticas autorizadas contradicen una regla, no resolver por preferencia textual,
    aplicar precedencia o escalar.
    """
    # agrupar por (tenant, policy_type, location)
    groups: dict[tuple, list[dict]] = {}
    for p in policies:
        key = (p.get("tenant_id"), p.get("policy_type"), p.get("location_id"))
        groups.setdefault(key, []).append(p)

    conflicts: list[dict] = []
    for key, group in groups.items():
        if len(group) <= 1:
            continue
        # si hay valores distintos para mismo presupuesto, conflicto
        # ejemplo: budget_limit con delegated_limit distinto
        values = set()
        for p in group:
            # extraer valor relevante
            rules = p.get("rules") or p.get("facts") or {}
            # para budget
            if "delegated_limit" in rules:
                values.add(rules["delegated_limit"])
            elif "order_total" in rules:
                values.add(rules["order_total"])
            else:
                values.add(str(rules))
        if len(values) > 1:
            conflicts.append({"key": key, "policies": [p.get("document_id") or p.get("policy_id") for p in group], "values": list(values)})

    return {
        "has_conflict": len(conflicts) > 0,
        "conflicts": conflicts,
        "requires_human_review": len(conflicts) > 0,
    }


def should_block_execution(
    *,
    is_malicious: bool,
    is_expired: bool,
    has_conflict: bool,
    reliability: str,
) -> tuple[bool, str]:
    """Decide si bloquear ejecución automática basado en contenido no confiable.

    Fase 3 criterio: no ejecutar acción basada exclusivamente en texto no confiable.
    """
    if is_malicious:
        return True, "documento malicioso detectado"
    if is_expired:
        # no bloquear automáticamente si solo es obsoleto, pero excluir de decisiones
        return False, "documento obsoleto excluido"
    if has_conflict:
        return True, "conflicto entre políticas requiere revisión humana"
    if reliability == "untrusted":
        return True, "contenido no confiable"
    return False, "ok"
