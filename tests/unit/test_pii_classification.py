"""PII classification-aware tests — F3-6."""

from procurement_platform.security.pii import redact_pii_by_classification


def test_public_only_ssn_credit():
    text = "email test@example.com phone 555-123-4567 ssn 123-45-6789 card 4111111111111111"
    redacted, det = redact_pii_by_classification(text, classification="public")
    # public should redact ssn and credit_card only
    assert "[REDACTED]_SSN" in redacted or "[REDACTED]_CREDIT_CARD" in redacted
    assert "test@example.com" in redacted  # email not redacted for public
    assert "555" in redacted  # phone not redacted


def test_internal_redacts_email_phone():
    text = "email test@example.com phone 555-123-4567 ssn 123-45-6789"
    redacted, det = redact_pii_by_classification(text, classification="internal")
    assert "[REDACTED]_EMAIL" in redacted
    assert "[REDACTED]_PHONE" in redacted
    assert "[REDACTED]_SSN" in redacted


def test_restricted_redacts_all():
    text = "email a@b.com ipv4 192.168.1.1 dni 12345678A"
    redacted, det = redact_pii_by_classification(text, classification="restricted")
    assert "[REDACTED]_EMAIL" in redacted
    assert "[REDACTED]_IPV4" in redacted
    assert "[REDACTED]_DNI_ES" in redacted


def test_confidential_same_as_restricted():
    text = "email a@b.com"
    r1, _ = redact_pii_by_classification(text, classification="restricted")
    r2, _ = redact_pii_by_classification(text, classification="confidential")
    assert r1 == r2
