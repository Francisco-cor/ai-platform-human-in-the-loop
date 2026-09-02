"use client";
import React from "react";

export function Timeline({ events, executionId }: { events: any[]; executionId: string }) {
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Timeline — request_id → execution_id → node → tool → approval → trace_id</h3>
      <p style={{ fontSize: 12, color: "#64748b" }}>Execution <span className="mono">{executionId}</span> · correlación §13 obligatoria</p>
      <ol style={{ paddingLeft: 18 }}>
        {events.map((e: any) => (
          <li key={e.event_id} style={{ marginBottom: 8, fontSize: 13 }}>
            <strong>{e.event_type}</strong> — <span className="mono">{e.actor_type}:{e.actor_id}</span> {e.tool_name ? `· tool ${e.tool_name}` : ""} <br />
            <span style={{ fontSize: 12, color: "#64748b" }}>
              trace <span className="mono">{e.trace_id}</span> {e.span_id ? `span ${e.span_id}` : ""} {e.duration_ms ? `${e.duration_ms}ms` : ""} {e.model_metadata ? `· prompt ${e.model_metadata.prompt_version} hash ${e.model_metadata.prompt_hash?.slice(0,12)}` : ""}
            </span>
            {e.details && Object.keys(e.details).length ? (
              <details style={{ marginTop: 4 }}>
                <summary style={{ cursor: "pointer", fontSize: 12 }}>details</summary>
                <pre style={{ background: "#f8fafc", padding: 8, borderRadius: 6, overflow: "auto", fontSize: 11 }}>{JSON.stringify(e.details, null, 2)}</pre>
              </details>
            ) : null}
          </li>
        ))}
      </ol>
      <p style={{ fontSize: 12, color: "#64748b" }}>Link a Grafana: <span className="mono">trace_id {events[0]?.trace_id}</span> → Grafana Tempo/Jaeger</p>
    </div>
  );
}
