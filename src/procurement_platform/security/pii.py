"""PII detection & redaction — Fase 7 §16.

Detecta y redacta PII en documentos, prompts, logs y traces.
No pretende ser DLP completo; cubre clases comunes para demo + tests.

Patrones:
- email
- teléfono (internacional y formato US/colombiano)
- SSN-like (###-##-####)
- credit card (Luhn-ish 13-19 dígitos con espacios/guiones)
- IPv4
- DNI español (8 dígitos + letra) / NIE
"""

from __future__ import annotations

import re

PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")),
    ("phone", re.compile(r"(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{4}\b")),
    # más específico para evitar sobre-match: si contiene @ ya es email, prioriza email
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("dni_es", re.compile(r"\b\d{8}[A-Za-z]\b")),
    ("nie_es", re.compile(r"\b[XYZ]\d{7}[A-Za-z]\b")),
]

# Allowlist para no marcar precios/cantidades como phone/credit_card
# Heurística: si el match es parte de contexto numérico financiero (ej "límite 5000"), no es PII
FINANCIAL_CONTEXT_RE = re.compile(r"(límite|total|precio|quantity|amount).{0,20}\d+", re.I)


def _is_likely_financial(text: str, match: re.Match) -> bool:
    # si el número está cerca de palabras financieras, considerarlo no PII
    start = max(0, match.start() - 30)
    snippet = text[start : match.end() + 10]
    if FINANCIAL_CONTEXT_RE.search(snippet):
        # pero si es email nunca es financiero
        return True
    return False


def detect_pii(text: str) -> dict:
    """Detecta PII. Retorna {has_pii: bool, findings: [{type, value, span}], count}."""
    if not text or not isinstance(text, str):
        return {"has_pii": False, "findings": [], "count": 0}
    findings: list[dict] = []
    for pii_type, pat in PII_PATTERNS:
        for m in pat.finditer(text):
            val = m.group(0).strip()
            # filtros para reducir falsos positivos
            if pii_type == "phone":
                # teléfono debe tener al menos 7 dígitos y no ser un SKU o cantidad pequeña
                digits = re.sub(r"\D", "", val)
                if len(digits) < 7 or len(digits) > 15:
                    continue
                # si es "MAT-001" no matchea por regex phone, ok
                if _is_likely_financial(text, m) and len(digits) <= 5:
                    continue
            if pii_type == "credit_card":
                digits = re.sub(r"\D", "", val)
                if len(digits) < 13 or len(digits) > 19:
                    continue
                # si es financial context y solo 4-6 dígitos, ignorar (ya filtrado)
                if _is_likely_financial(text, m):
                    # si no parece tarjeta (no tiene 16 dígitos típicos), ignorar
                    if len(digits) not in (13, 15, 16, 19):
                        continue
                # Luhn check opcional pero laxo para demo: al menos no todos iguales
                if len(set(digits)) == 1:
                    continue
            if pii_type == "ipv4":
                parts = val.split(".")
                if not all(0 <= int(p) <= 255 for p in parts if p.isdigit()):
                    continue
            findings.append({"type": pii_type, "value": val, "span": (m.start(), m.end())})
    # Deduplicar por span (email también matchea phone? priorizar email)
    # Si dos findings se solapan, mantener el más específico (email > phone)
    findings_sorted = sorted(findings, key=lambda x: (x["span"][0], -len(x["value"])))
    deduped: list[dict] = []
    for f in findings_sorted:
        overlaps = False
        for d in deduped:
            # solapamiento si intervals intersect
            if not (f["span"][1] <= d["span"][0] or f["span"][0] >= d["span"][1]):
                # si ya hay email en esa zona, no añadir phone
                if d["type"] == "email" and f["type"] == "phone":
                    overlaps = True
                    break
                # si es más corto, también solapa
                overlaps = True
                break
        if not overlaps:
            deduped.append(f)
    return {"has_pii": len(deduped) > 0, "findings": deduped, "count": len(deduped)}


def redact_pii(text: str, mask: str = "[REDACTED]") -> tuple[str, dict]:
    """Redacta PII. Retorna (redacted_text, detection_result)."""
    if not text or not isinstance(text, str):
        return text, {"has_pii": False, "findings": [], "count": 0}
    det = detect_pii(text)
    if not det["has_pii"]:
        return text, det
    # reemplazar en orden inverso para no desplazar spans
    redacted = text
    for f in sorted(det["findings"], key=lambda x: x["span"][0], reverse=True):
        s, e = f["span"]
        # verificar que el slice actual aún coincide (por redacciones previas el offset cambia si usamos spans originales inversos está ok)
        # Usar redacted actual: el span se calculó sobre texto original, pero al ir de atrás hacia adelante es estable
        redacted = redacted[:s] + f"{mask}_{f['type'].upper()}" + redacted[e:]
    return redacted, det


def contains_pii(text: str) -> bool:
    return detect_pii(text)["has_pii"]


# Helper para audit: censurar details que puedan contener PII
def redact_dict_values(data: dict, max_depth: int = 3) -> dict:
    """Recursivamente redacta valores string que contienen PII."""
    if max_depth < 0 or not isinstance(data, dict):
        return data
    out: dict = {}
    for k, v in data.items():
        if isinstance(v, str):
            rv, det = redact_pii(v)
            out[k] = rv
        elif isinstance(v, dict):
            out[k] = redact_dict_values(v, max_depth - 1)
        elif isinstance(v, list):
            new_list = []
            for item in v:
                if isinstance(item, str):
                    rv, _ = redact_pii(item)
                    new_list.append(rv)
                elif isinstance(item, dict):
                    new_list.append(redact_dict_values(item, max_depth - 1))
                else:
                    new_list.append(item)
            out[k] = new_list
        else:
            out[k] = v
    return out


# F3-6: classification-aware redaction
CLASSIFICATION_POLICIES: dict[str, set[str]] = {
    "public": {"ssn", "credit_card"},
    "internal": {"email", "phone", "ssn", "credit_card", "dni_es", "nie_es"},
    "restricted": {"email", "phone", "ssn", "credit_card", "ipv4", "dni_es", "nie_es"},
    "confidential": {"email", "phone", "ssn", "credit_card", "ipv4", "dni_es", "nie_es"},
}


def redact_pii_by_classification(text: str, classification: str = "restricted", mask: str = "[REDACTED]") -> tuple[str, dict]:
    """Redacta según clasificación de documento.
    
    public -> solo ssn/credit_card
    internal -> email/phone/ssn/credit_card/dni
    restricted/confidential -> todo
    """
    if not text or not isinstance(text, str):
        return text, {"has_pii": False, "findings": [], "count": 0}
    allowed = CLASSIFICATION_POLICIES.get(classification, CLASSIFICATION_POLICIES["restricted"])
    det = detect_pii(text)
    if not det["has_pii"]:
        return text, det
    # filtrar findings por clasificación
    filtered = [f for f in det["findings"] if f["type"] in allowed]
    if not filtered:
        return text, {"has_pii": False, "findings": [], "count": 0}
    redacted = text
    for f in sorted(filtered, key=lambda x: x["span"][0], reverse=True):
        s, e = f["span"]
        redacted = redacted[:s] + f"{mask}_{f['type'].upper()}" + redacted[e:]
    return redacted, {"has_pii": True, "findings": filtered, "count": len(filtered)}


def redact_dict_by_classification(data: dict, classification: str = "restricted", max_depth: int = 3) -> dict:
    """Recursivamente redacta por clasificación."""
    if max_depth < 0 or not isinstance(data, dict):
        return data
    out: dict = {}
    for k, v in data.items():
        if isinstance(v, str):
            rv, _ = redact_pii_by_classification(v, classification=classification)
            out[k] = rv
        elif isinstance(v, dict):
            out[k] = redact_dict_by_classification(v, classification=classification, max_depth=max_depth - 1)
        elif isinstance(v, list):
            new_list = []
            for item in v:
                if isinstance(item, str):
                    rv, _ = redact_pii_by_classification(item, classification=classification)
                    new_list.append(rv)
                elif isinstance(item, dict):
                    new_list.append(redact_dict_by_classification(item, classification=classification, max_depth=max_depth - 1))
                else:
                    new_list.append(item)
            out[k] = new_list
        else:
            out[k] = v
    return out
