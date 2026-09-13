# LoopGraph — Ingestion Layer

Everything from the external apps up to the normalized **Event**.

```
Gmail ─────┐
Slack ─────┤
Drive ─────┼── Composio ──→ FastAPI ──→ Event Normalizer ──→ │ LangGraph
Calendar ──┘                                                 │ Compiler / Verifier / Replanner
                                                             │ Outcome Graph → Supabase → UI
                                            ─────────────────┘ (not this layer)
```

This layer observes and reports. It does not reason about goals, touch the
outcome graph, or decide whether evidence proves anything.

---

## Layout

Follows §14 of the Technical Stack Reference so it merges cleanly with the
other branches.

```
.env / .env.example
requirements.txt
backend/
  app/
    main.py                    FastAPI app, health, lifespan
    api/webhooks.py            ingestion endpoints
    events/
      normalizer.py            raw payload -> Event   (pure, no I/O)
      sink.py                  dedup, record, forward
      gmail_poller.py          Gmail fast path
    integrations/composio.py   the only module that talks to Composio
    graph/schemas.py           Event + EventType  (shared — see note below)
    data/                      events.jsonl, seen_ids.json  (gitignored)
scripts/connect_apps.py        one-time OAuth
```

> `graph/schemas.py` is a **shared** file. Only `Event` and `EventType` are
> defined here, contributed by this layer. `Loop`, `OutcomeNode`, `Edge`,
> `Evidence` and `Action` belong to whoever owns the runtime — add them to
> this same file rather than creating a second schemas module.

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in the three Composio values
python scripts/connect_apps.py
```

`COMPOSIO_API_KEY` must be a **project** key from `platform.composio.dev`.
Composio has three key types and all three fail with the same generic 401:

| Prefix | Header | Belongs to |
|---|---|---|
| `ak_` / project key | `x-api-key` | **the SDK — this is the one** |
| `ck_` | `x-consumer-api-key` | Connect / MCP |
| `uak_` | `x-user-api-key` | the `composio` CLI |

### Scopes

Set when the auth config is created. Wrong scopes fail *silently*.

| App | Scopes |
|---|---|
| Gmail | `gmail.readonly` + `gmail.send` — both; readonly can't send, send can't search |
| Calendar | `calendar` — full, we create/edit/delete |
| Drive | `drive` — **not** `drive.file`, which only sees files we created |
| Slack | `chat:write`, `channels:history`, `channels:read`, `files:read`, `users:read` |

After connecting Slack, `/invite @yourbot` into the demo channel. Installing
the app is not the same as being in the channel.

---

## Run

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

| Source | Path | Latency |
|---|---|---|
| Gmail | background poller | ~5s |
| Slack | `POST /webhooks/composio` | seconds |
| Drive, Calendar | `POST /webhooks/composio` | ~1 min |
| Demo / tests | `POST /events` | instant |

Gmail is polled rather than triggered because Composio's Gmail trigger is a
poller with a **minutes**-grained interval. No Gmail trigger is registered —
if one were, every email would arrive twice.

### Endpoints

| | |
|---|---|
| `GET /health` | liveness, event count, forward target |
| `GET /connections` | live connection status straight from Composio |
| `POST /webhooks/composio` | trigger receiver, signature-verified when `COMPOSIO_WEBHOOK_SECRET` is set |
| `POST /events` | manual injection — same normalizer, same dedup as a real webhook |
| `GET /events?limit=20` | recent Events, newest first |

---

## The Event contract

A Pydantic v2 model (`app/graph/schemas.py`). Unknown `event_type` values are
rejected rather than silently stored.

```json
{
  "id": "event_d5f97cc344f1",
  "source_app": "gmail",
  "event_type": "MESSAGE_RECEIVED",
  "external_id": "1a09bd5f2c55bc37",
  "timestamp": "2026-09-13T14:03:00Z",
  "actor": "returns@merchant.com",
  "subject": "Your refund has been processed",
  "content": "Your $129.00 refund for Order #A1298 has been processed.",
  "attachments": [],
  "metadata": { "thread_id": "...", "source": "poll" },
  "linked_loop_id": null,
  "processed": false
}
```

`timestamp` is the **app's own** timestamp, not when we noticed it — deadlines
depend on when the merchant sent the mail, not when we polled for it.

### Deduplication

Identity is `source_app + external_id` (`Event.dedup_key`). The same webhook
can be delivered twice and the poller sees the same message every pass — one
real-world event yields exactly one Event. Duplicates return
`{"accepted": false}`.

On cold start the existing inbox is baselined silently, so launching the
service does not replay old mail.

---

## Handing off

Pull:

```
GET /events?limit=20
```

Or push — set `LOOPGRAPH_WEBHOOK_URL` and every Event is POSTed onward as it
arrives. Once the LangGraph runtime exists in-process, replace the forward in
`events/sink.py` with a direct call.

---

## Known gaps

- **Slack / Drive / Calendar extractors are unverified.** Gmail's field names
  were confirmed against live data; the other three are best guesses and will
  likely have camelCase mismatches. On the first real event of each type,
  check `backend/app/data/events.jsonl` — any `null` is a name to fix in the
  matching `from_*` function in `events/normalizer.py`.
- **No public webhook URL registered yet.** Slack, Drive and Calendar events
  will not arrive until `POST /webhooks/composio` is reachable and set in the
  Composio project's webhook settings.
- **Two spec conflicts**, resolved in favour of the Data Models doc, which is
  the canonical contract:
  - the Tech Stack Reference §8 shows a 5-field event (`source`, `type`, …);
    the Data Models doc specifies the fuller `Event` used here
  - §6 names the column `loop_id`; the Data Models doc says `linked_loop_id`

---

## Gotchas that cost real time

- **Connections live in Composio**, not on this machine. OAuth happens once
  per user, ever; Composio refreshes tokens. Nothing to persist locally.
- **`tools.execute` needs `dangerously_skip_version_check=True`** or it errors
  with "Toolkit version not specified". `version="latest"` is not accepted.
- **Use `connected_accounts.link()`, not `.initiate()`** — the latter returns
  400 for Composio-managed OAuth configs.
- **Gmail payloads are camelCase**: `messageId`, `messageText`,
  `attachmentList`.
