# Procura AI — Autonomous Procurement Intelligence

| Layer | Tech |
|---|---|
| Frontend | React 19 + Vite 7 + TypeScript + Tailwind CSS 4, React Router, Lucide, Recharts (`frontend/`) |
| Backend | Python 3.12 + FastAPI, API under `/api/v1` (`backend/`) |
| AI | Cloud LLM (Anthropic / OpenAI) with Ollama fallback |
| Orchestration | Custom Python agent engine |
| Database | PostgreSQL · SQLAlchemy 2 async + asyncpg · Alembic migrations |
| Payments | PayPal REST API (Orders v2), Sandbox |

Build plan: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) · **All 5 parts complete:** foundation; AI gateway and agent engine; agents and live workflow; PayPal payments and reconciliation; full UI, security and QA.

## Try it

1. Start backend and frontend (see Setup) and open http://localhost:5173. Sign in with a demo account button on the login page. The seeded accounts are a **requester**, an **approver** and an **admin**; their password is in `backend/seed/data.py`.
2. As the requester, open **AI Workspace**.
3. Type e.g. `10 laptops, 16GB RAM, under $15,000 by Dec 1` → **Submit & run agents**.
4. Watch the five agents work live:
   - **Intent** extracts the requirements.
   - **Research** finds catalogue products and quotes.
   - **Evaluation** scores vendors.
   - **Procurement** drafts the PO.
   - **Finance** validates it.

   This takes about 40 s on local Ollama.
5. Sign out and sign in as the **approver**. In the **Approval Center**, review the AI recommendation and budget check, then **Approve** or **Reject** (a rejection needs a reason).
6. Sign in as the requester again, open the request and click **Pay with PayPal**.
   - Use a regular browser; PayPal's popup can't open in embedded preview panes.
   - In PayPal, log in with a **sandbox personal (buyer)** account (Developer Dashboard → Testing Tools → Sandbox Accounts) or a sandbox test card.
7. The backend captures the payment and verifies it with PayPal. The **Reconciliation** agent then marks the PO PAID, and a receipt appears.
8. Look around:
   - **Payment Center**: every attempt, PayPal references, receipts.
   - **Purchase Orders**: all orders and their status.
   - **Intelligence & Audit** (approver/admin): agent performance, model usage, tool calls and the append-only audit trail.

On the sandbox, *Sandbox test → simulate card declined* exercises the failure path.

### No GPU or PayPal account? Demo modes
Set `LLM_PROVIDERS=mock` and/or `PAYPAL_MODE=mock` in `backend/.env`:
- **mock LLM:** a rule-based stand-in for the model.
- **mock PayPal:** an in-process simulator of orders, buyer approval and capture; the button reads "(simulated)".

Everything else runs the real code: agents, tools, scoring, state machine, approvals, payment checks, reconciliation. Both modes are refused when `APP_ENV=production`.

## Design

The interface is modelled on purchasing paperwork:
- **Type:** Public Sans, the US government's public-procurement typeface, with tabular figures so money lines up.
- **Colour:** ledger-ink text and sidebar, ballpoint-blue actions, cool paper background, carbon-copy canary for anything waiting on a person, stamp green and stamp red for decisions.
- **Status badges:** stamp-style, with plain-language labels.
- **Routing slip:** every request page shows one, with eight stations from *Understand* to *Reconciled*.

Tokens live in `frontend/src/index.css`, shared components in `frontend/src/components/ui.tsx`.

| Width | Navigation | Layout |
|---|---|---|
| phone (< 768 px) | drawer behind a menu button | single column; less important table columns fold into the first cell |
| tablet (768–1023 px) | icon rail | single column, full width |
| desktop (≥ 1024 px) | full sidebar | 12-column grids; list + detail side by side |
| wide (≥ 1536 px) | full sidebar | extra columns, e.g. two catalogue panels per row, five vendors per row |

The E2E suite checks every screen at 412, 768 and 1920 px for sideways scrolling and working navigation.

## Layout

```
backend/
  app/
    api/routes/      health, catalog (vendors/products), procurement (requests)
    models/          17 tables: core.py · workflow.py · finance.py · enums.py
    db/session.py    async engine, Base with constraint naming convention
    ai/              LLM gateway: providers.py (Anthropic, OpenAI, Ollama) · gateway.py · errors.py
    agents/          core/ (BaseAgent, runner, tool registry) · permissions.py · schemas.py
    tools/           20 agent tools: catalog · procurement · finance · payments
    workflow/        17-state machine (states.py, machine.py) · restart recovery
    payments/        paypal.py (REST client) · service.py (create, capture, settle, cancel, webhooks, recovery) · mock.py
    core/            config · security.py (passwords, JWT) · observability.py (JSON logs, request ids, error handling)
  migrations/        Alembic (async)
  seed/              idempotent demo catalogue:  uv run python -m seed
  tests/             runs against a real Postgres test DB (<DB_NAME>_test)
frontend/src/
  components/        Layout (sidebar + header), ui.tsx (cards, badges, buttons, states)
  pages/             Login, Overview, AI Workspace (+ live request page), Vendor Intelligence, Purchase Orders,
                     Approval Center, Payment Center, Intelligence & Audit
frontend/e2e/        Playwright journey + responsive tests
docs/                implementation plan
```

## Setup

### 1. PostgreSQL
Any local Postgres works. Put the connection details in `backend/.env`, one per line:
```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=paypal
DB_USER=postgres
DB_PASSWORD=...
```
Or start one with Docker (matches `.env.example`): `docker compose up -d postgres`.

### 2. Backend
```bash
cd backend
cp .env.example .env              # then edit DB_* and keys
uv sync
uv run alembic upgrade head       # create / update the schema
uv run python -m seed             # demo users, vendors, products, offers (safe to re-run)
uv run uvicorn app.main:app --reload --port 8000
```
API docs: http://localhost:8000/docs · status: http://localhost:8000/api/v1/health

> On an exFAT/external drive, `export UV_LINK_MODE=copy` avoids `uv sync` failures on macOS `._*` files.

### 3. Frontend
```bash
cd frontend
npm install
npm run dev
```
Open http://localhost:5173. Vite proxies `/api` to the backend on port 8000.

### 4. LLMs
`LLM_PROVIDERS=anthropic,ollama` sets the routing order. A cloud provider without an API key is skipped, so with no keys everything runs on Ollama.
Set `OLLAMA_MODEL` to any model from `ollama list`. Keep `OLLAMA_THINK=false` for reasoning models (qwen3.x): it cut a structured call from about 68 s to about 17 s here.

### 5. PayPal Sandbox
1. https://developer.paypal.com/dashboard/applications/sandbox → **Create App** → put the **Client ID** and the **Secret** (two different values) into `backend/.env`.
2. Testing Tools → Sandbox Accounts: use the **personal** account to pay in checkout.
3. *Optional, for webhooks:* expose the backend publicly (e.g. `cloudflared tunnel --url http://localhost:8000`). Add a webhook for `https://<tunnel>/api/v1/payments/webhook` with the events `PAYMENT.CAPTURE.*` and `CHECKOUT.ORDER.APPROVED`, then set `PAYPAL_WEBHOOK_ID`.
4. *Optional, for declines:* the sandbox negative-testing flag on your sandbox **business** account enables the "simulate card declined" option.

## Database

- UUID primary keys, `created_at`/`updated_at` on every table, money as `NUMERIC(14,2)`, JSONB for specs/payloads.
- Statuses are `VARCHAR` with `CHECK` constraints (no native PG enums, so migrations stay simple).
- Delete rules: purchase-order items cascade with their PO. Anything with business history (vendors with offers, requests with POs, payments, approvals) is `RESTRICT`. Audit logs survive user deletion (`SET NULL`).
- Schema changes: edit `app/models/*`, then
  ```bash
  uv run alembic revision --autogenerate -m "describe change"
  uv run alembic upgrade head
  ```

## AI gateway & agent engine (Part 2)

**Gateway** (`app/ai/gateway.py`): `generate(Schema, system=..., prompt=...)` returns a validated Pydantic object.
- Transient errors (timeout, 429, 5xx, connection) are retried on the same provider up to `LLM_MAX_RETRIES`, then the next provider is tried.
- Auth and bad-request errors skip straight to the next provider.
- Invalid JSON or a schema mismatch gets one repair turn, then falls back.
- Every call is written to `model_executions` (provider, model, latency, tokens, attempt, fallback, error type).

**Workflow** (`app/workflow/`): `transition(db, request_id, STATE, actor=...)` is the only way to change a request's status.
- It locks the row and checks the transition table.
- `APPROVED` and every payment state require a recorded APPROVED decision; the latest decision wins.
- It writes history, an event and an audit entry in one transaction.
- Assigning `request.status` directly raises `DirectStatusWriteError`.
- On startup, requests stuck in an agent state are resumed (via a registered resumer) or moved to `FAILED`.

**Agents and tools** (`app/agents/`, `app/tools/`): an agent subclasses `BaseAgent` and works through `ctx.tool(...)` and `ctx.generate(...)`. `AgentRunner` persists an `AgentRun`, enforces the timeout, and emits `agent.started`, `agent.tool_called`, `agent.completed` and `agent.failed` events. The tool registry enforces:
- per-agent allow-lists (`permissions.py`) and a tool-call limit
- argument and result validation
- an idempotency key on every write tool, so a repeat returns the stored result instead of acting twice
- logging of every call to `agent_tool_logs`

No agent may call `prepare_paypal_order` or `capture_approved_payment`. Money moves only through the payment API after a human approves.

## The procurement pipeline (Part 3)

| State | Agent | Uses the LLM for | Decided in Python |
|---|---|---|---|
| ANALYZING | Intent | reading the request (items, specs, budget, deadline) | missing/invalid fields → clarification questions; form fields override the text |
| RESEARCHING | Research | judging which *catalogue* products meet the specs | catalogue search, quotes; SKUs the model invents are discarded |
| EVALUATING | Evaluation | the written explanation only | scoring: price 45%, speed 25%, rating 15%, budget fit 15%; deadline is a hard rule |
| EVALUATING → ORDER_DRAFTED | Procurement | — | stock re-check, PO number, totals, idempotent PO creation |
| FINANCE_VALIDATING → AWAITING_APPROVAL | Finance | — | budget, currency, arithmetic checks; blocks (PO cancelled) or sends for approval |

Each agent ends with one of three outcomes:
- **continue**: move to the next state.
- **reject**: move to `REJECTED`, with the reason.
- **clarify**: move to `FAILED` with questions. Edit the request (`PATCH`) and re-run it.

If an agent crashes or times out, the request moves to `FAILED`. Pipelines run in the background, and on restart they resume from the state they were in, skipping agents that already finished.

**Approvals:** only `approver`/`admin` users may approve or reject, and never their own request.

**Live updates:** `ws://…/api/v1/ws/workflows/{id}?after=<seq>` sends a status snapshot, then every stored event after `seq`, then new events as they are committed. A refresh or reconnect never misses anything.

## Payments (Part 4)

```
APPROVED ─create-order→ PAYMENT_PENDING ─buyer approves at PayPal, capture→ PAYMENT_PROCESSING
   → PAYMENT_COMPLETED (verified capture) → RECONCILING (agent) → COMPLETED + receipt
   ↘ PAYMENT_FAILED (declined / amount or currency mismatch) → pay again → PAYMENT_PENDING
```

| Guarantee | How |
|---|---|
| The browser can't choose the amount | `create-order` takes only `purchase_order_id` (extra fields → 422). The amount is re-added from the PO lines on the server. |
| Human approval first | the PO must be APPROVED with a recorded approval; the state machine also refuses every PAYMENT_* state without one |
| Only the requester pays | ownership check (404 for others) |
| No double orders | an open PayPal order is reused; each attempt has its own `PayPal-Request-Id` |
| No double capture | Payment row lock + `PayPal-Request-Id: capture-<order>` + UNIQUE capture id; a repeat returns the first result |
| Paid means verified | settled only from PayPal's capture data, with status COMPLETED and amount and currency equal to the PO. Anything else → PAYMENT_FAILED + `payment.discrepancy` audit |
| Timeouts aren't failures | the outcome is unknown, so the order is re-read from PayPal; if still unknown → 202 PROCESSING, resolved on restart, by a webhook, or by retrying capture |
| Webhooks | each delivery is verified with PayPal's `verify-webhook-signature` (raw body, unmodified) and stored, and each event id is processed once. Money facts are re-read from the Orders API, not the webhook body. |
| Buyer cancel | payment CANCELLED, nothing charged, request stays payable |
| Reconciliation | the agent compares PayPal's live capture with our records, then marks the PO PAID (only this agent, only after verification) and writes the receipt. A mismatch → FAILED for review. |

## Security (Part 5)

| Area | What's in place |
|---|---|
| Sign-in | email + password, hashed with scrypt (salted, constant-time compare). The response is identical for an unknown email and a wrong password. Sign-ins are throttled per email and IP (5 failures / 5 min), and every attempt is audited. |
| Sessions | HS256 JWT (`Authorization: Bearer`, 8 h). `JWT_SECRET` is required in production; the WebSocket takes `?token=`. Disabled users are locked out immediately. |
| Authorization | every endpoint except health, login, demo accounts and the PayPal webhook needs a session. Roles: requester / approver / admin. Owners only for editing, running, cancelling and paying; others get 404, so ids can't be probed. Approvers can't approve their own requests. Audit and model usage are approver/admin only. |
| Money | see the Payments section: server-side amounts, recorded human approval, verified captures, idempotency. Agents can't move money. |
| Audit | `audit_logs` and `workflow_state_history` are append-only: a database trigger rejects UPDATE/DELETE. |
| Errors and logs | structured JSON logs; every response has an `X-Request-ID`, which also appears on every log line. 500s return a generic message plus the request id, never stack traces. Database outages return 503. |
| Secrets | only in `backend/.env` (git-ignored); the PayPal secret never reaches the browser. |
| Inputs | Pydantic validation on every payload (unknown fields rejected where they matter, e.g. `amount` on create-order); model output is validated before use. |

## Tests
```bash
cd backend && uv run pytest              # 207 tests, no network needed (PayPal and LLMs are simulated)
cd backend && uv run pytest --live       # also calls your local Ollama
cd frontend && npm run test:e2e          # Playwright: full journey in a real browser + phone layout (~20 s)
```
The E2E run creates its own `paypal_e2e` database and starts the backend in demo modes on :8100 and the frontend on :5174. It then drives the UI: sign in → request → five agents → refresh (state restored) → approver approves → PayPal checkout → reconciliation → receipt → Payment Center, Purchase Orders, Overview and Audit. It also covers the clarification and rejection paths and checks that every screen fits a phone. First-time setup: `npx playwright install chromium`.
The suite drops and recreates `<DB_NAME>_test`, runs migrations up → down → up, and seeds it. It then tests:
- schema, constraints and relationships
- the API
- the LLM gateway (fake providers) and the real SDK adapters (mocked HTTP)
- the state machine
- tools and agents

Your dev database is never touched.

## API (v1)
| Method | Path | |
|---|---|---|
| GET | `/api/v1/health` | DB, schema version, LLM and PayPal status (public) |
| POST | `/api/v1/auth/login` | email + password → access token (public) |
| GET | `/api/v1/auth/me`, `/api/v1/auth/demo-accounts` | current user; seeded demo accounts (dev only) |
| GET | `/api/v1/vendors`, `/api/v1/vendors/{id}` | vendors (detail includes offers) |
| GET | `/api/v1/products?category=&q=` | products with offers, cheapest first |
| POST, GET | `/api/v1/procurement/requests` | create / list your requests |
| GET, PATCH | `/api/v1/procurement/requests/{id}` | one request; edit while CREATED/FAILED |
| POST | `/api/v1/procurement/requests/{id}/execute` | start or re-run the agents (202, runs in background) |
| POST | `/api/v1/procurement/requests/{id}/cancel` | cancel |
| GET | `/api/v1/procurement/requests/{id}/status` | state, allowed next states, clarification questions, agent runs |
| GET | `/api/v1/procurement/requests/{id}/offers` | requirements, quotes, ranking, explanation, PO, finance report |
| GET | `/api/v1/procurement/requests/{id}/runs` | agent runs |
| GET | `/api/v1/agents/runs/{id}`, `/logs` | one agent run; its tool calls and model calls |
| GET | `/api/v1/purchase-orders`, `/{id}` | purchase orders (with items and approvals) |
| POST | `/api/v1/purchase-orders/{id}/approve`, `/reject` | human decision (approver/admin; reject needs a reason) |
| GET | `/api/v1/dashboard/summary` | KPIs, recent activity, recent POs |
| GET | `/api/v1/payments/config` | public PayPal SDK config (client id, SDK URL; never the secret) |
| POST | `/api/v1/payments/create-order` | `{purchase_order_id}` → `{orderId}` for the PayPal v6 SDK |
| POST | `/api/v1/payments/capture` | `{paypal_order_id}` after the buyer approves (202 while PayPal is still processing) |
| POST | `/api/v1/payments/cancel` | buyer closed or cancelled checkout |
| POST | `/api/v1/payments/webhook` | PayPal webhooks (signature-verified, de-duplicated) |
| GET | `/api/v1/payments?request_id=`, `/api/v1/payments/{id}` | payment records with PayPal order and capture ids and the receipt |
| GET | `/api/v1/procurement/requests/{id}/history` | state history with actors |
| GET | `/api/v1/approvals` | approval decisions |
| GET | `/api/v1/agents/runs?agent=&status=` | recent agent runs |
| GET | `/api/v1/intelligence/summary` | model, tool and agent performance (approver/admin) |
| GET | `/api/v1/audit/logs?action=&entity_type=` | audit trail (approver/admin) |
| WS | `/api/v1/ws/workflows/{id}?token=<jwt>&after=<seq>` | live workflow events |

All endpoints except health, login, demo-accounts and the PayPal webhook require `Authorization: Bearer <token>`.
