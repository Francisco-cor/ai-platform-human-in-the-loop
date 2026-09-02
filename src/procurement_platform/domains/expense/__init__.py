"""
Expense domain — second workflow (Fase 11).

Reuses platform core (gateway, approvals, audit, rag, llm) with <30% new code.

Domain: expense_approval
- Request: {amount, currency, reason, requester_id, tenant_id}
- Policy: amount>0, currency USD, budget 2000, requires approval if >1000
- Workflow 8 nodos: intake → normalize → load_context → retrieve_policies → validate → draft_proposal → policy_check → await_approval → executed → verified → completed
"""

from procurement_platform.domains.expense.models import ExpenseRequest, ExpenseProposal

__all__ = ["ExpenseRequest", "ExpenseProposal"]
