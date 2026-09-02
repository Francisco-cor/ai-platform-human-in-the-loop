"use client";
import { useEffect, useState } from "react";
import { getApproval, decideApproval } from "../../../lib/api";
import { ApprovalCard } from "../../../components/ApprovalCard";

export default function ApprovalPage({ params }: { params: { id: string } }) {
  const [approval, setApproval] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      const data = await getApproval(params.id);
      setApproval(data);
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [params.id]);

  async function handle(decision: string) {
    setMsg("");
    try {
      const decided_by = prompt("Tu usuario (ej approver_01):", "approver_01") || "approver_01";
      const res = await decideApproval(params.id, decision as any, decided_by, `decided via inbox`, approval.scope_hash);
      setMsg(JSON.stringify(res, null, 2));
      await load();
    } catch (e: any) {
      setMsg(e.message);
    }
  }

  if (loading) return <p>Cargando…</p>;
  if (!approval) return <p>Not found — {msg}</p>;

  return (
    <div>
      <ApprovalCard approval={approval} onDecide={handle} />
      {msg ? <pre style={{ background: "#f8fafc", padding: 12, borderRadius: 6, fontSize: 12 }}>{msg}</pre> : null}
      <p style={{ fontSize: 12, color: "#64748b" }}>
        Timeline: <a href={`/executions/${approval.execution_id}`}>ver execution timeline</a> · Grafana trace <span className="mono">{approval.execution_id}</span>
      </p>
    </div>
  );
}
