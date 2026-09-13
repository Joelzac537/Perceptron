-- LoopGraph — Supabase / PostgreSQL schema
-- Source of truth: LoopGraph_03_Data_Models_API_Contracts.md (§25 tables, §4 enums, §26 idempotency)
-- NOTE: root_node_id is non-null with a deferred FK, so a loop CANNOT be inserted alone.
-- Loop + root node must be in ONE transaction (db.py persist_compiled_graph does this).
-- On local Postgres, the table owner / superuser bypasses RLS, so pgAdmin testing as
-- 'postgres' is unaffected.

create extension if not exists "pgcrypto";

create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- 1. loops ------------------------------------------------------------------
create table loops (
  id            text primary key,
  user_id       text not null,
  title         text not null,
  goal          text not null,
  status        text not null default 'ACTIVE'
                  check (status in ('ACTIVE','WAITING','BLOCKED','COMPLETED','FAILED','CANCELLED')),
  root_node_id  text not null,      -- composite FK added after outcome_nodes exists (deferred)
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  completed_at  timestamptz
);
create trigger loops_set_updated_at before update on loops
  for each row execute function set_updated_at();

-- 2. outcome_nodes ----------------------------------------------------------
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
  metadata          jsonb not null default '{}'
                      check (jsonb_typeof(metadata) = 'object'),
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  constraint uq_node_loop_id unique (loop_id, id)
);
create trigger outcome_nodes_set_updated_at before update on outcome_nodes
  for each row execute function set_updated_at();

create index idx_nodes_loop on outcome_nodes(loop_id);
create index idx_nodes_due on outcome_nodes(deadline)
  where status in ('ACTIVE','WAITING');

-- #1 root must be a node IN THE SAME LOOP; deferred so loop+root insert in one transaction.
alter table loops
  add constraint fk_loop_root_same_loop
  foreign key (id, root_node_id)
  references outcome_nodes(loop_id, id)
  deferrable initially deferred;

-- 3. edges (both endpoints must be in this loop) ----------------------------
create table edges (
  id             text primary key,
  loop_id        text not null references loops(id) on delete cascade,
  source_node_id text not null,
  target_node_id text not null,
  relationship   text not null
                   check (relationship in ('DEPENDS_ON','PROVES','BLOCKS',
                                           'TRIGGERS','SUPERSEDES','CONTRADICTS')),
  reason         text,
  created_at     timestamptz not null default now(),
  constraint uq_edge_semantic unique (loop_id, source_node_id, target_node_id, relationship),
  constraint no_self_edge check (source_node_id <> target_node_id),
  constraint fk_edge_source_same_loop
    foreign key (loop_id, source_node_id) references outcome_nodes(loop_id, id) on delete cascade,
  constraint fk_edge_target_same_loop
    foreign key (loop_id, target_node_id) references outcome_nodes(loop_id, id) on delete cascade
);
create index idx_edges_loop on edges(loop_id);

-- 4. events -----------------------------------------------------------------
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
  attachments    jsonb not null default '[]'
                   check (jsonb_typeof(attachments) = 'array'),
  metadata       jsonb not null default '{}'
                   check (jsonb_typeof(metadata) = 'object'),
  linked_loop_id text references loops(id) on delete set null,   -- routing hint only
  processed      boolean not null default false,
  created_at     timestamptz not null default now()
);
-- Dedup: duplicate webhook can't create a second event ROW. Does NOT serialize two workers
-- processing the same row -- the worker still needs replay / concurrency protection.
create unique index uq_event_external on events(source_app, external_id)
  where external_id is not null;
create index idx_events_unprocessed on events(processed) where processed = false;
create index idx_events_loop on events(linked_loop_id);

-- #3 durable source-event provenance (derive Loop.source_event_ids from this on load) -----
create table loop_source_events (
  loop_id  text not null references loops(id) on delete cascade,
  event_id text not null references events(id) on delete cascade,
  primary key (loop_id, event_id)
);

-- 5. evidence_requirements --------------------------------------------------
create table evidence_requirements (
  id              text primary key,
  node_id         text not null references outcome_nodes(id) on delete cascade,
  type            text not null,
  description     text not null,
  source_apps     text[] not null
                    check (cardinality(source_apps) >= 1
                           and array_position(source_apps, null::text) is null),
  required_fields jsonb not null default '{}'
                    check (jsonb_typeof(required_fields) = 'object'),
  must_all_match  boolean not null default true,
  created_at      timestamptz not null default now()
);
create index idx_reqs_node on evidence_requirements(node_id);

-- 6. evidence ---------------------------------------------------------------
create table evidence (
  id               text primary key,
  node_id          text not null references outcome_nodes(id) on delete cascade,
  event_id         text not null references events(id) on delete cascade,
  relationship     text not null
                     check (relationship in ('PROVES','CONTRADICTS','SUPERSEDES',
                                             'PARTIALLY_SUPPORTS','INSUFFICIENT','UNRELATED')),
  confidence       real not null check (confidence >= 0.0 and confidence <= 1.0),
  reason           text not null,
  extracted_fields jsonb not null default '{}'
                     check (jsonb_typeof(extracted_fields) = 'object'),
  verified         boolean not null default false,
  created_at       timestamptz not null default now()
);
create index idx_evidence_node on evidence(node_id);

-- 7. actions (same-loop node; #5 risk policy enforced) ----------------------
create table actions (
  id                  text not null,
  loop_id             text not null references loops(id) on delete cascade,
  node_id             text,
  app                 text not null,
  action_type         text not null,
  parameters          jsonb not null default '{}'
                        check (jsonb_typeof(parameters) = 'object'),
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
  primary key (id),
  -- Stops a duplicate action ROW (not a second execution of one row -- executor still
  -- needs atomic claiming + replay checks).
  constraint uq_action_idempotency unique (idempotency_key),
  constraint uq_action_loop_id unique (loop_id, id),
  constraint fk_action_node_same_loop
    foreign key (loop_id, node_id) references outcome_nodes(loop_id, id) on delete cascade,
  -- #5a: any MEDIUM-risk action must require approval.
  constraint chk_medium_requires_approval
    check (risk_level <> 'MEDIUM' or requires_approval = true),
  -- #5b: comms sends + external Calendar update/cancel must be MEDIUM + require approval.
  constraint chk_comms_risk
    check (action_type not in
             ('SEND_EMAIL','SEND_SLACK_MESSAGE','UPDATE_CALENDAR_EVENT','CANCEL_CALENDAR_EVENT')
           or (risk_level = 'MEDIUM' and requires_approval = true))
);
create trigger actions_set_updated_at before update on actions
  for each row execute function set_updated_at();
create index idx_actions_loop on actions(loop_id);
create index idx_actions_status on actions(status);

-- 8. approvals (must approve an action in the same loop) ---------------------
create table approvals (
  id           text primary key,
  action_id    text not null,
  loop_id      text not null references loops(id) on delete cascade,
  status       text not null default 'PENDING'
                 check (status in ('PENDING','APPROVED','REJECTED','EXPIRED')),
  requested_at timestamptz not null default now(),
  resolved_at  timestamptz,
  user_comment text,
  constraint fk_approval_action_same_loop
    foreign key (loop_id, action_id) references actions(loop_id, id) on delete cascade
);
create index idx_approvals_status on approvals(status);
create index idx_approvals_action on approvals(action_id);

-- 9. activity_logs ----------------------------------------------------------
create table activity_logs (
  id            text primary key,
  loop_id       text not null references loops(id) on delete cascade,
  activity_type text not null,
  message       text not null,
  metadata      jsonb not null default '{}'
                  check (jsonb_typeof(metadata) = 'object'),
  created_at    timestamptz not null default now()
);
create index idx_activity_loop on activity_logs(loop_id, created_at);

-- compilations: persist CompiledGraph.assumptions / clarification across restart ---------
-- (kept OUT of the strict Loop DTO; select separately. A3 must not lose a pending
--  clarification or execute guessed actions after reload.)
create table compilations (
  loop_id              text primary key references loops(id) on delete cascade,
  assumptions          jsonb not null default '[]'
                         check (jsonb_typeof(assumptions) = 'array'),
  clarification_needed boolean not null default false,
  clarification_question text,
  created_at           timestamptz not null default now()
);

-- Realtime (Supabase only; skipped automatically on local Postgres) ---------
-- evidence_requirements included per review so an edited requirement pushes to the UI.
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table
      loops, outcome_nodes, edges, evidence, evidence_requirements,
      actions, approvals, activity_logs;
  end if;
end
$$;

-- RLS: read-only for the frontend, writes stay in the backend --------------
-- Every business table: RLS on + a SELECT policy only. No INSERT/UPDATE/DELETE policies,
-- so anon/authenticated (Realtime) clients can read but never write business state. The
-- backend uses the service-role key, which bypasses RLS. `using (true)` is demo-grade;
-- swap for owner-scoped policies once the auth-id convention is decided.
do $$
declare t text;
begin
  foreach t in array array[
    'loops','outcome_nodes','edges','events','loop_source_events',
    'evidence_requirements','evidence','actions','approvals',
    'activity_logs','compilations'
  ]
  loop
    execute format('alter table %I enable row level security;', t);
    execute format('create policy %I on %I for select using (true);', t || '_read', t);
  end loop;
end
$$;
