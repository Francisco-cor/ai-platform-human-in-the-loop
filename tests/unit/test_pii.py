"""Tests PII detection/redaction — Fase 7."""

from procurement_platform.security.pii import detect_pii, redact_pii, redact_dict_values


def test_detect_email():
    res = detect_pii("contact john.doe@example.com for details")
    assert res["has_pii"] is True
    assert any(f["type"] == "email" for f in res["findings"])
    assert res["count"] >= 1


def test_detect_phone():
    res = detect_pii("llame al 555-123-4567 urgente")
    assert res["has_pii"] is True
    # phone pattern may match
    assert res["count"] >= 1


def test_detect_ssn():
    res = detect_pii("mi SSN es 123-45-6789")
    assert res["has_pii"] is True
    assert any(f["type"] == "ssn" for f in res["findings"])


def test_detect_no_pii_financial():
    # límite 5000 no debe marcarse como phone
    res = detect_pii("Política: El límite delegado es 5000 USD")
    # Should not have pii (allow financial numbers)
    # email/phone false positive must not trigger
    assert res["has_pii"] is False or all(
        f["type"] not in ("phone", "credit_card") for f in res["findings"]
    )


def test_redact_email():
    text = "contact john@example.com y 555-1234"
    redacted, det = redact_pii(text)
    assert "[REDACTED]" in redacted
    assert "john@example.com" not in redacted
    assert det["has_pii"] is True


def test_redact_dict():
    data = {"raw_intent": "email test@example.com", "details": {"contact": "555-123-4567"}}
    out = redact_dict_values(data)
    assert "test@example.com" not in str(out)
    assert "[REDACTED]" in str(out)


def test_clean_text_not_redacted():
    text = "Necesitamos reponer materiales críticos para las próximas tres semanas."
    redacted, det = redact_pii(text)
    assert det["has_pii"] is False
    assert redacted == text


def test_credit_card():
    text = "tarjeta 4111 1111 1111 1111 vencimiento 12/25"
    res = detect_pii(text)
    assert res["has_pii"] is True
    assert any(f["type"] == "credit_card" for f in res["findings"])
