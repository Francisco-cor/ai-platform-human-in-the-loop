// API proxy for procurement platform — Fase 7 HITL
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export async function getApproval(approvalId: string) {
  const res = await fetch(`${API_BASE}/v1/approvals/${approvalId}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`getApproval ${res.status}`);
  return res.json();
}

export async function listApprovals(params?: { tenant?: string; state?: string }) {
  const q = new URLSearchParams();
  if (params?.tenant) q.set("tenant", params.tenant);
  if (params?.state) q.set("state", params.state);
  const res = await fetch(`${API_BASE}/v1/approvals?${q.toString()}`, { cache: "no-store" }).catch(() => null);
  // fallback to scanning executions (MVP)
  if (!res || !res.ok) return { approvals: [] };
  return res.json();
}

export async function decideApproval(approvalId: string, decision: "approved" | "rejected" | "needs_changes", decided_by: string, reason?: string, scope_hash?: string) {
  const res = await fetch(`${API_BASE}/v1/approvals/${approvalId}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": `${approvalId}:${decision}:${decided_by}` },
    body: JSON.stringify({ decision, decided_by, reason, scope_hash }),
  });
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(txt);
  }
  return res.json();
}

export async function getExecution(executionId: string) {
  const res = await fetch(`${API_BASE}/v1/procurement/executions/${executionId}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`getExecution ${res.status}`);
  return res.json();
}

export async function getEvents(executionId: string, format: "trace" | "json" = "trace") {
  const res = await fetch(`${API_BASE}/v1/procurement/executions/${executionId}/events?format=${format}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`getEvents ${res.status}`);
  return res.json();
}

export async function bulkDecide(approvalIds: string[], decision: string, decided_by: string, reason?: string) {
  const res = await fetch(`${API_BASE}/v1/approvals/bulk/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approval_ids: approvalIds, decision, decided_by, reason }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
