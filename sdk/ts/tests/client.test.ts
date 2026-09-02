import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { ProcurementClient } from "../src/client";

const server = setupServer(
  http.post("http://test/v1/procurement/executions", ({ request }) => {
    const key = request.headers.get("Idempotency-Key");
    if (!key) return HttpResponse.json({ code: "missing_key" }, { status: 400 });
    return HttpResponse.json({ execution_id: "exec_ts_123", approval_request: { approval_id: "appr_ts_123" }, status: "AWAITING_APPROVAL" }, { status: 202 });
  }),
  http.get("http://test/v1/procurement/executions/exec_ts_123", () => {
    return HttpResponse.json({ execution_id: "exec_ts_123", status: "AWAITING_APPROVAL" });
  }),
  http.post("http://test/v1/approvals/appr_ts_123/decision", ({ request }) => {
    const key = request.headers.get("Idempotency-Key");
    if (!key) return HttpResponse.json({ code: "missing_key" }, { status: 400 });
    return HttpResponse.json({ approval_id: "appr_ts_123", status: "approved", execution_status: "COMPLETED" });
  }),
  http.get("http://test/v1/procurement/executions/exec_ts_123/events", () => {
    return HttpResponse.json({ execution_id: "exec_ts_123", count: 1, total: 1, events: [] });
  }),
  http.get("http://test/v1/procurement/executions", () => {
    return HttpResponse.json({ count: 1, total_count: 1, has_more: false, executions: [{ execution_id: "exec_ts_123" }] });
  })
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("ProcurementClient TS", () => {
  it("create and approve via SDK", async () => {
    const client = new ProcurementClient({ baseUrl: "http://test" });
    const resp = await client.createExecution({ tenant_id: "tenant_demo" });
    expect(resp.execution_id).toBe("exec_ts_123");
    const detail = await client.getExecution("exec_ts_123");
    expect(detail.status).toBe("AWAITING_APPROVAL");
    const dec = await client.approve("appr_ts_123", "approver_01");
    expect(dec.status).toBe("approved");
  });

  it("has Idempotency-Key auto", async () => {
    let seen = new Set<string>();
    server.use(
      http.post("http://test/v1/procurement/executions", ({ request }) => {
        const k = request.headers.get("Idempotency-Key")!;
        expect(k).toBeTruthy();
        seen.add(k);
        return HttpResponse.json({ execution_id: "exec_1", approval_request: { approval_id: "appr_1" } }, { status: 202 });
      })
    );
    const client = new ProcurementClient({ baseUrl: "http://test" });
    await client.createExecution({ tenant_id: "tenant_demo" });
    expect(seen.size).toBe(1);
  });

  it("retries on 429", async () => {
    let count = 0;
    server.use(
      http.post("http://test/v1/procurement/executions", () => {
        count++;
        if (count === 1) return HttpResponse.json({ code: "rate_limited" }, { status: 429, headers: { "Retry-After": "0" } });
        return HttpResponse.json({ execution_id: "exec_retry", approval_request: { approval_id: "appr_retry" } }, { status: 202 });
      })
    );
    const client = new ProcurementClient({ baseUrl: "http://test", maxRetries: 2 });
    const resp = await client.createExecution({ tenant_id: "tenant_demo" });
    expect(resp.execution_id).toBe("exec_retry");
    expect(count).toBe(2);
  });
});
