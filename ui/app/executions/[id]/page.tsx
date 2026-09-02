"use client";
import { useEffect, useState } from "react";
import { getExecution, getEvents } from "../../../lib/api";
import { Timeline } from "../../../components/Timeline";

export default function ExecutionPage({ params }: { params: { id: string } }) {
  const [execution, setExecution] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [traceId, setTraceId] = useState<string>("");

  useEffect(() => {
    async function load() {
      try {
        const ex = await getExecution(params.id);
        setExecution(ex);
        setTraceId(ex.trace_id || "");
        const ev = await getEvents(params.id, "trace");
        setEvents(ev.timeline || ev.events || []);
      } catch (e) {
        console.error(e);
      }
    }
    load();
  }, [params.id]);

  if (!execution) return <p>Cargando execution {params.id}…</p>;

  return (
    <div>
      <div className="card">
        <h2>Execution {execution.execution_id}</h2>
        <p>status <span className="mono">{execution.status}</span> · node <span className="mono">{execution.current_node}</span> · trace <span className="mono">{traceId}</span></p>
        <p style={{ fontSize: 12, color: "#64748b" }}>Correlación obligatoria: request_id → execution_id → workflow_run_id → node_run_id → tool_call_id → approval_id → audit_event_id → trace_id §13.</p>
      </div>
      <Timeline events={events} executionId={params.id} />
    </div>
  );
}
