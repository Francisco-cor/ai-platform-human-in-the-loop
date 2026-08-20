"""Fake LLM adapter — determinista para tests y CI (Fase 4)."""
from __future__ import annotations

import json
import time

from procurement_platform.agents.adapter import LLMRequest, LLMResponse, LLMUsage


class FakeAdapter:
    provider = "fake"
    model = "fake"

    def __init__(self, mode: str = "happy") -> None:
        # mode: happy | invalid_json | missing_fields | high_tokens
        self.mode = mode

    def supports_structured_output(self) -> bool:
        return True

    async def generate(self, request: LLMRequest) -> LLMResponse:
        start = time.time()
        # Determinar respuesta basada en el prompt y schema
        # Si el schema pide proposal, devolver una propuesta válida determinista
        raw: str
        content: dict | str
        if request.response_schema and "supplier_id" in str(request.response_schema).lower():
            # draft_proposal schema
            if self.mode == "invalid_json":
                raw = "this is not json"
                content = raw
            elif self.mode == "missing_fields":
                # JSON válido pero faltan campos requeridos
                raw = json.dumps({"supplier_id": "supplier_demo"})
                content = json.loads(raw)
            else:
                proposal = {
                    "supplier_id": "supplier_demo",
                    "lines": [{"sku": "MAT-001", "quantity": 120, "unit": "piece", "unit_price": 10.0}],
                    "evidence": "fake — seleccionó supplier_demo por precio y lead_time (determinista)",
                    "confidence": 0.95,
                    "risk_level": "low",
                    "assumptions": ["fake pricing"],
                    "missing_data": [],
                    "requires_human_approval": True,
                }
                raw = json.dumps(proposal)
                content = proposal
        elif "normalize" in request.user_prompt.lower() or "items" in str(request.response_schema).lower():
            # normalize_request
            if self.mode == "invalid_json":
                raw = "not json"
                content = raw
            else:
                norm = {"items": [{"sku": "MAT-001", "quantity": 120, "unit": "piece"}], "horizon_days": 21, "location_id": "warehouse_north", "explanation": "fake normalization"}
                raw = json.dumps(norm)
                content = norm
        else:
            # default: sintetizar evidence
            raw = json.dumps({"summary": "fake evidence", "citations": []})
            content = json.loads(raw)

        # usage fake
        usage = LLMUsage(prompt_tokens=len(request.user_prompt) // 4, completion_tokens=len(raw) // 4, total_tokens=len(request.user_prompt) // 4 + len(raw) // 4)

        latency_ms = int((time.time() - start) * 1000)
        # Simular error en modos inválidos
        if self.mode in ("invalid_json", "missing_fields"):
            # No lanzamos excepción aquí; dejamos que el caller valide y reintente
            pass

        return LLMResponse(
            request_id=request.request_id,
            provider=self.provider,
            model=self.model,
            content=content,
            raw_content=raw,
            usage=usage,
            latency_ms=latency_ms,
            prompt_version=request.prompt_version,
            graph_version=request.graph_version,
            finish_reason="stop",
        )
