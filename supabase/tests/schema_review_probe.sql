-- Diagnostic for the original schema.sql, run ONLY in a disposable review DB.
-- This reports which invalid states the current schema accepts. It is not a
-- migration or a passing-regression-test specification. All probe rows roll back.
\set ON_ERROR_STOP on
begin;

insert into loops(id, user_id, title, goal, root_node_id) values
  ('review_l1','review_user','Loop one','Goal one','review_n2'),
  ('review_l2','review_user','Loop two','Goal two',null),
  ('review_l3','review_user','Loop three','Goal three','review_missing');
insert into outcome_nodes(id, loop_id, title) values
  ('review_n1','review_l1','Node one'),
  ('review_n2','review_l2','Node two');
insert into edges(id, loop_id, source_node_id, target_node_id, relationship) values
  ('review_e1','review_l1','review_n1','review_n2','DEPENDS_ON');
insert into actions(id, loop_id, node_id, app, action_type, risk_level,
                    requires_approval, status, idempotency_key) values
  ('review_a1','review_l1','review_n2','gmail','SEND_EMAIL','MEDIUM',false,
   'PROPOSED','review_key');
insert into approvals(id, action_id, loop_id) values
  ('review_approval','review_a1','review_l2');
insert into evidence_requirements(id, node_id, type, description) values
  ('review_req','review_n1','EMAIL_CONFIRMATION','Some proof');
insert into events(id, source_app, event_type, timestamp, external_id, attachments) values
  ('review_event','gmail','MESSAGE_RECEIVED',now(),'review_external','{}');

select 'dangling root' as invalid_state, count(*) as accepted
from loops where id='review_l3' and root_node_id='review_missing'
union all
select 'null root', count(*) from loops where id='review_l2' and root_node_id is null
union all
select 'root from another loop', count(*) from loops l join outcome_nodes n
  on n.id=l.root_node_id where l.id='review_l1' and n.loop_id<>l.id
union all
select 'cross-loop edge', count(*) from edges e join outcome_nodes n
  on n.id=e.target_node_id where e.id='review_e1' and e.loop_id<>n.loop_id
union all
select 'cross-loop action', count(*) from actions a join outcome_nodes n
  on n.id=a.node_id where a.id='review_a1' and a.loop_id<>n.loop_id
union all
select 'cross-loop approval', count(*) from approvals p join actions a
  on a.id=p.action_id where p.id='review_approval' and p.loop_id<>a.loop_id
union all
select 'empty evidence sources', count(*) from evidence_requirements
  where id='review_req' and cardinality(source_apps)=0
union all
select 'object instead of attachments array', count(*) from events
  where id='review_event' and jsonb_typeof(attachments)='object'
union all
select 'medium-risk send without approval flag', count(*) from actions
  where id='review_a1' and risk_level='MEDIUM' and not requires_approval;

do $$
begin
  begin
    insert into events(id, source_app, event_type, timestamp, external_id)
    values ('review_duplicate','gmail','MESSAGE_RECEIVED',now(),'review_external');
    raise exception 'Duplicate event unexpectedly accepted';
  exception when unique_violation then
    raise notice 'PASS: duplicate event rejected';
  end;
  begin
    insert into actions(id, loop_id, app, action_type, risk_level, idempotency_key)
    values ('review_duplicate_action','review_l1','gmail','SEARCH_GMAIL','LOW','review_key');
    raise exception 'Duplicate action key unexpectedly accepted';
  exception when unique_violation then
    raise notice 'PASS: duplicate action key rejected';
  end;
  begin
    insert into events(id, source_app, event_type, timestamp)
    values ('review_bad_enum','gmail','DEADLINE_MISSED',now());
    raise exception 'Invalid enum unexpectedly accepted';
  exception when check_violation then
    raise notice 'PASS: invalid event enum rejected';
  end;
end;
$$;

select tablename, rowsecurity from pg_tables where schemaname='public'
  order by tablename;
rollback;
