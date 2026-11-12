# Keicy AI — Implementation Plan (5 parts)

Source: `Project Document` (12 phases). This plan groups those 12 phases into 5 parts, each of which can be built, tested and shipped on its own, in order.

---

## 1. Where we are today (gap analysis)

| Area | Already in the scaffold | Missing vs the Project Document |
|---|---|---|
| Repo | `backend/`, `frontend/`, README, `.gitignore`, `.env.example`, compose file | Git repo, `seed/`, `docs/`, top-level `tests/`; `/api/v1` prefix (current routes are `/api/...`) |
| Backend | FastAPI, Uvicorn, SQLAlchemy async, asyncpg, Pydantic Settings, HTTPX, Anthropic SDK, health, CORS | Alembic, OpenAI SDK, Ollama SDK, WebSockets |
| Database | 4 tables (vendors, purchase_requests, agent_runs, payments) via `create_all` | ~17 tables; UUID keys; **money is `Float` — must become `NUMERIC`**; JSONB; `updated_at`; indexes; migrations |
| AI | Claude → Ollama fallback router, JSON parsing | Provider interface, Pydantic-validated output, timeouts, bounded retries, error classification, usage/latency records |
| Agents | Linear 4-step pipeline (requirements, sourcing, policy, recommendation) | Tool registry + permissions, 17-state workflow machine, persisted/resumable state, **6 agents** (Intent, Research, Evaluation, Procurement, Finance, Reconciliation) |
| Catalogue | 5 seeded vendors, no products | Products, multiple offers per product, prices, stock, lead times, expiry |
| PayPal | OAuth token cache, create order, capture order, `react-paypal-js` buttons | Idempotency keys, duplicate-capture guard, capture verification, webhooks + signature check, **JS SDK v6** |
| Frontend | Single page: request form, run agents, approve, pay | Router, sidebar/header layout, 7 screens, component library, live updates |
| Security / QA | 2 smoke tests (fake LLM, fake PayPal) | Auth, ownership checks, structured logs, full test matrix |

**Environment findings**
- PostgreSQL is running locally and the `paypal` database exists.
- `backend/.env` has `DB_USER=postgre`, which fails authentication. `DB_USER=postgres` with the same password connects. Fix this before Part 1.
- Ollama works with `qwen3.5:9b`, but one agent call took about 68 s. Use a smaller or non-thinking model for the fallback, or expect slow demo runs when the cloud LLM is off.

**Decisions to carry through every part**
1. Money is `NUMERIC(14,2)` in the database and `Decimal` in Python. LLMs never compute totals.
2. Every status change goes through the workflow state machine. Nothing writes `status` directly.
3. Financial truth comes only from PayPal API responses and the database, never from model output.
4. Until real auth lands in Part 5, a `get_current_user()` dependency returns a seeded demo user, so ownership checks can be written from Part 1 onward.
5. The existing scaffold is reused where it fits. Old tables are replaced by the Alembic baseline; nothing in the dev DB needs keeping.

---

## Part 1 — Foundation, data model & seed catalogue ✅ Done (2026-11-12)
**Covers:** Phase 1, Phase 2, Phase 7.1

> **Delivered:** 17 tables via Alembic, 13 products and 29 offers from 5 vendors (including expired and out-of-stock offers), `/api/v1` routes, 29 tests passing on Postgres, and the frontend shell with Overview, AI Workspace, Vendor Intelligence and placeholders for the other screens.
> Deviations: `seed/` and `tests/` live under `backend/` because they import the app. The old 4-agent pipeline (`app/agents/`), LLM router and PayPal client are still in the code but not wired up; Parts 2–4 replace or reuse them.

**Goal:** the final database schema, migrated with Alembic and seeded with a realistic catalogue, plus the app shell on both sides.

**Backend**
- `git init`; add `seed/`, `docs/`, `tests/`; move routes under `/api/v1`.
- Add deps: `alembic`, `openai`, `ollama`, `websockets`.
- Remove `create_all` from startup; configure Alembic (async env), initial migration, `alembic upgrade head`.
- Models, all with UUID keys, `created_at`/`updated_at` and NUMERIC money:
  - Core: `users`, `vendors`, `products`, `vendor_offers`, `procurement_requests`, `purchase_orders`, `purchase_order_items`
  - AI/workflow: `agent_runs`, `agent_tool_logs`, `workflow_events`, `workflow_state_history`, `model_executions`
  - Finance: `approvals`, `payments`, `paypal_orders`, `paypal_captures`, `audit_logs`
- Constraints: FKs with explicit `ON DELETE`, unique PO numbers, unique PayPal order and capture IDs, unique idempotency keys, indexes on status, FKs and `created_at`.
- Seed script (`seed/seed.py`, idempotent): demo user, 4+ vendors, 10–15 products, 2–4 offers each with different specs, prices, stock, lead times and expiry dates.

**Frontend**
- Add React Router, Lucide icons and a chart library (Recharts).
- Layout shell: sidebar, header, responsive content area, empty route stubs for Dashboard, AI Workspace, Vendors, Purchase Orders, Approvals, Payments, Audit.
- API client moved to `/api/v1`.

**Done when**
- `alembic upgrade head` builds the schema on a fresh DB; `alembic downgrade base` cleans it.
- Seed runs twice without duplicates.
- Records survive a backend restart.
- Relationship and constraint tests pass (cascade/restrict, unique, NOT NULL, NUMERIC precision).
- Frontend shell loads, every nav link routes, and the header shows live `/api/v1/health`.

---

## Part 2 — Hybrid AI gateway & agent engine ✅ Done (2026-11-12)
**Covers:** Phase 3, Phase 4, Phase 6

> **Delivered:**
> - Gateway with Anthropic, OpenAI and Ollama adapters, retry/repair/fallback, and a `model_executions` record per call.
> - 17-state machine with an approval guard, a block on direct status writes, row locking and restart recovery.
> - Tool registry with 20 tools (14 implemented, 6 stubs for Parts 3–4), per-agent allow-lists, idempotent writes and full call logging.
> - `BaseAgent` and `AgentRunner`; one migration (tool-call statuses); 116 tests passing, plus a `--live` Ollama test (~17 s).
>
> Notes:
> - Phase 6 lists 20 tools, not the 24 this plan originally said.
> - `create_purchase_order` was built now rather than stubbed, since totals and idempotency were already in place.
> - The Anthropic SDK (v2.8) uses its own `httpx2` HTTP package. This only matters for test mocks.

**Goal:** a provider-agnostic LLM gateway and an agent runtime with tools, permissions and a persisted state machine. No business agents yet.

**AI gateway** (`app/ai/`)
- `LLMProvider` interface → `AnthropicProvider`, `OpenAIProvider`, `OllamaProvider`; primary chosen in `.env`.
- `gateway.generate(schema=PydanticModel, ...)`: structured output, validation, one repair retry on invalid JSON, request timeout, bounded retries.
- Error classes (timeout, rate limit, auth, invalid output, unavailable) decide retry vs fallback.
- Every call writes a `model_executions` row: provider, model, latency, tokens, fallback used, outcome.

**Agent engine** (`app/agents/core/`)
- `BaseAgent`, `AgentContext`, `AgentResult`; per-agent timeout and max tool calls.
- `ToolRegistry`: Pydantic arg/result schemas, `read`/`write` flag, per-agent allow-list, duplicate-write guard (idempotency key), duration logging into `agent_tool_logs`.
- Register the 24 tools from Phase 6 with signatures. Read-only catalogue tools get implemented here; write and payment tools can stay stubs until Parts 3–4.

**Workflow state machine** (`app/workflow/`)
- The 17 states and an explicit allowed-transition table.
- `transition()` is the only writer: it validates, writes `workflow_state_history`, `workflow_events` and `audit_logs` in one DB transaction.
- Hard rule: no `PAYMENT_*` state is reachable without an `APPROVED` record.
- Resume: on startup, workflows stuck in an active state are resumed or marked `FAILED`.

**Done when**
- Tests: cloud success, cloud down → Ollama, invalid JSON → repair → fail cleanly, timeout, retry limit hit.
- Tests: every invalid transition is rejected; payment before approval is rejected; transitions survive a restart.
- An agent calling a tool outside its allow-list is blocked and logged.

---

## Part 3 — Six agents & the procurement workflow ✅ Done (2026-11-12)
**Covers:** Phase 5.1–5.5, Phase 7.2, Phase 9.1–9.4, 9.6, 9.7

> **Delivered:**
> - Intent, Research, Evaluation, Procurement and Finance agents, run as a background pipeline that resumes after a restart.
> - All Phase 9.1–9.4 and 9.6 APIs, plus edit and cancel.
> - WebSocket with replay from a cursor (new `workflow_events.seq` column).
> - A live request page in the AI Workspace with demo approve/reject.
> - 147 tests passing.
> - Live check on Ollama (`qwen3.5:9b`): "10 laptops, 16GB RAM, under $15,000 by Dec 1" became PO-…: 10 × LAP-PRO-14 from Fabrikam at $11,890.00, awaiting approval, in about 40 s, then approved from the UI.
>
> **Design choices:**
> - Procurement and Finance use no LLM; all money logic is Python.
> - One PO means one vendor, so a multi-item request needs a vendor that sells every item.
> - Budget is a ranking factor in Evaluation and a hard rule in Finance.
> - Clarification uses `FAILED` plus questions (no extra state), then edit and re-run.
> - Until Part 5, `X-Demo-User` (and `?user=` on the WebSocket) selects a seeded user.
>
> **Moved to Part 5:** the full Purchase Orders and Approval Center screens.

**Goal:** natural-language request → validated purchase order awaiting approval, observable live.

**Agents** (each has its own system prompt, output schema and tool allow-list)
- **Intent**: product, quantity, specs, budget, currency, deadline; returns clarification questions when the request is incomplete.
- **Research**: only real catalogue records via tools, with source IDs on every offer; rejects anything not in the DB.
- **Evaluation**: compares unit price, total, specs, stock and lead time. Scores are computed in Python; the LLM writes the explanation.
- **Procurement**: validates the selection, generates a unique PO number, Python-computed line totals, subtotal, tax and total; saves PO and items.
- **Finance**: checks budget, currency, total and status; decides if approval is needed; prepares the payment request or blocks it.

**Workflow:** `CREATED → ANALYZING → RESEARCHING → EVALUATING → ORDER_DRAFTED → FINANCE_VALIDATING → AWAITING_APPROVAL`, with `REJECTED`/`FAILED` exits. It runs as a background task, not inside the HTTP request.

**APIs (`/api/v1`)**
- procurement: `POST /requests`, `GET /requests/{id}`, `GET /requests/{id}/status`, `POST /requests/{id}/execute`, `GET /requests/{id}/offers`
- agents: `GET /agents/runs/{id}`, `GET /agents/runs/{id}/logs`
- catalogue: `GET /vendors`, `GET /products`
- POs: `GET /purchase-orders/{id}`, `POST /purchase-orders/{id}/approve`, `POST /purchase-orders/{id}/reject`
- `GET /dashboard/summary`
- WebSocket `/ws/workflows/{id}`: events are persisted first, then broadcast; on reconnect the client gets the missed events from the DB.

**Done when**
- A request like "10 laptops, 16GB RAM, under $15,000 by Dec 1" produces a saved PO in `AWAITING_APPROVAL`, with real offer IDs and a correct total.
- The agent test cases in Phase 12.2 pass: missing details, invalid quantity, no budget, no match, no vendor, conflicting offers, finance failure, timeout, fallback, tool failure.
- WebSocket shows each `agent.*` event live; a page refresh restores the state.

---

## Part 4 — PayPal payments & reconciliation ✅ Done (2026-11-12): verified with a real sandbox checkout

> **Live sandbox result:**
> - Attempt 1 (`24H73067MT286335K`): the buyer window closed inside the in-app browser, so it was correctly recorded as a buyer cancel with nothing charged.
> - Attempt 2 (`8JG00081TY825460S`): approved by the sandbox buyer and captured as `4XE70473LG098642X`, $11,890.00 USD. Our stored capture matches PayPal's live record.
> - The Reconciliation Agent marked PO-20261112-7EC151 PAID, issued receipt RCPT-20261112-7EC151, and the request reached COMPLETED.
> - Tip: PayPal's popup needs a regular browser; the in-app preview pane can't open it.
**Covers:** Phase 8, Phase 9.5, Phase 5.6, payment items of Phase 11

> **Delivered:**
> - Payment service (create-order, capture, settle, cancel, webhooks, timeout resolution, restart recovery).
> - Hardened PayPal client: Decimal amounts, PayPal-Request-Id, timeouts as "unknown", raw-body webhook verification.
> - Reconciliation Agent (PO → PAID + receipt).
> - The 5 payment tools implemented; the 2 money-moving tools refuse agents.
> - One migration (`webhook_events`; payment attempt, failure reason, receipt, reconciled_at).
> - PayPal JS SDK v6 checkout and receipt in the request page.
> - 174 tests passing against a simulated PayPal.
>
> **Live sandbox check:** OAuth works, and the v6 SDK loads and reports PayPal eligible. A real sandbox order, `24H73067MT286335K` ($11,890.00), was created from the UI. The buyer-approval step needs a sandbox buyer login, so the user does it; capture and reconciliation have so far been verified only against the simulator.
>
> **Notes:**
> - v6 uses the client ID directly; no server-issued token is needed for PayPal-only checkout.
> - Both `PAYMENT.CAPTURE.DENIED` and `.DECLINED` are handled, because PayPal's docs list both.

**Goal:** a Sandbox buyer pays an approved PO and the backend verifies, reconciles and records it.

**Setup (manual, you):** PayPal Developer → Sandbox app (Client ID and Secret into `.env`), sandbox buyer and merchant accounts, webhook URL (use a tunnel such as ngrok or cloudflared for local dev), webhook ID into `.env`.

**Backend**
- `POST /payments/create-order`: loads the PO, **recalculates the total on the server**, requires `APPROVED` and ownership, sends a `PayPal-Request-Id` idempotency key, persists the PayPal order and moves to `PAYMENT_PENDING`.
- `POST /payments/capture`: checks approval and state, guards against duplicate capture (row lock + unique capture ID), verifies status `COMPLETED`, amount and currency against the PO, persists the capture ID, moves to `PAYMENT_COMPLETED` or `PAYMENT_FAILED`.
- Capture timeout: re-reads the order from PayPal instead of guessing.
- `POST /payments/webhook`: signature verification via PayPal's verify endpoint, stores the event ID (unique) so duplicates are ignored, handles async capture updates.
- `GET /payments/{id}`.
- **Reconciliation Agent**: compares the payment record with PayPal's capture data, flags discrepancies, records the transaction reference, generates the receipt and report, then `RECONCILING → COMPLETED`.

**Frontend:** replace `react-paypal-js` with **PayPal JS SDK v6**. Checkout view shows the approved PO, amount and currency, and handles success, failure and cancel states.

**Done when**
- A real Sandbox checkout completes end to end and the DB holds the order ID, capture ID, verified amount and receipt.
- Tests: wrong amount, wrong currency, unapproved PO, duplicate create-order, duplicate capture, capture timeout, bad webhook signature, duplicate webhook, buyer cancel.

---

## Part 5 — Full UI, security & QA ✅ Done (2026-11-12)
**Covers:** Phase 10, Phase 11, Phase 12

> **Delivered:**
> - **UI:** Login (with demo-account quick sign-in), role-aware navigation and user menu, toasts.
> - **Screens:** Purchase Orders (list + detail), Approval Center (pending queue with AI recommendation and budget check, approve/reject with reason, history), Payment Center (all attempts, PayPal references, receipts), Intelligence & Audit (agent performance chart, run traces with model and tool calls, model usage incl. p95 latency and tokens, tool stats, filterable audit trail), workflow history on the request page, price comparison in Vendor Intelligence.
> - **Security:** scrypt passwords, JWT sessions (WebSocket via `?token=`), login throttling and auditing, role and ownership checks (404 for others), append-only audit/history enforced by a DB trigger, JSON logs with request ids, safe 500/503 responses, `JWT_SECRET` required in production.
> - **Demo modes:** `LLM_PROVIDERS=mock` (rule-based) and `PAYPAL_MODE=mock` (in-process simulator), both refused in production.
> - **QA:** 207 backend tests; a 5-test Playwright suite (full journey with refresh/persistence, clarification loop, rejection path, signed-out access, phone layout on a Pixel 7) on its own database.
>
> **Notes:**
> - The real PayPal sandbox checkout was verified manually in Part 4. In E2E the PayPal simulator stands in, because the buyer step needs a human sign-in at PayPal.
> - The X-Demo-User header from Part 3 was removed.

**Goal:** a judge can run the whole journey from the UI, and failures can never look like success.

**UI (light enterprise theme):** design tokens and components (buttons, forms, tables, badges, toasts, loading/empty/error states), then the screens:
- **Dashboard**: KPIs, recent activity, recent POs
- **AI Workspace**: prompt input, extracted requirements, live agent timeline with tool calls, model provider and errors, final recommendation
- **Vendor Intelligence**: vendors, offers, price/spec/delivery comparison, scores and explanation
- **Purchase Orders**: list, detail, items, subtotal/tax/total, status
- **Approval Center**: pending list, AI recommendation, budget check, approve/reject with reason, history
- **Payment Center**: checkout, history, PayPal references, receipt
- **Intelligence & Audit**: agent runs, model usage, tool logs, audit trail, workflow history

**Security:** JWT login (seeded users with `requester`, `approver` and `admin` roles) replacing the demo-user dependency; protected routes; role checks on approve, create-order and capture; ownership checks; payload validation; structured JSON logs with request IDs; audit history is append-only.

**QA:** finish the Phase 12 matrix (API, schemas, migrations, recovery); a Playwright end-to-end run of the full journey including refresh/persistence; desktop and small-screen checks.

**Done when:** one complete request → research → PO → approval → Sandbox payment → reconciliation → receipt journey works from the UI alone, and unauthorized users or failed services cannot produce a "paid" state.

---

## Order and dependencies

```
Part 1 ──► Part 2 ──► Part 3 ──► Part 4 ──► Part 5
schema      engine      agents      payments    UI + auth + QA
```

Each part depends on the one before it. Within a part, backend and frontend tasks can run in parallel. Part 5's UI screens can start once Part 3's APIs exist.
