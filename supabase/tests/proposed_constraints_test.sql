-- Run after schema.sql + review/proposed_contract_constraints.sql in a disposable DB.
\set ON_ERROR_STOP on
begin;

-- Forward root references work when the whole graph is saved in one transaction.
insert into loops(id,user_id,title,goal,root_node_id) values
 ('test_l1','user','One','Goal one','test_n1'),
 ('test_l2','user','Two','Goal two','test_n2');
insert into outcome_nodes(id,loop_id,title) values
 ('test_n1','test_l1','One'), ('test_n2','test_l2','Two');
set constraints all immediate;

do $$
begin
  begin
    update loops set root_node_id='absent' where id='test_l1';
    raise exception 'Dangling root accepted';
  exception when foreign_key_violation then raise notice 'PASS: dangling root rejected'; end;
  begin
    update loops set root_node_id='test_n2' where id='test_l1';
    raise exception 'Cross-loop root accepted';
  exception when foreign_key_violation then raise notice 'PASS: cross-loop root rejected'; end;
  begin
    update loops set root_node_id=null where id='test_l1';
    raise exception 'Null root accepted';
  exception when not_null_violation then raise notice 'PASS: null root rejected'; end;
  begin
    insert into edges(id,loop_id,source_node_id,target_node_id,relationship)
    values ('test_e','test_l1','test_n1','test_n2','DEPENDS_ON');
    raise exception 'Cross-loop edge accepted';
  exception when foreign_key_violation then raise notice 'PASS: cross-loop edge rejected'; end;
  begin
    insert into actions(id,loop_id,node_id,app,action_type,risk_level,idempotency_key)
    values ('test_a_bad','test_l1','test_n2','gmail','SEARCH_GMAIL','LOW','test_bad');
    raise exception 'Cross-loop action accepted';
  exception when foreign_key_violation then raise notice 'PASS: cross-loop action rejected'; end;
  begin
    insert into evidence_requirements(id,node_id,type,description,source_apps)
    values ('test_req_bad','test_n1','CUSTOM','Proof','{}');
    raise exception 'Empty sources accepted';
  exception when check_violation then raise notice 'PASS: empty sources rejected'; end;
  begin
    insert into events(id,source_app,event_type,timestamp,attachments)
    values ('test_event_bad','gmail','MESSAGE_RECEIVED',now(),'{}');
    raise exception 'Invalid attachments accepted';
  exception when check_violation then raise notice 'PASS: invalid attachments rejected'; end;
  begin
    insert into actions(id,loop_id,app,action_type,risk_level,idempotency_key)
    values ('test_send_bad','test_l1','gmail','SEND_EMAIL','LOW','test_send');
    raise exception 'Unsafe send accepted';
  exception when check_violation then raise notice 'PASS: unsafe send rejected'; end;
end;
$$;

insert into actions(id,loop_id,node_id,app,action_type,risk_level,requires_approval,
                    status,idempotency_key)
values ('test_a','test_l1','test_n1','gmail','SEND_EMAIL','MEDIUM',true,
        'AWAITING_APPROVAL','test_action_key');
do $$
begin
  begin
    insert into approvals(id,loop_id,action_id) values ('test_p','test_l2','test_a');
    raise exception 'Cross-loop approval accepted';
  exception when foreign_key_violation then raise notice 'PASS: cross-loop approval rejected'; end;
end;
$$;

insert into approvals(id,loop_id,action_id) values ('test_p','test_l1','test_a');
insert into events(id,source_app,event_type,timestamp)
values ('test_source','gmail','MESSAGE_RECEIVED',now());
insert into loop_source_events(loop_id,event_id) values ('test_l1','test_source');
-- Root FK must permit graph deletion through the loop's normal cascades.
delete from loops where id in ('test_l1','test_l2');
do $$
begin
  if exists(select 1 from outcome_nodes where id in ('test_n1','test_n2'))
     or exists(select 1 from loop_source_events where loop_id='test_l1') then
    raise exception 'Cascade cleanup failed';
  end if;
  raise notice 'PASS: transactional graph insert, source linkage, and loop cascade';
end;
$$;
rollback;
