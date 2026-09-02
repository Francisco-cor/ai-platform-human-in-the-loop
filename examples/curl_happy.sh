#!/bin/bash
# Example happy flow via curl — Fase 8 DX
# Requiere API en http://localhost:8000
set -e
BASE=${BASE_URL:-http://localhost:8000}
echo "== Health =="
curl -s $BASE/healthz | jq
echo "== Create execution =="
RESP=$(curl -s -X POST $BASE/v1/procurement/executions \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: curl_happy_$(date +%s)" \
  -d '{"tenant_id":"tenant_demo","requester_id":"user_01","items":[{"sku":"MAT-001","quantity":10,"unit":"piece"}]}')
echo $RESP | jq
EXEC_ID=$(echo $RESP | jq -r .execution_id)
APPR_ID=$(echo $RESP | jq -r .approval_request.approval_id)
echo "execution $EXEC_ID approval $APPR_ID"
echo "== Get execution =="
curl -s $BASE/v1/procurement/executions/$EXEC_ID | jq .status
echo "== List executions paginated =="
curl -s "$BASE/v1/procurement/executions?tenant=tenant_demo&limit=2" | jq
echo "== List events =="
curl -s "$BASE/v1/procurement/executions/$EXEC_ID/events?limit=5&format=trace" | jq '.count, .trace_id'
echo "== Get approval =="
curl -s $BASE/v1/approvals/$APPR_ID | jq
echo "== Approve =="
curl -s -X POST $BASE/v1/approvals/$APPR_ID/decision \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: approve_${APPR_ID}" \
  -d '{"decision":"approved","decided_by":"approver_01"}' | jq
echo "== Verify COMPLETED =="
curl -s $BASE/v1/procurement/executions/$EXEC_ID | jq .status
echo "== Webhook subscription =="
curl -s -X POST $BASE/v1/webhooks/subscriptions \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"tenant_demo","url":"http://webhook.site/test","secret":"secret123","events":["execution.completed"]}' | jq
echo "done"
