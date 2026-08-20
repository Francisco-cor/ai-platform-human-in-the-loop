from procurement_platform.domain.models import (
    ExecutionState,
    Proposal,
    ProposalLine,
    is_valid_transition,
)


def test_valid_transitions():
    assert is_valid_transition(ExecutionState.RECEIVED, ExecutionState.NORMALIZED)
    assert is_valid_transition(ExecutionState.NORMALIZED, ExecutionState.BLOCKED)  # terminal branch always valid
    assert is_valid_transition(ExecutionState.AWAITING_APPROVAL, ExecutionState.APPROVED)
    assert is_valid_transition(ExecutionState.AWAITING_APPROVAL, ExecutionState.REJECTED)
    assert not is_valid_transition(ExecutionState.RECEIVED, ExecutionState.COMPLETED)
    assert not is_valid_transition(ExecutionState.RECEIVED, ExecutionState.ACTION_EXECUTED)


def test_scope_hash_deterministic():
    lines = [{"sku": "MAT-001", "quantity": 10, "unit_price": 5.0}]
    h1 = Proposal.compute_scope_hash(proposal_id="prop_1", supplier_id="sup_1", lines=lines, total=50.0, currency="USD")
    h2 = Proposal.compute_scope_hash(proposal_id="prop_1", supplier_id="sup_1", lines=lines, total=50.0, currency="USD")
    assert h1 == h2
    assert h1.startswith("sha256:")
    h3 = Proposal.compute_scope_hash(proposal_id="prop_1", supplier_id="sup_1", lines=lines, total=51.0, currency="USD")
    assert h1 != h3


def test_proposal_total():
    lines = [
        ProposalLine(sku="MAT-001", quantity=10, unit_price=5.0),
        ProposalLine(sku="MAT-002", quantity=2, unit_price=100.0),
    ]
    assert Proposal.compute_total(lines, tax=10) == 260.0
