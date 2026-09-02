"use client";
import { useEffect, useState } from "react";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export default function InboxPage() {
  const [approvals, setApprovals] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("pending");

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        // For MVP, we list via scanning executions not yet approved; fallback to mock if API list not implemented
        // Try bulk export to get pending
        const res = await fetch(`${API_BASE}/v1/approvals/export?state=${filter}`, { cache: "no-store" }).catch(() => null);
        if (res && res.ok) {
          const txt = await res.text();
          // CSV -> parse quick
          const lines = txt.split("\n").filter(Boolean);
          // header + rows
          if (lines.length > 1) {
            const rows = lines.slice(1).map((l) => {
              const [approval_id, execution_id, status, total, risk] = l.split(",");
              return { approval_id, execution_id, status, total, risk_level: risk };
            });
            setApprovals(rows);
          } else {
            setApprovals([]);
          }
        } else {
          // fallback demo data notice
          setApprovals([]);
        }
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [filter]);

  return (
    <div>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Inbox</h2>
        <p style={{ fontSize: 13, color: "#475569" }}>Aprobador ve propuesta exacta + scope_hash + líneas + total + evidencia RAG + políticas + riesgo. Decide en &lt;2 min.</p>
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <button className={filter === "pending" ? "btn" : "btn btn-outline"} onClick={() => setFilter("pending")}>Pendientes</button>
          <button className={filter === "approved" ? "btn" : "btn btn-outline"} onClick={() => setFilter("approved")}>Aprobadas</button>
          <button className={filter === "all" ? "btn" : "btn btn-outline"} onClick={() => setFilter("all")}>Todas</button>
        </div>
        {loading ? <p>Cargando…</p> : approvals.length === 0 ? <p style={{ fontSize: 13, color: "#64748b" }}>No hay aprobaciones {filter}. Crea una ejecución via API y recarga.</p> : null}
        <ul style={{ paddingLeft: 0, listStyle: "none" }}>
          {approvals.map((a) => (
            <li key={a.approval_id} style={{ border: "1px solid #e2e8f0", borderRadius: 6, padding: 12, marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
              <div>
                <strong>{a.approval_id}</strong> <span className="mono">{a.execution_id}</span> <span className={`badge ${a.status === "pending" ? "badge-pending" : "badge-approved"}`}>{a.status}</span>
                <div style={{ fontSize: 12, color: "#475569" }}>total {a.total} · risk {a.risk_level}</div>
              </div>
              <Link href={`/approvals/${a.approval_id}`} style={{ alignSelf: "center" }} className="btn">Ver</Link>
            </li>
          ))}
        </ul>
        <p style={{ fontSize: 12, color: "#64748b" }}>Notificación llega en &lt;60s tras `approval.requested` (Slack/Email/webhook). Escalamiento auto a las 12h si no se decide.</p>
      </div>
      <div className="card">
        <h4>Bulk actions</h4>
        <p style={{ fontSize: 12, color: "#64748b" }}>Selecciona múltiples aprobaciones y decide en lote via <span className="mono">POST /v1/approvals/bulk/decision</span> (RBAC admin). Export CSV via <span className="mono">GET /v1/approvals/export</span>.</p>
      </div>
    </div>
  );
}
