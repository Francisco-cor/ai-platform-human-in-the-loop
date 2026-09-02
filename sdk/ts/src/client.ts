// ProcurementClient TypeScript — Fase 8 DX
export interface ProcurementClientOptions {
  baseUrl?: string;
  apiKey?: string;
  timeoutMs?: number;
  maxRetries?: number;
  fetch?: typeof fetch;
}

export class ProcurementError extends Error {
  statusCode?: number;
  code?: string;
  details?: any;
  constructor(message: string, statusCode?: number, code?: string, details?: any) {
    super(message);
    this.statusCode = statusCode;
    this.code = code;
    this.details = details;
  }
}

function uuid(): string {
  return Math.random().toString(36).slice(2, 10) + Math.random().toString(36).slice(2, 10);
}

export class ProcurementClient {
  private baseUrl: string;
  private apiKey?: string;
  private timeoutMs: number;
  private maxRetries: number;
  private fetchFn: typeof fetch;

  constructor(opts: ProcurementClientOptions = {}) {
    this.baseUrl = (opts.baseUrl || "http://localhost:8000").replace(/\/$/, "");
    this.apiKey = opts.apiKey;
    this.timeoutMs = opts.timeoutMs ?? 15000;
    this.maxRetries = opts.maxRetries ?? 3;
    this.fetchFn = opts.fetch || fetch;
  }

  private headers(extra?: Record<string, string>): Record<string, string> {
    const h: Record<string, string> = { "Content-Type": "application/json", ...(extra || {}) };
    if (this.apiKey) {
      h["Authorization"] = `Bearer ${this.apiKey}`;
      h["X-API-Key"] = this.apiKey;
    }
    return h;
  }

  private async request(method: string, path: string, opts: { headers?: Record<string, string>; body?: any; params?: Record<string, any> } = {}): Promise<any> {
    const url = new URL(`${this.baseUrl}${path}`);
    if (opts.params) {
      for (const [k, v] of Object.entries(opts.params)) {
        if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
      }
    }
    const headers = this.headers(opts.headers);
    // auto Idempotency-Key for POST creates
    if (method === "POST" && (path === "/v1/procurement/executions" || path.endsWith("/decision") || path === "/v1/approvals/bulk/decision") && !headers["Idempotency-Key"]) {
      headers["Idempotency-Key"] = `sdk_${uuid()}`;
    }
    let lastErr: any = null;
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
        const res = await this.fetchFn(url.toString(), {
          method,
          headers,
          body: opts.body ? JSON.stringify(opts.body) : undefined,
          signal: controller.signal,
        });
        clearTimeout(timeout);
        if ([429, 500, 502, 503, 504].includes(res.status) && attempt < this.maxRetries) {
          const retryAfter = res.headers.get("Retry-After");
          const wait = retryAfter ? parseInt(retryAfter, 10) * 1000 : 500 * 2 ** attempt;
          await new Promise((r) => setTimeout(r, Math.min(wait, 5000)));
          continue;
        }
        if (!res.ok) {
          let data: any = null;
          try { data = await res.json(); } catch { data = await res.text(); }
          const msg = data?.message || data?.msg || res.statusText;
          throw new ProcurementError(msg, res.status, data?.code, data);
        }
        const ctype = res.headers.get("content-type") || "";
        if (ctype.includes("text/csv")) return await res.text();
        const text = await res.text();
        if (!text) return {};
        try { return JSON.parse(text); } catch { return text; }
      } catch (e: any) {
        if (e instanceof ProcurementError) throw e;
        lastErr = e;
        if (attempt < this.maxRetries) {
          await new Promise((r) => setTimeout(r, 500 * 2 ** attempt));
          continue;
        }
        throw new ProcurementError(e.message || String(e));
      }
    }
    throw lastErr;
  }

  // Executions
  createExecution(payload: any, idempotencyKey?: string): Promise<any> {
    const h: Record<string, string> = {};
    if (idempotencyKey) h["Idempotency-Key"] = idempotencyKey;
    return this.request("POST", "/v1/procurement/executions", { headers: h, body: payload });
  }
  getExecution(executionId: string): Promise<any> {
    return this.request("GET", `/v1/procurement/executions/${executionId}`);
  }
  listExecutions(params: { tenant_id?: string; state?: string; limit?: number; cursor?: string } = {}): Promise<any> {
    return this.request("GET", "/v1/procurement/executions", { params: params as any });
  }
  resume(executionId: string): Promise<any> {
    return this.request("POST", `/v1/procurement/executions/${executionId}/resume`, { body: {} });
  }
  listEvents(executionId: string, params: { limit?: number; cursor?: string; format?: string } = {}): Promise<any> {
    return this.request("GET", `/v1/procurement/executions/${executionId}/events`, { params: params as any });
  }

  // Approvals
  getApproval(approvalId: string): Promise<any> {
    return this.request("GET", `/v1/approvals/${approvalId}`);
  }
  listApprovals(params: { tenant_id?: string; state?: string; limit?: number; cursor?: string } = {}): Promise<any> {
    return this.request("GET", "/v1/approvals", { params: params as any });
  }
  approve(approvalId: string, decidedBy: string, reason?: string, scopeHash?: string): Promise<any> {
    const body: any = { decision: "approved", decided_by: decidedBy };
    if (reason) body.reason = reason;
    if (scopeHash) body.scope_hash = scopeHash;
    return this.request("POST", `/v1/approvals/${approvalId}/decision`, { body });
  }
  bulkDecide(approvalIds: string[], decision: string, decidedBy: string, reason?: string): Promise<any> {
    return this.request("POST", "/v1/approvals/bulk/decision", { body: { approval_ids: approvalIds, decision, decided_by: decidedBy, reason } });
  }

  // Webhooks
  createWebhook(url: string, secret: string, events: string[], tenantId = "tenant_demo"): Promise<any> {
    return this.request("POST", "/v1/webhooks/subscriptions", { body: { url, secret, events, tenant_id: tenantId } });
  }
  listWebhooks(tenantId?: string): Promise<any> {
    return this.request("GET", "/v1/webhooks/subscriptions", { params: tenantId ? { tenant_id: tenantId } : {} });
  }

  // Ops
  health(): Promise<any> { return this.request("GET", "/healthz"); }
  readyz(): Promise<any> { return this.request("GET", "/readyz"); }
}
