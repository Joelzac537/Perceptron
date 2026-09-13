-- PROPOSAL FOR TEAMMATE C, not an applied/shared migration.
-- Tested only against a disposable database initialized from schema.sql.
-- Existing invalid rows must be repaired before these constraints can be added.
-- Root integrity requires graph insertion in ONE database transaction/RPC.
begin;

alter table outcome_nodes add constraint uq_nodes_loop_id unique (loop_id, id);
alter table actions add constraint uq_actions_loop_id unique (loop_id, id);

alter table loops alter column root_node_id set not null;
alter table loops add constraint fk_loop_root_same_loop
  foreign key (id, root_node_id) references outcome_nodes(loop_id, id)
  deferrable initially deferred;

alter table edges drop constraint edges_source_node_id_fkey;
alter table edges drop constraint edges_target_node_id_fkey;
alter table edges add constraint fk_edge_source_same_loop
  foreign key (loop_id, source_node_id) references outcome_nodes(loop_id, id)
  on delete cascade;
alter table edges add constraint fk_edge_target_same_loop
  foreign key (loop_id, target_node_id) references outcome_nodes(loop_id, id)
  on delete cascade;

alter table actions drop constraint actions_node_id_fkey;
alter table actions add constraint fk_action_node_same_loop
  foreign key (loop_id, node_id) references outcome_nodes(loop_id, id)
  on delete cascade;
alter table approvals drop constraint approvals_action_id_fkey;
alter table approvals add constraint fk_approval_action_same_loop
  foreign key (loop_id, action_id) references actions(loop_id, id)
  on delete cascade;

-- Source provenance is distinct from events.linked_loop_id (routing hint).
create table loop_source_events (
  loop_id text not null references loops(id) on delete cascade,
  event_id text not null references events(id) on delete restrict,
  primary key (loop_id, event_id)
);

alter table evidence_requirements add constraint ck_requirement_sources
  check (cardinality(source_apps) > 0 and array_position(source_apps, null) is null);
alter table evidence_requirements alter column source_apps drop default;

alter table outcome_nodes add constraint ck_node_metadata_object
  check (jsonb_typeof(metadata) = 'object');
alter table events add constraint ck_event_metadata_object
  check (jsonb_typeof(metadata) = 'object');
alter table events add constraint ck_event_attachments_array
  check (jsonb_typeof(attachments) = 'array');
alter table evidence_requirements add constraint ck_required_fields_object
  check (jsonb_typeof(required_fields) = 'object');
alter table evidence add constraint ck_extracted_fields_object
  check (jsonb_typeof(extracted_fields) = 'object');
alter table actions add constraint ck_action_parameters_object
  check (jsonb_typeof(parameters) = 'object');
alter table activity_logs add constraint ck_activity_metadata_object
  check (jsonb_typeof(metadata) = 'object');

-- Static proposal flags only: runtime must ALSO enforce real approval and state.
alter table actions add constraint ck_medium_requires_approval
  check (risk_level <> 'MEDIUM' or requires_approval);
alter table actions add constraint ck_communication_risk
  check (action_type not in ('SEND_EMAIL','SEND_SLACK_MESSAGE',
                            'UPDATE_CALENDAR_EVENT','CANCEL_CALENDAR_EVENT')
         or (risk_level = 'MEDIUM' and requires_approval));

-- Evidence-panel changes should wake the UI even when node status is unchanged.
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime')
     and not exists (
       select 1 from pg_publication_tables
       where pubname = 'supabase_realtime'
         and schemaname = 'public' and tablename = 'evidence_requirements'
     ) then
    alter publication supabase_realtime add table public.evidence_requirements;
  end if;
end;
$$;

commit;

-- Intentionally omitted: RLS/grants depend on C/D's auth choice; assumptions and
-- clarification persistence depend on the draft/intake storage design. Both are
-- called out in the review and must be resolved before their integration steps.
