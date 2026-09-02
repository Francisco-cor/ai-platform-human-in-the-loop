"use client";
import React from "react";

// Fase 7 — diff viewer for scope_hash changes (snapshot vs current)
// Muestra diff de proposal_snapshot vs proposal_current si scope_mismatch
export function ScopeDiff({ snapshot, current }: { snapshot: any; current: any }) {
  if (!snapshot || !current) return null;
  const same = snapshot.scope_hash === current.scope_hash;
  if (same) {
    return (
      <div className="card" style={{ borderColor: "#86efac" }}>
        <h4 style={{ margin: "0 0 8px" }}>Scope check — OK</h4>
        <span className="mono">{snapshot.scope_hash}</span>
        <p style={{ fontSize: 13, color: "#475569" }}>No hay cambios entre snapshot aprobado y propuesta actual.</p>
      </div>
    );
  }

  // compute diffs
  const snapLines = snapshot.lines || [];
  const currLines = current.lines || [];
  const diffs: string[] = [];
  if (snapshot.supplier_id !== current.supplier_id) diffs.push(`supplier: ${snapshot.supplier_id} → ${current.supplier_id}`);
  if (snapshot.total !== current.total) diffs.push(`total: ${snapshot.total} → ${current.total}`);
  if (JSON.stringify(snapLines) !== JSON.stringify(currLines)) diffs.push(`lines changed`);
  if (snapshot.currency !== current.currency) diffs.push(`currency ${snapshot.currency} → ${current.currency}`);

  return (
    <div className="card" style={{ borderColor: "#fca5a5", background: "#fef2f2" }}>
      <h4 style={{ margin: "0 0 8px", color: "#991b1b" }}>⚠️ Scope mismatch — se requiere nueva aprobación</h4>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <span className="mono">snapshot {snapshot.scope_hash?.slice(0, 16)}…</span>
        <span className="mono">current {current.scope_hash?.slice(0, 16)}…</span>
      </div>
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
        {diffs.map((d, i) => (
          <li key={i}>{d}</li>
        ))}
      </ul>
      <p style={{ fontSize: 12, color: "#7f1d1d" }}>El aprobador no debe aprobar sin ver el objeto exacto. Ver PLAN §11.</p>
    </div>
  );
}
