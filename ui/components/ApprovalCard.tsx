"use client";
import React from "react";
import { ScopeDiff } from "./ScopeDiff";

export function ApprovalCard({ approval, onDecide }: { approval: any; onDecide: (d: string) => void }) {
  const snap = approval.proposal_snapshot;
  const curr = approval.proposal_current;
  const risk = approval.risk_level || snap?.risk_level || "low";
  const total = approval.total ?? snap?.total ?? 0;
  const currency = approval.currency ?? snap?.currency ?? "USD";
  const lines = snap?.lines || [];

  return (
    <div>
      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Solicitud {approval.approval_id}</h2>
          <span className={`badge ${approval.status === "pending" ? "badge-pending" : "badge-approved"}`}>{approval.status}</span>
        </div>
        <p style={{ fontSize: 13, color: "#475569" }}>
          Execution <span className="mono">{approval.execution_id}</span> · Request <span className="mono">{approval.request_id}</span>
        </p>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", margin: "12px 0" }}>
          <span className="badge badge-high">risk {risk}</span>
          <span className="mono">total {total} {currency}</span>
          <span className="mono">scope {approval.scope_hash?.slice(0, 16)}…</span>
          <span className="mono">required {approval.required_approvals} · received {approval.approvals_received}</span>
        </div>
        <div style={{ fontSize: 13 }}>
          <strong>Líneas</strong>
          <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 8 }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid #e2e8f0" }}>
                <th>SKU</th><th>Qty</th><th>Unit</th><th>Unit price</th><th>Currency</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l: any, i: number) => (
                <tr key={i} style={{ borderBottom: "1px solid #f1f5f9" }}>
                  <td>{l.sku}</td><td>{l.quantity}</td><td>{l.unit}</td><td>{l.unit_price}</td><td>{l.currency}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ marginTop: 12, fontSize: 13 }}>
          <strong>Evidencia</strong>
          <p style={{ background: "#f8fafc", padding: 8, borderRadius: 6 }}>{snap?.evidence || "—"}</p>
          <strong>Políticas aplicadas</strong>
          <p className="mono">{(snap?.policies_applied || []).join(", ") || "—"}</p>
          <strong>Supuestos</strong>
          <p style={{ fontSize: 12, color: "#64748b" }}>{(snap?.assumptions || []).join("; ") || "—"}</p>
          <strong>Missing data</strong>
          <p style={{ fontSize: 12, color: "#64748b" }}>{(snap?.missing_data || []).join("; ") || "—"}</p>
        </div>
      </div>

      <ScopeDiff snapshot={snap} current={curr} />

      <div className="card">
        <h4>Decidir</h4>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={() => onDecide("approved")}>Aprobar</button>
          <button className="btn btn-outline" onClick={() => onDecide("rejected")}>Rechazar</button>
          <button className="btn btn-outline" onClick={() => onDecide("needs_changes")}>Pedir cambios</button>
        </div>
        <p style={{ fontSize: 12, color: "#64748b" }}>Ver impacto: aprobar ejecuta <span className="mono">submit_purchase_order</span> idempotente; rechazar es terminal.</p>
      </div>

      <div className="card">
        <h4>Auditoría</h4>
        <p style={{ fontSize: 12 }}>Trace <span className="mono">{approval.execution_id}</span> · <a href={`/executions/${approval.execution_id}`}>ver timeline</a></p>
        <p style={{ fontSize: 12, color: "#64748b" }}>Scope hash truncado en notificación y link inbox. Ver docs/operations/runbooks/approval-stuck.md</p>
      </div>
    </div>
  );
}
