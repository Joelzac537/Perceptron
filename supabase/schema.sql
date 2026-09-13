-- LoopGraph — Supabase / PostgreSQL schema
-- Enums are TEXT + CHECK (easy to edit mid-hackathon). All values UPPERCASE.
-- IDs are app-supplied TEXT. ON DELETE CASCADE flows down from loops (demo reset = one delete).

create extension if not exists "pgcrypto";

-- Reusable updated_at trigger
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- 1. loops
create table loops (
  id            text primary key,
  user_id       text not null,
  title         text not null,
  goal          text not null,
  status        text not null default 'ACTIVE'
                  check (status in ('ACTIVE','WAITING','BLOCKED','COMPLETED','FAILED','CANCELLED')),
  root_node_id  text,               -- forward ref to outcome_nodes.id; deliberately not FK'd
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  completed_at  timestamptz
);
create trigger loops_set_updated_at before update on loops
  for each row execute function set_updated_at();

-- 2. outcome_nodes
create table outcome_nodes (
  id                text primary key,
  loop_id           text not null references loops(id) on delete cascade,
  title             text not null,
  description       text,
  status            text not null default 'PENDING'
                      check (status in ('PENDING','ACTIVE','WAITING','BLOCKED',
                                        'VERIFIED','FAILED','CANCELLED','SUPERSEDED')),
  owner             text,
  deadline          timestamptz,
  recovery_strategy text,
  metadata          jsonb not null default '{}',
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create trigger outcome_nodes_set_updated_at before update on outcome_nodes
  for each row execute function set_updated_at();

create index idx_nodes_loop on outcome_nodes(loop_id);
create index idx_nodes_due on outcome_nodes(deadline)
  where status in ('ACTIVE','WAITING');

-- 3. edges (authoritative graph topology)
create table edges (
  id             text primary key,
  loop_id        text not null references loops(id) on delete cascade,
  source_node_id text not null references outcome_nodes(id) on delete cascade,
  target_node_id text not null references outcome_nodes(id) on delete cascade,
  relationship   text not null
                   check (relationship in ('DEPENDS_ON','PROVES','BLOCKS',
                                           'TRIGGERS','SUPERSEDES','CONTRADICTS')),
  reason         text,
  created_at     timestamptz not null default now(),
  constraint uq_edge_semantic unique (loop_id, source_node_id, target_node_id, relationship),
  constraint no_self_edge check (source_node_id <> target_node_id)
);
create index idx_edges_loop on edges(loop_id);

-- 4. events
create table events (
  id             text primary key,
  source_app     text not null,
  event_type     text not null
                   check (event_type in (
                     'MESSAGE_RECEIVED','MESSAGE_SENT','DOCUMENT_CREATED','DOCUMENT_UPDATED',
                     'DOCUMENT_FOUND','CALENDAR_EVENT_CREATED','CALENDAR_EVENT_UPDATED',
                     'CALENDAR_EVENT_DELETED','DEADLINE_REACHED','ACTION_COMPLETED',
                     'ACTION_FAILED','USER_APPROVED','USER_REJECTED','USER_INPUT','SYSTEM_EVENT')),
  external_id    text,
  timestamp      timestamptz not null,
  actor          text,
  subject        text,
  content        text,
  attachments    jsonb not null default '[]',
  metadata       jsonb not null default '{}',
  linked_loop_id text references loops(id) on delete set null,
  processed      boolean not null default false,
  created_at     timestamptz not null default now()
);
-- Dedup keystone: duplicate webhook can't create a second event.
create unique index uq_event_external on events(source_app, external_id)
  where external_id is not null;
create index idx_events_unprocessed on events(processed) where processed = false;
create index idx_events_loop on events(linked_loop_id);

-- 5. evidence_requirements
create table evidence_requirements (
  id              text primary key,
  node_id         text not null references outcome_nodes(id) on delete cascade,
  type            text not null,
  description     text not null,
  source_apps     text[] not null default '{}',
  required_fields jsonb not null default '{}',
  must_all_match  boolean not null default true,
  created_at      timestamptz not null default now()
);
create index idx_reqs_node on evidence_requirements(node_id);

-- 6. evidence
create table evidence (
  id               text primary key,
  node_id          text not null references outcome_nodes(id) on delete cascade,
  event_id         text not null references events(id) on delete cascade,
  relationship     text not null
                     check (relationship in ('PROVES','CONTRADICTS','SUPERSEDES',
                                             'PARTIALLY_SUPPORTS','INSUFFICIENT','UNRELATED')),
  confidence       real not null check (confidence >= 0.0 and confidence <= 1.0),
  reason           text not null,
  extracted_fields jsonb not null default '{}',
  verified         boolean not null default false,
  created_at       timestamptz not null default now()
);
create index idx_evidence_node on evidence(node_id);

-- 7. actions
create table actions (
  id                  text primary key,
  loop_id             text not null references loops(id) on delete cascade,
  node_id             text references outcome_nodes(id) on delete cascade,
  app                 text not null,
  action_type         text not null,
  parameters          jsonb not null default '{}',
  risk_level          text not null check (risk_level in ('LOW','MEDIUM','HIGH')),
  requires_approval   boolean not null default false,
  status              text not null default 'PROPOSED'
                        check (status in ('PROPOSED','AWAITING_APPROVAL','APPROVED',
                                          'EXECUTING','EXECUTED','VERIFIED','FAILED','CANCELLED')),
  idempotency_key     text not null,
  external_id         text,
  verification_method text,
  error               text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  -- Idempotency keystone: same key can never execute twice.
  constraint uq_action_idempotency unique (idempotency_key)
);
create trigger actions_set_updated_at before update on actions
  for each row execute function set_updated_at();
create index idx_actions_loop on actions(loop_id);
create index idx_actions_status on actions(status);

-- 8. approvals
create table approvals (
  id           text primary key,
  action_id    text not null references actions(id) on delete cascade,
  loop_id      text not null references loops(id) on delete cascade,
  status       text not null default 'PENDING'
                 check (status in ('PENDING','APPROVED','REJECTED','EXPIRED')),
  requested_at timestamptz not null default now(),
  resolved_at  timestamptz,
  user_comment text
);
create index idx_approvals_status on approvals(status);
create index idx_approvals_action on approvals(action_id);

-- 9. activity_logs
create table activity_logs (
  id            text primary key,
  loop_id       text not null references loops(id) on delete cascade,
  activity_type text not null,
  message       text not null,
  metadata      jsonb not null default '{}',
  created_at    timestamptz not null default now()
);
create index idx_activity_loop on activity_logs(loop_id, created_at);

-- Realtime (Supabase only; skipped automatically on local Postgres)
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table
      loops, outcome_nodes, edges, evidence, actions, approvals, activity_logs;
  end if;
end
$$;
