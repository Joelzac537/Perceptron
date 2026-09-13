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
    api/events.py              POST /events, GET /events
    events/
      normalizer.py            raw payload -> Event   (pure, no I/O)
      classify.py              created / updated / deleted, per item
      poller.py                all four apps at ~10s
      sink.py                  dedup, record, forward
    integrations/composio.py   the only module that talks to Composio
    graph/schemas.py           Event + EventType  (shared — see note below)
    data/                      events.jsonl, seen_ids.json, logs  (gitignored)
run-server.sh                  start the service (logs to terminal + file)
watch-events.sh                follow events live in another terminal
scripts/
  connect_apps.py              one-time OAuth
  check_apps.py                reads live data from all four, proves the auth
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
./run-server.sh          # not `uvicorn` — see Gotchas
```

That is the whole thing. No tunnel, no second process, nothing that needs a
public URL.

### Where events show up

`run-server.sh` prints to your terminal **and** appends to
`backend/app/data/server.log`, so events are still readable if you started it
in the background. One line per event:

```
[slack] MESSAGE_RECEIVED <Mahesh> :: can you send the deck by friday
[googledrive] DOCUMENT_DELETED :: (deleted file)
[googlecalendar] CALENDAR_EVENT_CREATED <me@gmail.com> :: design review
```

To watch them live in a second terminal:

```bash
./watch-events.sh            # events only — the one to project during a demo
./watch-events.sh --all      # plus uvicorn request lines
```

Other views of the same data:

```bash
curl -s localhost:8000/events?limit=20  # the API teammates consume
cat backend/app/data/events.jsonl       # durable history
```

### How each app gets here

All four are polled at ~10s. No Composio triggers are registered and there is
no inbound webhook, so nothing depends on this machine being reachable.

| Source | Read via |
|---|---|
| Gmail | `GMAIL_FETCH_EMAILS` ×3 — INBOX, SENT, TRASH |
| Slack | `SLACK_SEARCH_MESSAGES` — every channel **and DMs** |
| Drive | `LIST_FILES` ×2 + `LIST_CHANGES` |
| Calendar | `GOOGLECALENDAR_EVENTS_LIST` |
| Demo / tests | `POST /events` |

### What gets detected

Create, update and delete for all four:

| | created | updated | deleted |
|---|---|---|---|
| **Gmail** | `MESSAGE_RECEIVED` (inbox)<br>`MESSAGE_SENT` (sent) | — | `MESSAGE_DELETED` (bin) |
| **Slack** | `MESSAGE_RECEIVED` | `MESSAGE_UPDATED` | `MESSAGE_DELETED` |
| **Drive** | `DOCUMENT_CREATED` | `DOCUMENT_UPDATED` | `DOCUMENT_DELETED` (bin **and** permanent) |
| **Calendar** | `CALENDAR_EVENT_CREATED` | `CALENDAR_EVENT_UPDATED` | `CALENDAR_EVENT_DELETED` |

How each app expresses a change, because none of them agree:

- **Gmail** has no notion of editing a message; the whole lifecycle is which
  label it carries, so `classify.gmail` reads `labelIds` and TRASH beats SENT.
- **Slack** search results carry *no* `edited` marker, and a deleted message is
  simply absent. Both are derived by diffing consecutive polls
  (`_slack_previous`). A message is only called deleted if it vanished from
  *within* the window still visible — results are capped, so an old message
  dropping off the bottom is not a deletion.
- **Drive** needs three reads; see the list above.
- **Calendar** marks deletions `status: cancelled` and only returns them when
  the query passes `showDeleted`.

Events carry a Drive file's **name, link and category — not its contents**.
That is enough to say "a receipt was uploaded", not enough to say "the receipt
is for $129". Content extraction is written and verified but commented out in
`integrations/composio.py` (`drive_file_text`); uncommenting it and the call
in `fetch_recent_drive` is the whole change, since the normalizer already
prefers a `text` key when one is present. It costs one Composio call plus one
HTTP fetch per file per poll, and PDFs/images would additionally need a parser
that is deliberately not a dependency.

Video and audio are skipped (`DRIVE_IGNORED_MEDIA`). A loop is closed by a
receipt, an invoice or a signed PDF; a phone's photo sync would otherwise
flood the stream and push real evidence out of the dedup store. Everything
kept gets a `metadata.category` — `pdf`, `image`, `document`, `spreadsheet`,
`presentation`, `folder`, `text`, `other` — so downstream never has to match
on `application/vnd.google-apps.*` itself.

Composio *does* offer triggers, and Slack's is a genuine webhook that would
arrive in about a second rather than ten. They were tried and removed:

- Gmail/Drive/Calendar triggers are pollers on *Composio's* servers with a
  one-minute floor — slower than doing it here, and they produced a second,
  later copy of every event.
- Slack's real webhook needs a publicly reachable URL, which means running a
  tunnel next to the server. A tunnel that drops takes Slack offline with no
  error anywhere. Polling has no such dependency.

Trade-off, stated plainly: Slack events take ~10s instead of ~1s. That buys
one process instead of three and nothing to re-register when a free tunnel
domain rotates.

### Endpoints

| | |
|---|---|
| `GET /health` | liveness, event count, forward target |
| `GET /connections` | live connection status straight from Composio |
| `POST /events` | manual injection — same normalizer, same dedup as a poll |
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

Identity is `source_app + external_id + version` (`Event.dedup_key`). Every
poll re-reads the same recent items, so without this each message would be
re-emitted every 10 seconds. Duplicates return `{"accepted": false}`.

The `version` component is what makes *changes* visible, and it is more than a
timestamp:

| App | version |
|---|---|
| Gmail | `inbox` / `sent` / `trash` — the box it sits in |
| Slack | `live` / `edited` / `deleted` + a digest of the text |
| Calendar | `updated` |
| Drive | `modifiedTime` + `\|live` / `\|trashed` / `\|removed` |

Drive needs the state suffix because moving a file to the bin does **not**
reliably change its `modifiedTime` — on a timestamp alone the deletion hashes
identically to the file's own creation and gets dropped as a duplicate. Slack
needs the text digest for the same reason: `ts` never changes when a message
is edited. Gmail needs the box, or deleting a mail we already reported looks
like a duplicate of its arrival.

Changing how a version is built re-keys every event, so the next start will
replay. Clear `backend/app/data/` when you do.

On cold start everything that already exists is baselined silently, so
launching the service does not replay old mail, files or messages.

---

## Handing off

Pull:

```
GET /events?limit=20
```

Or push — set `LOOPGRAPH_WEBHOOK_URL` and every Event is POSTed onward as it
arrives. That is **outbound**, so it needs no tunnel. Once the LangGraph
runtime exists in-process, replace the forward in `events/sink.py` with a
direct call.

---

## Known gaps

- **`EventType.DOCUMENT_DELETED` is an addition to the spec.** Doc 03 §4.4
  lists `CALENDAR_EVENT_DELETED` but no document equivalent. Drive reports
  deletions explicitly and a loop can hinge on one, so the value was added to
  `graph/schemas.py`. Anyone matching exhaustively on `EventType` should know.
- **`LOOPGRAPH_WEBHOOK_URL` is unset**, so Events are not forwarded anywhere
  yet — consumers must pull `GET /events`.
- **Drive deletions from before the first run are invisible.** The change feed
  starts at "now" and Google expires old page tokens, so history cannot be
  replayed. Deleting the saved `data/drive_page_token` restarts from now.
- **Slack `after:` has day granularity**, so each poll re-reads the last two
  days and leans on dedup. Fine at this volume; a busy workspace would want
  `oldest`-based paging instead.
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
  `attachmentList`. Drive's link field is `display_url`, not `webViewLink`.
- **`run-server.sh`, not bare `uvicorn`.** On this machine `python` resolves
  to conda and `python3` to homebrew, neither of which has composio — hence
  `ModuleNotFoundError`. The script uses the venv interpreter by absolute path.
- **A failed Composio call still returns HTTP 200**, with the real error inside
  `data.http_error`. Unchecked, a 429 looks exactly like "no new items", so
  `integrations/composio.execute()` raises on it.
- **Calendar `orderBy=updated` sorts ASCENDING**, so `maxResults=5` returns the
  five *oldest*-updated events and a new one never appears. Use `updatedMin`.
  Deletions need `showDeleted: True` or they are invisible.
- **Drive's change feed returns only `id`/`kind`/`mimeType`/`name` per file**,
  and offers no way to ask for more. `trashed` and the timestamps have to come
  from `LIST_FILES`, which does take a `fields` mask. Without that mask
  `createdTime`/`modifiedTime` come back empty and every file looks newly
  created forever.
- **"Delete" in the Drive UI means move to bin**, which leaves the file listed
  with `trashed: true`. Only a permanent delete appears in the change feed as
  `removed: true`. Both are needed to catch every deletion.
- **Slack `conversations.history` allows roughly ONE call per minute** for
  apps like Composio's. `search.messages` sits in a far higher tier, covers
  every channel in one call, and picks up DMs too — which is why the Slack
  poller uses it.
- **An exception in a poller task is swallowed by asyncio**, since nothing
  awaits those tasks. The app just goes quiet, which reads as "nothing is
  happening" rather than "this app is dead". `poller.start()` attaches a
  done-callback that prints when a watcher stops.
