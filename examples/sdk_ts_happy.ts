// SDK TypeScript happy flow — Fase 8 DX
import { ProcurementClient } from "../sdk/ts/src/client";

async function main() {
  const client = new ProcurementClient({ baseUrl: "http://localhost:8000" });
  // create
  const resp = await client.createExecution({
    tenant_id: "tenant_demo",
    requester_id: "user_01",
    items: [{ sku: "MAT-001", quantity: 10, unit: "piece" }],
  });
  console.log("created", resp.execution_id, resp.approval_request.approval_id);

  // list paginated
  const page = await client.listExecutions({ tenant_id: "tenant_demo", limit: 2 });
  console.log("list", page.total_count, page.has_more);

  // approve
  const aid = resp.approval_request.approval_id;
  const detail = await client.getApproval(aid);
  console.log("approval", detail.status, detail.total);
  const dec = await client.approve(aid, "approver_01");
  console.log("approve", dec.status);

  // verify
  const final = await client.getExecution(resp.execution_id);
  console.log("final", final.status);

  // events
  const events = await client.listEvents(resp.execution_id, { limit: 5, format: "trace" });
  console.log("events", events.count, events.trace_id);

  // webhook
  const wh = await client.createWebhook("http://webhook.site/test", "secret123", ["execution.completed"]);
  console.log("webhook", wh.id);
}

main().catch(console.error);
