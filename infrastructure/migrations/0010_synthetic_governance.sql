CREATE OR REPLACE FUNCTION markos_control.authorize(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_capability text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$
DECLARE actor uuid; allowed text[];BEGIN
 actor:=markos_control.session_context(p_session,p_hash);
 SELECT roles INTO allowed FROM markos_control.memberships WHERE principal_id=actor AND owner_id=p_owner AND domain=p_domain FOR SHARE;
 IF NOT FOUND OR p_capability IS NULL OR NOT (CASE p_capability
 WHEN 'inspect' THEN allowed&&ARRAY['viewer','operator','approver','administrator']::text[]
 WHEN 'decide-approval' THEN 'approver'=ANY(allowed)
 WHEN 'chat' THEN 'operator'=ANY(allowed) WHEN 'configure' THEN 'administrator'=ANY(allowed)
 ELSE false END) THEN RAISE EXCEPTION 'control scope denied' USING ERRCODE='42501';END IF;
 RETURN actor;
END $$;
-- M1-F fixed synthetic governance. No role/account/topology provisioning or production tools.
CREATE TABLE markos_control.tool_grants(
 owner_id uuid NOT NULL,domain text NOT NULL,grant_id uuid NOT NULL,revision integer NOT NULL,
 document jsonb NOT NULL,enabled boolean NOT NULL,PRIMARY KEY(owner_id,domain)
);
CREATE TABLE markos_control.tool_grant_history(
 owner_id uuid NOT NULL,domain text NOT NULL,revision integer NOT NULL,document jsonb NOT NULL,
 PRIMARY KEY(owner_id,domain,revision)
);
CREATE TABLE markos_control.tool_plans(
 owner_id uuid NOT NULL,domain text NOT NULL,event_id uuid NOT NULL,arguments jsonb,arguments_sha256 text NOT NULL,
 state text NOT NULL CHECK(state IN ('queued','awaiting','ready','dispatched','succeeded','failed','unknown','cancelled')),
 intent_id uuid,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,event_id),FOREIGN KEY(owner_id,domain,event_id) REFERENCES markos_control.chat_runs
);
CREATE TABLE markos_control.tool_intents(
 intent_id uuid PRIMARY KEY,owner_id uuid NOT NULL,domain text NOT NULL,event_id uuid NOT NULL,
 task_id uuid NOT NULL,principal_id uuid NOT NULL,epoch uuid NOT NULL,task_fence bigint NOT NULL,resume_fence bigint,
 grant_id uuid NOT NULL,grant_revision integer NOT NULL,policy_revision integer NOT NULL,
 document jsonb,digest text NOT NULL CHECK(length(digest)=64),
 status text NOT NULL CHECK(status IN ('awaiting','approved','rejected','expired','revoked','consumed')),
 revision integer NOT NULL DEFAULT 1,approver_id uuid,dispatch_id uuid,
 expires_at timestamptz NOT NULL,terminal_at timestamptz,
 UNIQUE(owner_id,domain,event_id),FOREIGN KEY(owner_id,domain,event_id) REFERENCES markos_control.tool_plans
);
CREATE TABLE markos_control.tool_dispatches(
 dispatch_id uuid PRIMARY KEY,operation_id uuid NOT NULL UNIQUE,intent_id uuid NOT NULL UNIQUE REFERENCES markos_control.tool_intents,
 owner_id uuid NOT NULL,domain text NOT NULL,epoch uuid NOT NULL,task_id uuid NOT NULL,fence bigint NOT NULL,executor_id uuid NOT NULL,
 state text NOT NULL CHECK(state IN ('dispatched','succeeded','failed','unknown','resolved')),
 revision integer NOT NULL DEFAULT 1,started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 lease_until timestamptz NOT NULL,finished_at timestamptz,result jsonb,result_sha256 text,
 -- Reserved/unresolved capacity is held until a terminal resolution, even after lease expiry.
 reserved boolean NOT NULL DEFAULT true
);
CREATE TABLE markos_control.tool_audit(
 sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,audit_id uuid NOT NULL DEFAULT gen_random_uuid(),
 owner_id uuid NOT NULL,domain text NOT NULL,principal_id uuid NOT NULL,correlation_id uuid NOT NULL,
 intent_id uuid,dispatch_id uuid,intent_sha256 text,recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 outcome text NOT NULL,reason text NOT NULL,metadata jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE markos_control.tool_slots(slot_id integer PRIMARY KEY CHECK(slot_id BETWEEN 1 AND 2),dispatch_id uuid,lease_until timestamptz);
INSERT INTO markos_control.tool_slots(slot_id) VALUES(1),(2);
CREATE TABLE markos_control.tool_rates(owner_id uuid NOT NULL,domain text NOT NULL,tokens numeric NOT NULL,at timestamptz NOT NULL,PRIMARY KEY(owner_id,domain));
CREATE TABLE markos_control.tool_holds(intent_id uuid PRIMARY KEY REFERENCES markos_control.tool_intents,owner_id uuid NOT NULL,
 purpose text NOT NULL CHECK(purpose IN ('reconciliation','synthetic-review')),review_at timestamptz NOT NULL);
DO $$DECLARE n text;BEGIN FOREACH n IN ARRAY ARRAY['tool_grants','tool_grant_history','tool_plans','tool_intents','tool_dispatches','tool_audit','tool_slots','tool_rates','tool_holds'] LOOP
 EXECUTE format('ALTER TABLE markos_control.%I ENABLE ROW LEVEL SECURITY',n);
 EXECUTE format('REVOKE ALL ON markos_control.%I FROM PUBLIC,markos_control_gateway,markos_runtime_client',n);
END LOOP;END $$;
REVOKE ALL ON SEQUENCE markos_control.tool_audit_sequence_seq FROM PUBLIC,markos_control_gateway,markos_runtime_client;

-- Private canonical form for the closed integer/string/array/object fixture document.
CREATE FUNCTION markos_control.tool_canonical(d jsonb) RETURNS text LANGUAGE plpgsql IMMUTABLE
SET search_path=pg_catalog AS $$DECLARE answer text;BEGIN
 CASE jsonb_typeof(d)
 WHEN 'object' THEN SELECT '{'||coalesce(string_agg(to_jsonb(key)::text||':'||markos_control.tool_canonical(value),',' ORDER BY key COLLATE "C"),'')||'}' INTO answer FROM jsonb_each(d);
 WHEN 'array' THEN SELECT '['||coalesce(string_agg(markos_control.tool_canonical(value),',' ORDER BY ord),'')||']' INTO answer FROM jsonb_array_elements(d) WITH ORDINALITY AS a(value,ord);
 ELSE answer:=d::text;END CASE;RETURN answer;
END $$;
CREATE FUNCTION markos_control.tool_timestamp(p_time timestamptz) RETURNS text LANGUAGE sql IMMUTABLE
SET search_path=pg_catalog AS $$ SELECT to_char(p_time AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS') ||
 CASE WHEN to_char(p_time AT TIME ZONE 'UTC','US')='000000' THEN '' ELSE '.'||to_char(p_time AT TIME ZONE 'UTC','US') END || 'Z' $$;
ALTER TABLE markos_control.activity DROP CONSTRAINT activity_kind_check;
ALTER TABLE markos_control.activity ADD CHECK(kind IN ('queued','working','awaiting-decision','succeeded','failed','unknown'));
CREATE FUNCTION markos_control.tool_log(p_owner uuid,p_domain text,p_actor uuid,p_event uuid,p_intent uuid,p_dispatch uuid,p_outcome text,p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE i markos_control.tool_intents;
 r markos_control.chat_runs;g markos_control.tool_grants;d markos_control.tool_dispatches;ordinal bigint;kind text;BEGIN
 SELECT * INTO i FROM markos_control.tool_intents WHERE intent_id=p_intent AND owner_id=p_owner AND domain=p_domain;
 SELECT * INTO r FROM markos_control.chat_runs WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
 SELECT * INTO g FROM markos_control.tool_grants WHERE owner_id=p_owner AND domain=p_domain;
 SELECT * INTO d FROM markos_control.tool_dispatches WHERE dispatch_id=p_dispatch AND owner_id=p_owner AND domain=p_domain;
 INSERT INTO markos_control.tool_audit(owner_id,domain,principal_id,correlation_id,intent_id,dispatch_id,intent_sha256,outcome,reason,metadata)
 VALUES(p_owner,p_domain,p_actor,p_event,p_intent,p_dispatch,i.digest,p_outcome,p_reason,
 jsonb_build_object('event_id',p_event,'causation_event_id',r.event_id,'task_id',i.task_id,'run_id',i.event_id,
 'requester_id',i.principal_id,'epoch',i.epoch,'suspension_fence',i.task_fence,'execution_fence',d.fence,
 'grant_id',i.grant_id,'grant_revision',i.grant_revision,'policy_revision',i.policy_revision,
 'server_id','synthetic-mcp-fixture','tool','fixture.read','tool_schema_sha256',g.document->>'tool_schema_sha256',
 'arguments_sha256',(SELECT arguments_sha256 FROM markos_control.tool_plans WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event),
 'result_sha256',d.result_sha256,'operation_id',d.operation_id,'recipient','synthetic-mcp-fixture','data_class','synthetic','effect','read','attempt',CASE WHEN d.dispatch_id IS NULL THEN 0 ELSE 1 END));
 kind:=CASE p_outcome WHEN 'awaiting' THEN 'awaiting-decision' WHEN 'dispatched' THEN 'working'
 WHEN 'succeeded' THEN 'succeeded' WHEN 'unknown' THEN 'unknown' ELSE NULL END;
 IF kind IS NOT NULL AND r.event_id IS NOT NULL AND coalesce(p_dispatch,p_intent) IS NOT NULL THEN
  ordinal:=markos_control.next_scope_sequence(p_owner,p_domain);
  INSERT INTO markos_control.activity(owner_id,domain,sequence,conversation_id,object_id,correlation_id,kind)
  VALUES(p_owner,p_domain,ordinal,r.conversation_id,coalesce(p_dispatch,p_intent),p_event,kind);
 END IF;
END $$;
CREATE FUNCTION markos_control.save_tool_grant(p_session uuid,p_hash text,d jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE actor uuid;g markos_control.tool_grants;revoked markos_control.tool_intents;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,(d->>'owner_id')::uuid,d->>'domain','configure');
 SELECT * INTO g FROM markos_control.tool_grants WHERE owner_id=(d->>'owner_id')::uuid AND domain=d->>'domain' FOR UPDATE;
 IF coalesce(g.revision,0)<>(d->>'previous_revision')::integer OR (d->>'revision')::integer<>coalesce(g.revision,0)+1
 OR (g.grant_id IS NOT NULL AND g.grant_id<>(d->>'grant_id')::uuid) THEN RAISE EXCEPTION 'grant revision conflict' USING ERRCODE='40001';END IF;
 IF d->>'server_id'<>'synthetic-mcp-fixture' OR d->>'tool'<>'fixture.read' OR d->>'agent_id'<>'control-test-agent'
 OR d->>'protocol'<>'2026-07-28' OR d->>'tool_schema_sha256'<>'3d6fe221d21e0c10a08064edb93ed60724fa850e6da7d1dc7df7da5bb6738483'
 OR d->>'recipient'<>'synthetic-mcp-fixture' OR d->>'data_class'<>'synthetic' OR d->>'effect'<>'read'
 OR d->>'endpoint' !~ '^https://127[.]0[.]0[.]1:[0-9]{4,5}/mcp$'
 OR jsonb_typeof(d->'enabled')<>'boolean' OR jsonb_typeof(d->'require_approval')<>'boolean'
 THEN RAISE EXCEPTION 'closed fixture grant required' USING ERRCODE='42501';END IF;
 INSERT INTO markos_control.tool_grants VALUES((d->>'owner_id')::uuid,d->>'domain',(d->>'grant_id')::uuid,(d->>'revision')::integer,d,(d->>'enabled')::boolean)
 ON CONFLICT(owner_id,domain) DO UPDATE SET revision=excluded.revision,document=excluded.document,enabled=excluded.enabled;
 INSERT INTO markos_control.tool_grant_history VALUES((d->>'owner_id')::uuid,d->>'domain',(d->>'revision')::integer,d);
 UPDATE markos_control.tool_intents SET status='revoked',revision=revision+1,terminal_at=clock_timestamp()
 WHERE owner_id=(d->>'owner_id')::uuid AND domain=d->>'domain' AND status IN ('awaiting','approved');
 FOR revoked IN SELECT i.* FROM markos_control.tool_intents i JOIN markos_control.tool_plans p USING(owner_id,domain,event_id)
 WHERE i.owner_id=(d->>'owner_id')::uuid AND i.domain=d->>'domain' AND i.status='revoked' AND p.state IN ('awaiting','ready') LOOP
  PERFORM markos_control.end_chat(revoked.owner_id,revoked.domain,revoked.event_id,'failed','authority');
  PERFORM markos_control.tool_log(revoked.owner_id,revoked.domain,actor,revoked.event_id,revoked.intent_id,NULL,'revoked','grant-revision');
 END LOOP;
 PERFORM markos_control.tool_log((d->>'owner_id')::uuid,d->>'domain',actor,(d->>'grant_id')::uuid,NULL,NULL,'requested','grant-revision');
 RETURN d;
END $$;
CREATE FUNCTION markos_control.read_tool_grant(p_session uuid,p_hash text,p_owner uuid,p_domain text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 RETURN (SELECT document||jsonb_build_object('enabled',enabled) FROM markos_control.tool_grants WHERE owner_id=p_owner AND domain=p_domain);
END $$;
CREATE FUNCTION markos_control.admit_governed_message(p_session uuid,p_hash text,p_owner uuid,p_domain text,d jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE actor uuid;answer jsonb;g markos_control.tool_grants;old text;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 IF EXISTS(SELECT 1 FROM markos_control.tool_tombstones WHERE owner_id=p_owner AND domain=p_domain AND principal_id=actor AND request_key_hash=encode(sha256(convert_to(d->>'operation_key','UTF8')),'hex')) THEN RETURN jsonb_build_object('denied','retired');END IF;
 SELECT * INTO g FROM markos_control.tool_grants WHERE owner_id=p_owner AND domain=p_domain AND enabled;
 IF NOT FOUND THEN
 PERFORM markos_control.tool_log(p_owner,p_domain,actor,(d->>'conversation_id')::uuid,NULL,NULL,'denied','policy');RETURN jsonb_build_object('denied','policy');END IF;
 IF jsonb_typeof(d->'fixture_arguments')<>'object' OR d->'fixture_arguments'->>'schema_version'<>'1'
 OR octet_length(d->'fixture_arguments'->>'label') NOT BETWEEN 1 AND 1024
 OR (SELECT count(*) FROM jsonb_object_keys(d->'fixture_arguments'))<>2 THEN RAISE EXCEPTION 'invalid fixture arguments';END IF;
 IF (SELECT count(*) FROM markos_control.tool_plans WHERE state='queued')>=100 AND NOT EXISTS(SELECT 1 FROM markos_control.idempotency k JOIN markos_control.tool_plans x ON x.event_id=(k.receipt->>'event_id')::uuid AND x.owner_id=k.owner_id AND x.domain=k.domain WHERE k.owner_id=p_owner AND k.domain=p_domain AND k.actor_id=actor AND k.operation_key=d->>'operation_key') THEN
 PERFORM markos_control.tool_log(p_owner,p_domain,actor,(d->>'conversation_id')::uuid,NULL,NULL,'denied','budget');RETURN jsonb_build_object('denied','budget');END IF;
 IF EXISTS(SELECT 1 FROM markos_control.idempotency k WHERE k.owner_id=p_owner AND k.domain=p_domain AND k.actor_id=actor AND k.operation_key=d->>'operation_key' AND NOT EXISTS(SELECT 1 FROM markos_control.tool_plans x WHERE x.owner_id=p_owner AND x.domain=p_domain AND x.event_id=(k.receipt->>'event_id')::uuid)) THEN RAISE EXCEPTION 'operation profile changed' USING ERRCODE='40001';END IF;
 answer:=markos_control.admit_message_m1d(p_session,p_hash,p_owner,p_domain,d-'fixture_arguments');
 SELECT arguments_sha256 INTO old FROM markos_control.tool_plans WHERE owner_id=p_owner AND domain=p_domain AND event_id=(answer->>'event_id')::uuid;
 IF FOUND THEN IF old IS DISTINCT FROM encode(sha256(convert_to(markos_control.tool_canonical(d->'fixture_arguments'),'UTF8')),'hex') THEN RAISE EXCEPTION 'changed immutable operation' USING ERRCODE='40001';END IF;RETURN answer;END IF;
 IF EXISTS(SELECT 1 FROM markos_runtime.tasks WHERE owner_id=p_owner AND domain=p_domain AND event_id=(answer->>'event_id')::uuid AND state<>'pending') THEN RAISE EXCEPTION 'run already claimed' USING ERRCODE='40001';END IF;
 INSERT INTO markos_control.tool_plans VALUES(p_owner,p_domain,(answer->>'event_id')::uuid,d->'fixture_arguments',encode(sha256(convert_to(markos_control.tool_canonical(d->'fixture_arguments'),'UTF8')),'hex'),'queued',NULL,clock_timestamp());
 PERFORM markos_control.tool_log(p_owner,p_domain,actor,(answer->>'event_id')::uuid,NULL,NULL,'requested','accepted');
 RETURN answer;
END $$;

-- A live task, its current human authority and the shared execution slot are mandatory.
CREATE FUNCTION markos_runtime.tool_task(p_task uuid,p_fence bigint,p_executor uuid,p_epoch uuid) RETURNS markos_runtime.tasks
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$DECLARE p markos_runtime.service_identities;t markos_runtime.tasks;BEGIN
 p:=markos_runtime.chat_identity('control-test-agent');PERFORM pg_advisory_xact_lock(712093482);
 SELECT * INTO t FROM markos_runtime.tasks WHERE task_id=p_task AND owner_id=p.owner_id AND domain=p.domain AND consumer_id=p.service_id
 AND state='running' AND fence=p_fence AND executor_id=p_executor AND lease_until>clock_timestamp() FOR UPDATE;
 IF NOT FOUND OR NOT p.can_execute OR NOT p.can_consume OR NOT markos_runtime.valid_epoch(p_epoch)
 OR NOT markos_control.chat_authority(p.owner_id,p.domain,t.event_id)
 OR NOT EXISTS(SELECT 1 FROM markos_runtime.execution_slots WHERE slot_id=t.slot_id AND task_id=t.task_id AND fence=t.fence AND lease_until>clock_timestamp())
 THEN RAISE EXCEPTION 'current execution required' USING ERRCODE='42501';END IF;RETURN t;
END $$;
CREATE FUNCTION markos_control.tool_release(p_task uuid,p_fence bigint) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_control AS $$BEGIN
 UPDATE markos_runtime.execution_slots SET task_id=NULL,fence=NULL,lease_until=NULL WHERE task_id=p_task AND fence=p_fence;
 UPDATE markos_runtime.task_attempts SET outcome='review',finished_at=clock_timestamp() WHERE task_id=p_task AND fence=p_fence AND outcome='running';
 UPDATE markos_runtime.tasks SET state='review',fence=fence+1,lease_until=NULL,executor_id=NULL,slot_id=NULL WHERE task_id=p_task AND fence=p_fence;
END $$;
CREATE FUNCTION markos_runtime.wait_tool(p_task uuid,p_fence bigint,p_executor uuid,p_epoch uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE t markos_runtime.tasks;r markos_control.chat_runs;g markos_control.tool_grants;plan markos_control.tool_plans;
 ident uuid:=gen_random_uuid();now_at timestamptz:=clock_timestamp();doc jsonb;args jsonb;preview text;next_state text;BEGIN
 t:=markos_runtime.tool_task(p_task,p_fence,p_executor,p_epoch);
 SELECT * INTO STRICT r FROM markos_control.chat_runs WHERE owner_id=t.owner_id AND domain=t.domain AND event_id=t.event_id;
 SELECT * INTO plan FROM markos_control.tool_plans WHERE owner_id=t.owner_id AND domain=t.domain AND event_id=t.event_id FOR UPDATE;
 IF NOT FOUND OR plan.state<>'queued' OR plan.intent_id IS NOT NULL THEN RAISE EXCEPTION 'immutable tool plan unavailable' USING ERRCODE='40001';END IF;
 SELECT * INTO g FROM markos_control.tool_grants WHERE owner_id=t.owner_id AND domain=t.domain AND enabled;
 IF NOT FOUND THEN PERFORM markos_control.end_chat(t.owner_id,t.domain,t.event_id,'failed','authority');RETURN NULL;END IF;
 IF (SELECT count(*) FROM markos_control.tool_intents WHERE status IN ('awaiting','approved'))>=100
 OR (SELECT count(*) FROM markos_control.tool_intents WHERE owner_id=t.owner_id AND domain=t.domain AND status IN ('awaiting','approved'))>=10
 THEN PERFORM markos_control.tool_log(t.owner_id,t.domain,r.actor_id,t.event_id,NULL,NULL,'denied','budget');RETURN NULL;END IF;
 args:=plan.arguments;preview:='Synthetic fixture read: '||(args->>'label');
 doc:=jsonb_build_object('schema_version',1,'owner_id',t.owner_id,'domain',t.domain,'intent_id',ident,'principal_id',r.actor_id,
 'agent_id','control-test-agent','agent_revision',r.agent_revision,'task_id',t.task_id,'run_id',r.event_id,'epoch',r.epoch,'task_fence',t.fence+2,
 'operation_key','tool:'||replace(r.event_id::text,'-',''),'server_id','synthetic-mcp-fixture','tool','fixture.read','tool_schema_sha256',g.document->>'tool_schema_sha256',
 'grant_id',g.grant_id,'grant_revision',g.revision,'policy_revision',r.config_revision,'arguments',args,
 'arguments_sha256',encode(sha256(convert_to(markos_control.tool_canonical(args),'UTF8')),'hex'),'preview',preview,
 'preview_sha256',encode(sha256(convert_to(preview,'UTF8')),'hex'),'recipient','synthetic-mcp-fixture','data_class','synthetic','effect','read',
 'created_at',markos_control.tool_timestamp(now_at),
 'expires_at',markos_control.tool_timestamp(now_at+interval '15 minutes'),'max_attempts',1);
 next_state:=CASE WHEN (g.document->>'require_approval')::boolean THEN 'awaiting' ELSE 'approved' END;
 INSERT INTO markos_control.tool_intents(intent_id,owner_id,domain,event_id,task_id,principal_id,epoch,task_fence,grant_id,grant_revision,policy_revision,document,digest,status,expires_at)
 VALUES(ident,t.owner_id,t.domain,t.event_id,t.task_id,r.actor_id,r.epoch,t.fence+2,g.grant_id,g.revision,r.config_revision,doc,
 encode(sha256(convert_to(markos_control.tool_canonical(doc),'UTF8')),'hex'),next_state,now_at+interval '15 minutes');
 UPDATE markos_control.tool_plans SET intent_id=ident,state=CASE WHEN next_state='awaiting' THEN 'awaiting' ELSE 'ready' END WHERE owner_id=t.owner_id AND domain=t.domain AND event_id=t.event_id;
 PERFORM markos_control.tool_release(t.task_id,t.fence);
 PERFORM markos_control.tool_log(t.owner_id,t.domain,r.actor_id,t.event_id,ident,NULL,CASE WHEN next_state='awaiting' THEN 'awaiting' ELSE 'approved' END,CASE WHEN next_state='awaiting' THEN 'approval' ELSE 'standing-grant' END);
 RETURN jsonb_build_object('intent_id',ident,'state',next_state);
END $$;

CREATE FUNCTION markos_control.tool_sweep() RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_control AS $$DECLARE i markos_control.tool_intents;d markos_control.tool_dispatches;why text;BEGIN
 PERFORM pg_advisory_xact_lock(712093482);
 FOR i IN SELECT * FROM markos_control.tool_intents WHERE status IN ('awaiting','approved') FOR UPDATE LOOP
  why:=NULL;
  IF i.expires_at<=clock_timestamp() THEN why:='expired';
  ELSIF i.epoch IS DISTINCT FROM markos_runtime.current_epoch() OR NOT markos_control.chat_authority(i.owner_id,i.domain,i.event_id)
  OR NOT EXISTS(SELECT 1 FROM markos_control.tool_grants WHERE owner_id=i.owner_id AND domain=i.domain AND grant_id=i.grant_id AND revision=i.grant_revision AND enabled) THEN why:='revoked';END IF;
  IF why IS NOT NULL THEN
   UPDATE markos_control.tool_intents SET status=why,revision=revision+1,terminal_at=clock_timestamp() WHERE intent_id=i.intent_id;
   PERFORM markos_control.tool_log(i.owner_id,i.domain,i.principal_id,i.event_id,i.intent_id,NULL,why,'authority');
   PERFORM markos_control.end_chat(i.owner_id,i.domain,i.event_id,'failed','authority');
  END IF;
 END LOOP;
 FOR d IN SELECT * FROM markos_control.tool_dispatches WHERE state='dispatched' AND
 (lease_until<=clock_timestamp() OR epoch IS DISTINCT FROM markos_runtime.current_epoch()
 OR NOT EXISTS(SELECT 1 FROM markos_runtime.tasks WHERE task_id=tool_dispatches.task_id AND fence=tool_dispatches.fence AND state='running' AND lease_until>clock_timestamp())
 OR NOT markos_control.chat_authority(owner_id,domain,(SELECT event_id FROM markos_control.tool_intents WHERE intent_id=tool_dispatches.intent_id))) FOR UPDATE LOOP
  SELECT * INTO STRICT i FROM markos_control.tool_intents WHERE intent_id=d.intent_id;
  UPDATE markos_control.tool_dispatches SET state='unknown',revision=revision+1,finished_at=clock_timestamp() WHERE dispatch_id=d.dispatch_id;
  UPDATE markos_control.tool_plans SET state='unknown' WHERE intent_id=i.intent_id;
  UPDATE markos_control.tool_slots SET dispatch_id=NULL,lease_until=NULL WHERE dispatch_id=d.dispatch_id;
  PERFORM markos_control.tool_release(d.task_id,d.fence);
  PERFORM markos_control.tool_log(i.owner_id,i.domain,i.principal_id,i.event_id,i.intent_id,d.dispatch_id,'unknown','lease-or-authority');
 END LOOP;
END $$;
CREATE FUNCTION markos_control.read_tool_records(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_kind text,p_after bigint,p_limit integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE answer jsonb;BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 IF p_limit NOT BETWEEN 1 AND 100 OR p_after<0 OR p_kind NOT IN ('approvals','audit','dispatches') THEN RAISE EXCEPTION 'invalid bounded page';END IF;
 PERFORM markos_control.tool_sweep();
 IF p_kind='audit' THEN
 SELECT coalesce(jsonb_agg(to_jsonb(a)),'[]') INTO answer FROM (SELECT * FROM markos_control.tool_audit WHERE owner_id=p_owner AND domain=p_domain AND sequence>p_after ORDER BY sequence LIMIT p_limit) a;
 ELSIF p_kind='approvals' THEN
 SELECT coalesce(jsonb_agg(to_jsonb(a)),'[]') INTO answer FROM
 (SELECT 1 AS schema_version,i.owner_id,i.domain,coalesce((SELECT (h.document->>'require_approval')::boolean FROM markos_control.tool_grant_history h WHERE h.owner_id=i.owner_id AND h.domain=i.domain AND h.revision=i.grant_revision),true) AS requires_human,i.intent_id AS approval_id,i.intent_id,i.digest AS intent_sha256,i.revision,i.status,i.approver_id,i.dispatch_id,i.expires_at,i.document AS intent,
 (SELECT document->>'endpoint' FROM markos_control.tool_grant_history WHERE owner_id=i.owner_id AND domain=i.domain AND revision=i.grant_revision) AS endpoint
 FROM markos_control.tool_intents i WHERE i.owner_id=p_owner AND i.domain=p_domain ORDER BY i.expires_at,i.intent_id OFFSET p_after LIMIT p_limit) a;
 ELSE
 SELECT coalesce(jsonb_agg(to_jsonb(a)),'[]') INTO answer FROM
 (SELECT 1 AS schema_version,owner_id,domain,dispatch_id,operation_id,intent_id,state,revision,started_at,finished_at,result_sha256,result FROM markos_control.tool_dispatches WHERE owner_id=p_owner AND domain=p_domain ORDER BY started_at,dispatch_id OFFSET p_after LIMIT p_limit) a;
 END IF;RETURN answer;
END $$;
CREATE FUNCTION markos_control.decide_tool(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_intent uuid,d jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE actor uuid;i markos_control.tool_intents;next_status text;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'decide-approval');PERFORM markos_control.tool_sweep();
 SELECT * INTO i FROM markos_control.tool_intents WHERE intent_id=p_intent AND owner_id=p_owner AND domain=p_domain FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'object unavailable' USING ERRCODE='P0002';END IF;
 IF (d->>'intent_id')::uuid<>i.intent_id OR d->>'intent_sha256'<>i.digest THEN RAISE EXCEPTION 'immutable decision changed' USING ERRCODE='40001';END IF;
 next_status:=CASE d->>'decision' WHEN 'approve' THEN 'approved' WHEN 'reject' THEN 'rejected' WHEN 'revoke' THEN 'revoked' ELSE NULL END;
 IF next_status IS NULL THEN RAISE EXCEPTION 'invalid decision';END IF;
 IF i.status=next_status AND i.approver_id=actor AND i.revision=(d->>'expected_revision')::integer+1 THEN RETURN jsonb_build_object('intent_id',i.intent_id,'status',i.status,'revision',i.revision);END IF;
 IF i.revision<>(d->>'expected_revision')::integer OR (i.status<>'awaiting' AND NOT (i.status='approved' AND next_status='revoked')) THEN RAISE EXCEPTION 'stale decision' USING ERRCODE='40001';END IF;
 UPDATE markos_control.tool_intents SET status=next_status,revision=revision+1,approver_id=actor,
 terminal_at=CASE WHEN next_status IN ('rejected','revoked') THEN clock_timestamp() END WHERE intent_id=i.intent_id RETURNING * INTO i;
 UPDATE markos_control.tool_plans SET state=CASE WHEN next_status='approved' THEN 'ready' ELSE 'failed' END WHERE intent_id=i.intent_id;
 PERFORM markos_control.tool_log(p_owner,p_domain,actor,i.event_id,i.intent_id,NULL,next_status,'human-decision');
 IF next_status<>'approved' THEN PERFORM markos_control.end_chat(p_owner,p_domain,i.event_id,'failed','authority');END IF;
 RETURN jsonb_build_object('intent_id',i.intent_id,'status',i.status,'revision',i.revision);
END $$;
CREATE FUNCTION markos_runtime.tool_context(p_task uuid,p_fence bigint,p_executor uuid,p_epoch uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$DECLARE t markos_runtime.tasks;plan markos_control.tool_plans;BEGIN
 t:=markos_runtime.tool_task(p_task,p_fence,p_executor,p_epoch);
 SELECT * INTO STRICT plan FROM markos_control.tool_plans WHERE owner_id=t.owner_id AND domain=t.domain AND event_id=t.event_id;
 RETURN jsonb_build_object('state',plan.state,'intent',(SELECT document FROM markos_control.tool_intents WHERE intent_id=plan.intent_id),
 'intent_sha256',(SELECT digest FROM markos_control.tool_intents WHERE intent_id=plan.intent_id),
 'grant',(SELECT document||jsonb_build_object('enabled',enabled) FROM markos_control.tool_grants WHERE owner_id=t.owner_id AND domain=t.domain));
END $$;
CREATE FUNCTION markos_runtime.dispatch_tool(p_task uuid,p_fence bigint,p_executor uuid,p_epoch uuid,p_intent uuid,p_digest text,p_endpoint text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$DECLARE t markos_runtime.tasks;i markos_control.tool_intents;g markos_control.tool_grants;chosen integer;
 ident uuid:=gen_random_uuid();operation uuid:=gen_random_uuid();rate markos_control.tool_rates;now_at timestamptz:=clock_timestamp();deadline timestamptz;BEGIN
 t:=markos_runtime.tool_task(p_task,p_fence,p_executor,p_epoch);PERFORM markos_control.tool_sweep();
 SELECT * INTO i FROM markos_control.tool_intents WHERE intent_id=p_intent AND owner_id=t.owner_id AND domain=t.domain AND task_id=t.task_id FOR UPDATE;
 SELECT * INTO g FROM markos_control.tool_grants WHERE owner_id=t.owner_id AND domain=t.domain;
 IF i.intent_id IS NULL OR i.status<>'approved' OR i.resume_fence IS DISTINCT FROM t.fence OR i.resume_fence<i.task_fence OR i.digest<>p_digest OR i.epoch<>p_epoch OR i.expires_at<=now_at
 OR NOT g.enabled OR g.revision<>i.grant_revision OR g.grant_id<>i.grant_id OR g.document->>'endpoint'<>p_endpoint
 OR EXISTS(SELECT 1 FROM markos_control.tool_dispatches WHERE intent_id=i.intent_id)
 THEN PERFORM markos_control.tool_log(t.owner_id,t.domain,(SELECT actor_id FROM markos_control.chat_runs WHERE owner_id=t.owner_id AND domain=t.domain AND event_id=t.event_id),t.event_id,i.intent_id,NULL,'denied','authority');RETURN jsonb_build_object('denied','authority');END IF;
 IF (g.document->>'require_approval')::boolean AND (i.approver_id IS NULL OR NOT EXISTS
 (SELECT 1 FROM markos_control.memberships m JOIN markos_control.principals h USING(principal_id)
 WHERE m.principal_id=i.approver_id AND m.owner_id=i.owner_id AND m.domain=i.domain AND 'approver'=ANY(m.roles) AND NOT h.disabled))
 THEN PERFORM markos_control.tool_log(t.owner_id,t.domain,(SELECT actor_id FROM markos_control.chat_runs WHERE owner_id=t.owner_id AND domain=t.domain AND event_id=t.event_id),t.event_id,i.intent_id,NULL,'denied','authority');RETURN jsonb_build_object('denied','authority');END IF;
 SELECT slot_id INTO chosen FROM markos_control.tool_slots WHERE lease_until IS NULL OR lease_until<=now_at ORDER BY slot_id LIMIT 1 FOR UPDATE;
 IF NOT FOUND OR (SELECT count(*) FROM markos_control.tool_dispatches WHERE reserved)>=100
 OR (SELECT count(*) FROM markos_control.tool_dispatches WHERE owner_id=t.owner_id AND domain=t.domain AND reserved)>=20
 THEN PERFORM markos_control.tool_log(t.owner_id,t.domain,i.principal_id,t.event_id,i.intent_id,NULL,'denied','budget');RETURN jsonb_build_object('denied','budget');END IF;
 INSERT INTO markos_control.tool_rates VALUES(t.owner_id,t.domain,2,now_at) ON CONFLICT DO NOTHING;
 SELECT * INTO STRICT rate FROM markos_control.tool_rates WHERE owner_id=t.owner_id AND domain=t.domain FOR UPDATE;
 rate.tokens:=least(2,rate.tokens+greatest(0,extract(epoch FROM now_at-rate.at))/6);
 UPDATE markos_control.tool_rates SET tokens=rate.tokens,at=now_at WHERE owner_id=t.owner_id AND domain=t.domain;
 IF rate.tokens<1 THEN PERFORM markos_control.tool_log(t.owner_id,t.domain,i.principal_id,t.event_id,i.intent_id,NULL,'denied','rate');RETURN jsonb_build_object('denied','budget');END IF;
 UPDATE markos_control.tool_rates SET tokens=tokens-1 WHERE owner_id=t.owner_id AND domain=t.domain;
 deadline:=least(t.lease_until,now_at+interval '10 seconds');
 INSERT INTO markos_control.tool_dispatches(dispatch_id,operation_id,intent_id,owner_id,domain,epoch,task_id,fence,executor_id,state,lease_until)
 VALUES(ident,operation,i.intent_id,t.owner_id,t.domain,p_epoch,t.task_id,t.fence,p_executor,'dispatched',deadline);
 UPDATE markos_control.tool_intents SET status='consumed',revision=revision+1,dispatch_id=ident WHERE intent_id=i.intent_id;
 UPDATE markos_control.tool_plans SET state='dispatched' WHERE intent_id=i.intent_id;
 UPDATE markos_control.tool_slots SET dispatch_id=ident,lease_until=deadline WHERE slot_id=chosen;
 PERFORM markos_control.tool_log(t.owner_id,t.domain,i.principal_id,t.event_id,i.intent_id,ident,'dispatched','accepted');
 RETURN jsonb_build_object('dispatch_id',ident,'operation_id',operation,'deadline',deadline);
END $$;
CREATE FUNCTION markos_runtime.finish_tool(p_task uuid,p_fence bigint,p_executor uuid,p_epoch uuid,p_dispatch uuid,p_result jsonb,p_result_hash text,p_unknown boolean) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$DECLARE t markos_runtime.tasks;d markos_control.tool_dispatches;i markos_control.tool_intents;outcome_state text;BEGIN
 t:=markos_runtime.tool_task(p_task,p_fence,p_executor,p_epoch);
 SELECT * INTO d FROM markos_control.tool_dispatches WHERE dispatch_id=p_dispatch AND task_id=t.task_id AND owner_id=t.owner_id AND domain=t.domain
 AND fence=t.fence AND executor_id=p_executor AND epoch=p_epoch AND state='dispatched' AND lease_until>clock_timestamp() FOR UPDATE;
 IF NOT FOUND THEN RETURN false;END IF;
 SELECT * INTO STRICT i FROM markos_control.tool_intents WHERE intent_id=d.intent_id;
 IF p_unknown IS NULL OR (NOT p_unknown AND (p_result IS NULL OR p_result_hash IS NULL OR p_result_hash !~ '^[0-9a-f]{64}$')) THEN RAISE EXCEPTION 'typed tool result required';END IF;
 IF NOT p_unknown AND (p_result->>'schema_version'<>'1' OR p_result->>'owner_id'<>t.owner_id::text OR p_result->>'domain'<>t.domain
 OR p_result->>'kind'<>'synthetic' OR p_result->>'label' IS DISTINCT FROM i.document->'arguments'->>'label'
 OR (SELECT count(*) FROM jsonb_object_keys(p_result))<>5 OR p_result_hash<>encode(sha256(convert_to(markos_control.tool_canonical(p_result),'UTF8')),'hex')) THEN RAISE EXCEPTION 'invalid scoped fixture result';END IF;
 outcome_state:=CASE WHEN p_unknown THEN 'unknown' ELSE 'succeeded' END;
 UPDATE markos_control.tool_dispatches SET state=outcome_state,finished_at=clock_timestamp(),revision=revision+1,reserved=p_unknown,
 result=CASE WHEN p_unknown THEN NULL ELSE p_result END,result_sha256=CASE WHEN p_unknown THEN NULL ELSE p_result_hash END WHERE dispatch_id=d.dispatch_id;
 UPDATE markos_control.tool_plans SET state=outcome_state WHERE intent_id=i.intent_id;
 UPDATE markos_control.tool_intents SET terminal_at=CASE WHEN p_unknown THEN NULL ELSE clock_timestamp() END WHERE intent_id=i.intent_id;
 UPDATE markos_control.tool_slots SET dispatch_id=NULL,lease_until=NULL WHERE dispatch_id=d.dispatch_id;
 PERFORM markos_control.tool_log(t.owner_id,t.domain,i.principal_id,t.event_id,i.intent_id,d.dispatch_id,outcome_state,CASE WHEN p_unknown THEN 'unknown' ELSE 'accepted' END);
 IF p_unknown THEN PERFORM markos_control.tool_release(t.task_id,t.fence);END IF;RETURN true;
END $$;
CREATE FUNCTION markos_runtime.invalidate_tool_grant(p_task uuid,p_fence bigint,p_executor uuid,p_epoch uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$DECLARE t markos_runtime.tasks;BEGIN
 t:=markos_runtime.tool_task(p_task,p_fence,p_executor,p_epoch);
 UPDATE markos_control.tool_grants SET enabled=false WHERE owner_id=t.owner_id AND domain=t.domain;
 PERFORM markos_control.tool_sweep();
END $$;
CREATE FUNCTION markos_control.reconcile_tool(p_session uuid,p_hash text,p_owner uuid,p_domain text,d jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE actor uuid;attempt markos_control.tool_dispatches;i markos_control.tool_intents;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'decide-approval');PERFORM markos_control.tool_sweep();
 SELECT * INTO attempt FROM markos_control.tool_dispatches WHERE dispatch_id=(d->>'dispatch_id')::uuid AND owner_id=p_owner AND domain=p_domain FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'object unavailable' USING ERRCODE='P0002';END IF;
 IF attempt.state<>'unknown' OR attempt.revision<>(d->>'expected_revision')::integer THEN RAISE EXCEPTION 'stale reconciliation' USING ERRCODE='40001';END IF;
 SELECT * INTO STRICT i FROM markos_control.tool_intents WHERE intent_id=attempt.intent_id;
 IF d->>'outcome'<>'unresolved' THEN
  UPDATE markos_control.tool_dispatches SET state='resolved',revision=revision+1,reserved=false,finished_at=clock_timestamp() WHERE dispatch_id=attempt.dispatch_id;
  UPDATE markos_control.tool_plans SET state='failed' WHERE intent_id=i.intent_id;
  UPDATE markos_control.tool_intents SET terminal_at=clock_timestamp() WHERE intent_id=i.intent_id;
  -- Human reconciliation records external evidence, never manufactures an executable reply or retry.
  PERFORM markos_control.end_chat(p_owner,p_domain,i.event_id,'failed','execution');
 END IF;
 PERFORM markos_control.tool_log(p_owner,p_domain,actor,i.event_id,i.intent_id,attempt.dispatch_id,'reconciled',d->>'outcome');
 RETURN jsonb_build_object('dispatch_id',attempt.dispatch_id,'state',CASE WHEN d->>'outcome'='unresolved' THEN 'unknown' ELSE 'resolved' END);
END $$;

-- Explicit migration-role-only retention. No runtime clock override or scheduled deletion.
CREATE TABLE markos_control.tool_tombstones(
 owner_id uuid NOT NULL,domain text NOT NULL,event_id uuid NOT NULL,principal_id uuid NOT NULL,
 request_key_hash text NOT NULL,intent_sha256 text NOT NULL,operation_id uuid,
 PRIMARY KEY(owner_id,domain,event_id)
);
ALTER TABLE markos_control.tool_tombstones ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_control.tool_tombstones FROM PUBLIC,markos_control_gateway,markos_runtime_client;
CREATE FUNCTION markos_control.cleanup_tools() RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_control AS $$DECLARE now_at timestamptz:=markos_runtime.recovery_now();i markos_control.tool_intents;
 copies integer:=0;metadata integer:=0;BEGIN
 PERFORM pg_advisory_xact_lock(712093482);
 FOR i IN SELECT * FROM markos_control.tool_intents x WHERE terminal_at+interval '7 days'<=now_at AND document IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM markos_control.tool_dispatches d WHERE d.intent_id=x.intent_id AND d.reserved)
 AND NOT EXISTS(SELECT 1 FROM markos_control.tool_holds h WHERE h.intent_id=x.intent_id)
 ORDER BY terminal_at,intent_id LIMIT 100 FOR UPDATE LOOP
  UPDATE markos_control.tool_intents SET document=NULL WHERE intent_id=i.intent_id;
  UPDATE markos_control.tool_plans SET arguments=NULL WHERE intent_id=i.intent_id;
  UPDATE markos_control.tool_dispatches SET result=NULL WHERE intent_id=i.intent_id;
  PERFORM markos_control.tool_log(i.owner_id,i.domain,i.principal_id,i.event_id,i.intent_id,i.dispatch_id,'cleanup','7-day-policy');
  copies:=copies+1;
 END LOOP;
 FOR i IN SELECT * FROM markos_control.tool_intents x WHERE terminal_at+interval '365 days'<=now_at
 AND NOT EXISTS(SELECT 1 FROM markos_control.tool_dispatches d WHERE d.intent_id=x.intent_id AND d.reserved)
 AND NOT EXISTS(SELECT 1 FROM markos_control.tool_holds h WHERE h.intent_id=x.intent_id)
 AND EXISTS(SELECT 1 FROM markos_control.chat_runs r WHERE r.owner_id=x.owner_id AND r.domain=x.domain AND r.event_id=x.event_id AND r.state IN ('succeeded','failed','cancelled'))
 ORDER BY terminal_at,intent_id LIMIT 100 FOR UPDATE LOOP
  INSERT INTO markos_control.tool_tombstones(owner_id,domain,event_id,principal_id,request_key_hash,intent_sha256,operation_id)
  SELECT i.owner_id,i.domain,i.event_id,i.principal_id,encode(sha256(convert_to(k.operation_key,'UTF8')),'hex'),i.digest,
  (SELECT operation_id FROM markos_control.tool_dispatches WHERE intent_id=i.intent_id)
  FROM markos_control.idempotency k WHERE k.owner_id=i.owner_id AND k.domain=i.domain AND k.actor_id=i.principal_id AND (k.receipt->>'event_id')::uuid=i.event_id
  ON CONFLICT DO NOTHING;
  -- Do not remove deduplication evidence if its anti-replay tombstone could not be committed.
  IF NOT EXISTS(SELECT 1 FROM markos_control.tool_tombstones WHERE owner_id=i.owner_id AND domain=i.domain AND event_id=i.event_id) THEN CONTINUE;END IF;
  DELETE FROM markos_control.tool_audit WHERE intent_id=i.intent_id;
  DELETE FROM markos_control.tool_dispatches WHERE intent_id=i.intent_id;
  DELETE FROM markos_control.tool_intents WHERE intent_id=i.intent_id;
  DELETE FROM markos_control.tool_plans WHERE intent_id=i.intent_id;
  PERFORM markos_control.tool_log(i.owner_id,i.domain,i.principal_id,i.event_id,NULL,NULL,'cleanup','365-day-policy');
  metadata:=metadata+1;
 END LOOP;
 DELETE FROM markos_control.tool_audit WHERE sequence IN
 (SELECT a.sequence FROM markos_control.tool_audit a WHERE a.intent_id IS NULL AND a.recorded_at+interval '365 days'<=now_at
 AND NOT EXISTS(SELECT 1 FROM markos_control.tool_grants g WHERE g.owner_id=a.owner_id AND g.domain=a.domain AND g.grant_id=a.correlation_id AND g.enabled)
 ORDER BY a.sequence LIMIT 1000);
 RETURN jsonb_build_object('payload_copies_purged',copies,'terminal_intents_purged',metadata,
 'unresolved_alerts',(SELECT count(*) FROM markos_control.tool_dispatches WHERE reserved AND started_at+interval '24 hours'<=now_at),
 'holds_due',(SELECT count(*) FROM markos_control.tool_holds WHERE review_at<=now_at));
END $$;

ALTER FUNCTION markos_runtime.claim_chat(uuid,integer) RENAME TO claim_chat_m1d;
REVOKE ALL ON FUNCTION markos_runtime.claim_chat_m1d(uuid,integer) FROM markos_runtime_client;
CREATE FUNCTION markos_runtime.claim_chat(p_executor uuid,p_seconds integer) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_runtime AS $$DECLARE p markos_runtime.service_identities;t markos_runtime.tasks;r markos_control.chat_runs;m markos_control.messages;s markos_control.sessions;subject text;chosen integer;deadline timestamptz;ordinal bigint;BEGIN
 p:=markos_runtime.chat_identity('control-test-agent');
 IF NOT p.can_consume OR NOT p.can_execute OR p_executor IS NULL OR p_executor='00000000-0000-0000-0000-000000000000' OR p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 30 THEN RAISE EXCEPTION 'invalid chat claim';END IF;
 PERFORM pg_advisory_xact_lock(712093482);
 FOR t IN SELECT * FROM markos_runtime.tasks WHERE owner_id=p.owner_id AND domain=p.domain AND consumer_id=p.service_id
 AND NOT EXISTS(SELECT 1 FROM markos_control.tool_plans x WHERE x.owner_id=tasks.owner_id AND x.domain=tasks.domain AND x.event_id=tasks.event_id)
 AND ((state='pending' AND available_at<=clock_timestamp()) OR (state='running' AND lease_until<=clock_timestamp())) ORDER BY created_at,task_id LIMIT 100 FOR UPDATE LOOP
  SELECT * INTO r FROM markos_control.chat_runs WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=t.event_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'untyped chat task';END IF;
  IF markos_runtime.event_gate(p.owner_id,p.domain,t.event_id,p.service_id) IS NOT NULL OR NOT markos_control.chat_authority(p.owner_id,p.domain,t.event_id) THEN PERFORM markos_control.end_chat(p.owner_id,p.domain,t.event_id,'failed','authority');CONTINUE;END IF;
  IF t.attempts>=3 THEN PERFORM markos_control.end_chat(p.owner_id,p.domain,t.event_id,'failed','abandoned');CONTINUE;END IF;
  SELECT slot_id INTO chosen FROM markos_runtime.execution_slots WHERE lease_until IS NULL OR lease_until<=clock_timestamp() ORDER BY slot_id LIMIT 1 FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL;END IF;
  deadline:=clock_timestamp()+make_interval(secs=>p_seconds);
  UPDATE markos_runtime.task_attempts SET outcome='abandoned',finished_at=clock_timestamp() WHERE task_id=t.task_id AND outcome='running';
  UPDATE markos_runtime.tasks SET state='running',attempts=attempts+1,fence=fence+1,lease_until=deadline,executor_id=p_executor,slot_id=chosen WHERE task_id=t.task_id RETURNING * INTO t;
  UPDATE markos_runtime.execution_slots SET task_id=t.task_id,fence=t.fence,lease_until=deadline WHERE slot_id=chosen;
  INSERT INTO markos_runtime.task_attempts(owner_id,domain,consumer_id,task_id,fence,executor_id,outcome) VALUES(p.owner_id,p.domain,p.service_id,t.task_id,t.fence,p_executor,'running');
  IF r.state='queued' THEN
   UPDATE markos_control.chat_runs SET state='working' WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=t.event_id;
   ordinal:=markos_control.next_scope_sequence(p.owner_id,p.domain);
   INSERT INTO markos_control.activity(owner_id,domain,sequence,conversation_id,object_id,correlation_id,kind) VALUES(p.owner_id,p.domain,ordinal,r.conversation_id,r.message_id,r.event_id,'working');
  END IF;
  SELECT * INTO STRICT m FROM markos_control.messages WHERE owner_id=p.owner_id AND domain=p.domain AND message_id=r.message_id;
  SELECT * INTO STRICT s FROM markos_control.sessions WHERE session_id=r.session_id;
  SELECT principals.subject INTO STRICT subject FROM markos_control.principals WHERE principal_id=r.actor_id;
  RETURN jsonb_build_object('task_id',t.task_id,'event_id',r.event_id,'fence',t.fence,'attempt',t.attempts,'epoch',r.epoch,
   'lease_until',deadline,'message_sha256',m.body_sha256,'subject',subject,'provider_session_id',s.provider_session_id,'provider_expires_at',s.provider_expires_at);
 END LOOP;
 RETURN NULL;
END $$;
CREATE FUNCTION markos_runtime.claim_governed_chat(p_executor uuid,p_seconds integer) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_runtime AS $$DECLARE p markos_runtime.service_identities;t markos_runtime.tasks;r markos_control.chat_runs;m markos_control.messages;s markos_control.sessions;subject text;chosen integer;deadline timestamptz;ordinal bigint;BEGIN
 p:=markos_runtime.chat_identity('control-test-agent');
 IF NOT p.can_consume OR NOT p.can_execute OR p_executor IS NULL OR p_executor='00000000-0000-0000-0000-000000000000' OR p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 30 THEN RAISE EXCEPTION 'invalid chat claim';END IF;
 PERFORM pg_advisory_xact_lock(712093482);PERFORM markos_control.tool_sweep();
 FOR t IN SELECT * FROM markos_runtime.tasks WHERE owner_id=p.owner_id AND domain=p.domain AND consumer_id=p.service_id
 AND EXISTS(SELECT 1 FROM markos_control.tool_plans x WHERE x.owner_id=tasks.owner_id AND x.domain=tasks.domain AND x.event_id=tasks.event_id AND
 ((x.state='queued' AND tasks.state IN ('pending','review','running') AND (tasks.state<>'running' OR tasks.lease_until<=clock_timestamp())
 AND (SELECT count(*) FROM markos_control.tool_intents WHERE status IN ('awaiting','approved'))<100
 AND (SELECT count(*) FROM markos_control.tool_intents WHERE owner_id=tasks.owner_id AND domain=tasks.domain AND status IN ('awaiting','approved'))<10)
 OR (x.state='succeeded' AND (tasks.state='review' OR (tasks.state='running' AND tasks.lease_until<=clock_timestamp())))
 OR (x.state='ready' AND markos_control.tool_capacity(tasks.owner_id,tasks.domain) AND (tasks.state='review' OR (tasks.state='running' AND tasks.lease_until<=clock_timestamp())) AND EXISTS(SELECT 1 FROM markos_control.tool_intents i WHERE i.intent_id=x.intent_id AND i.status='approved' ))))
 ORDER BY created_at,task_id LIMIT 100 FOR UPDATE LOOP
  SELECT * INTO r FROM markos_control.chat_runs WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=t.event_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'untyped chat task';END IF;
  IF markos_runtime.event_gate(p.owner_id,p.domain,t.event_id,p.service_id) IS NOT NULL OR NOT markos_control.chat_authority(p.owner_id,p.domain,t.event_id) THEN PERFORM markos_control.end_chat(p.owner_id,p.domain,t.event_id,'failed','authority');CONTINUE;END IF;
  IF t.attempts>=3 THEN PERFORM markos_control.end_chat(p.owner_id,p.domain,t.event_id,'failed','abandoned');CONTINUE;END IF;
  SELECT slot_id INTO chosen FROM markos_runtime.execution_slots WHERE lease_until IS NULL OR lease_until<=clock_timestamp() ORDER BY slot_id LIMIT 1 FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL;END IF;
  deadline:=clock_timestamp()+make_interval(secs=>p_seconds);
  UPDATE markos_runtime.task_attempts SET outcome='abandoned',finished_at=clock_timestamp() WHERE task_id=t.task_id AND outcome='running';
  UPDATE markos_runtime.tasks SET state='running',attempts=attempts+1,fence=fence+1,lease_until=deadline,executor_id=p_executor,slot_id=chosen WHERE task_id=t.task_id RETURNING * INTO t;
  UPDATE markos_runtime.execution_slots SET task_id=t.task_id,fence=t.fence,lease_until=deadline WHERE slot_id=chosen;
  INSERT INTO markos_runtime.task_attempts(owner_id,domain,consumer_id,task_id,fence,executor_id,outcome) VALUES(p.owner_id,p.domain,p.service_id,t.task_id,t.fence,p_executor,'running');
  IF r.state='queued' THEN
   UPDATE markos_control.chat_runs SET state='working' WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=t.event_id;
   ordinal:=markos_control.next_scope_sequence(p.owner_id,p.domain);
   INSERT INTO markos_control.activity(owner_id,domain,sequence,conversation_id,object_id,correlation_id,kind) VALUES(p.owner_id,p.domain,ordinal,r.conversation_id,r.message_id,r.event_id,'working');
  END IF;
  UPDATE markos_control.tool_intents SET resume_fence=t.fence WHERE owner_id=t.owner_id AND domain=t.domain AND event_id=t.event_id AND status='approved';
  SELECT * INTO STRICT m FROM markos_control.messages WHERE owner_id=p.owner_id AND domain=p.domain AND message_id=r.message_id;
  SELECT * INTO STRICT s FROM markos_control.sessions WHERE session_id=r.session_id;
  SELECT principals.subject INTO STRICT subject FROM markos_control.principals WHERE principal_id=r.actor_id;
  RETURN jsonb_build_object('task_id',t.task_id,'event_id',r.event_id,'fence',t.fence,'attempt',t.attempts,'epoch',r.epoch,
   'lease_until',deadline,'message_sha256',m.body_sha256,'subject',subject,'provider_session_id',s.provider_session_id,'provider_expires_at',s.provider_expires_at);
 END LOOP;
 RETURN NULL;
END $$;

ALTER FUNCTION markos_runtime.finish_chat(uuid,bigint,uuid,uuid,text,text) RENAME TO finish_chat_m1d;
REVOKE ALL ON FUNCTION markos_runtime.finish_chat_m1d(uuid,bigint,uuid,uuid,text,text) FROM markos_runtime_client;
CREATE FUNCTION markos_runtime.finish_chat(p_task uuid,p_fence bigint,p_executor uuid,p_epoch uuid,p_reply text,p_failure text DEFAULT NULL) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$DECLARE p markos_runtime.service_identities;t markos_runtime.tasks;BEGIN
 p:=markos_runtime.chat_identity('control-test-agent');
 SELECT * INTO t FROM markos_runtime.tasks WHERE task_id=p_task AND owner_id=p.owner_id AND domain=p.domain;
 IF EXISTS(SELECT 1 FROM markos_control.tool_plans WHERE owner_id=t.owner_id AND domain=t.domain AND event_id=t.event_id AND state<>'succeeded') AND p_failure IS NULL THEN RETURN false;END IF;
 RETURN markos_runtime.finish_chat_m1d(p_task,p_fence,p_executor,p_epoch,p_reply,p_failure);
END $$;
ALTER FUNCTION markos_control.end_chat(uuid,text,uuid,text,text) RENAME TO end_chat_m1d;
CREATE FUNCTION markos_control.end_chat(p_owner uuid,p_domain text,p_event uuid,p_state text,p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE i markos_control.tool_intents;d markos_control.tool_dispatches;BEGIN
 PERFORM markos_control.end_chat_m1d(p_owner,p_domain,p_event,p_state,p_reason);
 SELECT * INTO i FROM markos_control.tool_intents WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
 IF FOUND THEN
  IF i.status IN ('awaiting','approved') THEN
   UPDATE markos_control.tool_intents SET status='revoked',revision=revision+1,terminal_at=clock_timestamp() WHERE intent_id=i.intent_id;
   PERFORM markos_control.tool_log(p_owner,p_domain,i.principal_id,p_event,i.intent_id,NULL,'revoked','run-ended');
  END IF;
  SELECT * INTO d FROM markos_control.tool_dispatches WHERE intent_id=i.intent_id;
  IF FOUND AND d.state='dispatched' THEN
   UPDATE markos_control.tool_dispatches SET state='unknown',revision=revision+1,finished_at=clock_timestamp() WHERE dispatch_id=d.dispatch_id;
   UPDATE markos_control.tool_slots SET dispatch_id=NULL,lease_until=NULL WHERE dispatch_id=d.dispatch_id;
   PERFORM markos_control.tool_log(p_owner,p_domain,i.principal_id,p_event,i.intent_id,d.dispatch_id,'unknown','run-ended');
  END IF;
 END IF;
 UPDATE markos_control.tool_plans SET state=CASE WHEN EXISTS(SELECT 1 FROM markos_control.tool_dispatches WHERE intent_id=tool_plans.intent_id AND reserved) THEN 'unknown' WHEN p_state='cancelled' THEN 'cancelled' ELSE 'failed' END
 WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
END $$;
ALTER FUNCTION markos_control.chat_status(uuid,text,uuid,text,uuid,uuid) RENAME TO chat_status_m1e;
REVOKE ALL ON FUNCTION markos_control.chat_status_m1e(uuid,text,uuid,text,uuid,uuid) FROM markos_control_gateway;
CREATE FUNCTION markos_control.chat_status(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_conversation uuid,p_message uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE answer jsonb;state text;BEGIN
 answer:=markos_control.chat_status_m1e(p_session,p_hash,p_owner,p_domain,p_conversation,p_message);
 SELECT p.state INTO state FROM markos_control.tool_plans p JOIN markos_control.chat_runs r USING(owner_id,domain,event_id)
 WHERE r.owner_id=p_owner AND r.domain=p_domain AND r.message_id=p_message;
 IF state='awaiting' THEN answer:=answer||jsonb_build_object('state','awaiting-decision');
 ELSIF state='unknown' THEN answer:=answer||jsonb_build_object('state','unknown','can_cancel',false);END IF;RETURN answer;
END $$;
ALTER FUNCTION markos_runtime.fence_restored_state() RENAME TO fence_restored_state_m0f;
CREATE FUNCTION markos_runtime.fence_restored_state() RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_runtime AS $$DECLARE fresh uuid;BEGIN
 fresh:=markos_runtime.fence_restored_state_m0f();
 UPDATE markos_control.tool_intents SET status='revoked',revision=revision+1,terminal_at=clock_timestamp() WHERE status IN ('awaiting','approved');
 UPDATE markos_control.tool_dispatches SET state='unknown',revision=revision+1,finished_at=clock_timestamp() WHERE state='dispatched';
 UPDATE markos_control.tool_slots SET dispatch_id=NULL,lease_until=NULL;
 UPDATE markos_control.tool_plans SET state=CASE WHEN EXISTS(SELECT 1 FROM markos_control.tool_dispatches WHERE intent_id=tool_plans.intent_id AND reserved) THEN 'unknown' ELSE 'failed' END WHERE state NOT IN ('succeeded','failed','cancelled');
 RETURN fresh;
END $$;
ALTER FUNCTION markos_control.admit_message(uuid,text,uuid,text,jsonb) RENAME TO admit_message_m1d;
REVOKE ALL ON FUNCTION markos_control.admit_message_m1d(uuid,text,uuid,text,jsonb) FROM markos_control_gateway;
CREATE FUNCTION markos_control.admit_message(p_session uuid,p_hash text,p_owner uuid,p_domain text,d jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE actor uuid;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 IF EXISTS(SELECT 1 FROM markos_control.idempotency k JOIN markos_control.tool_plans x ON x.event_id=(k.receipt->>'event_id')::uuid AND x.owner_id=k.owner_id AND x.domain=k.domain
 WHERE k.owner_id=p_owner AND k.domain=p_domain AND k.actor_id=actor AND k.operation_key=d->>'operation_key') THEN RAISE EXCEPTION 'operation profile changed' USING ERRCODE='40001';END IF;
 RETURN markos_control.admit_message_m1d(p_session,p_hash,p_owner,p_domain,d);
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA markos_control FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA markos_runtime FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.admit_message(uuid,text,uuid,text,jsonb),markos_control.save_tool_grant(uuid,text,jsonb),markos_control.read_tool_grant(uuid,text,uuid,text),
 markos_control.admit_governed_message(uuid,text,uuid,text,jsonb),markos_control.read_tool_records(uuid,text,uuid,text,text,bigint,integer),
 markos_control.decide_tool(uuid,text,uuid,text,uuid,jsonb),markos_control.reconcile_tool(uuid,text,uuid,text,jsonb),
 markos_control.chat_status(uuid,text,uuid,text,uuid,uuid) TO markos_control_gateway;
GRANT EXECUTE ON FUNCTION markos_runtime.claim_chat(uuid,integer),markos_runtime.claim_governed_chat(uuid,integer),
 markos_runtime.finish_chat(uuid,bigint,uuid,uuid,text,text),markos_runtime.wait_tool(uuid,bigint,uuid,uuid),
 markos_runtime.tool_context(uuid,bigint,uuid,uuid),markos_runtime.dispatch_tool(uuid,bigint,uuid,uuid,uuid,text,text),
 markos_runtime.finish_tool(uuid,bigint,uuid,uuid,uuid,jsonb,text,boolean),markos_runtime.invalidate_tool_grant(uuid,bigint,uuid,uuid) TO markos_runtime_client;

CREATE INDEX tool_audit_scope ON markos_control.tool_audit(owner_id,domain,sequence);
CREATE INDEX tool_intents_scope ON markos_control.tool_intents(owner_id,domain,expires_at,intent_id);
CREATE INDEX tool_dispatches_scope ON markos_control.tool_dispatches(owner_id,domain,started_at,dispatch_id);
CREATE FUNCTION markos_control.read_tool_object(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE answer jsonb;BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');PERFORM markos_control.tool_sweep();
 IF p_kind='approvals' THEN
 SELECT jsonb_build_object('schema_version',1,'owner_id',i.owner_id,'domain',i.domain,'requires_human',coalesce((SELECT (h.document->>'require_approval')::boolean FROM markos_control.tool_grant_history h WHERE h.owner_id=i.owner_id AND h.domain=i.domain AND h.revision=i.grant_revision),true),'approval_id',intent_id,'intent_id',intent_id,'intent_sha256',digest,'revision',revision,
 'status',status,'approver_id',approver_id,'dispatch_id',dispatch_id,'expires_at',expires_at,'intent',document,
 'endpoint',(SELECT h.document->>'endpoint' FROM markos_control.tool_grant_history h WHERE h.owner_id=i.owner_id AND h.domain=i.domain AND h.revision=i.grant_revision))
 INTO answer FROM markos_control.tool_intents i WHERE intent_id=p_id AND owner_id=p_owner AND domain=p_domain;
 ELSIF p_kind='dispatches' THEN
 SELECT jsonb_build_object('schema_version',1,'owner_id',owner_id,'domain',domain,'dispatch_id',dispatch_id,'operation_id',operation_id,'intent_id',intent_id,
 'state',state,'revision',revision,'started_at',started_at,'finished_at',finished_at,'result_sha256',result_sha256,'result',result)
 INTO answer FROM markos_control.tool_dispatches WHERE dispatch_id=p_id AND owner_id=p_owner AND domain=p_domain;
 ELSE RAISE EXCEPTION 'invalid object kind';END IF;
 IF answer IS NULL THEN RAISE EXCEPTION 'object unavailable' USING ERRCODE='P0002';END IF;RETURN answer;
END $$;
REVOKE ALL ON FUNCTION markos_control.read_tool_object(uuid,text,uuid,text,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.read_tool_object(uuid,text,uuid,text,text,uuid) TO markos_control_gateway;

CREATE FUNCTION markos_control.tool_capacity(p_owner uuid,p_domain text) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$
 SELECT (SELECT count(*) FROM markos_control.tool_dispatches WHERE reserved)<100
 AND (SELECT count(*) FROM markos_control.tool_dispatches WHERE owner_id=p_owner AND domain=p_domain AND reserved)<20
 AND EXISTS(SELECT 1 FROM markos_control.tool_slots WHERE lease_until IS NULL OR lease_until<=clock_timestamp())
 AND coalesce((SELECT least(2,tokens+greatest(0,extract(epoch FROM clock_timestamp()-at))/6)>=1 FROM markos_control.tool_rates WHERE owner_id=p_owner AND domain=p_domain),true);
$$;
REVOKE ALL ON FUNCTION markos_control.tool_capacity(uuid,text) FROM PUBLIC;
CREATE FUNCTION markos_runtime.defer_tool(p_task uuid,p_fence bigint,p_executor uuid,p_epoch uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$DECLARE t markos_runtime.tasks;BEGIN
 t:=markos_runtime.tool_task(p_task,p_fence,p_executor,p_epoch);
 IF NOT EXISTS(SELECT 1 FROM markos_control.tool_plans p JOIN markos_control.tool_intents i USING(intent_id)
 WHERE p.owner_id=t.owner_id AND p.domain=t.domain AND p.event_id=t.event_id AND p.state='ready' AND i.status='approved')
 OR EXISTS(SELECT 1 FROM markos_control.tool_dispatches WHERE task_id=t.task_id) THEN RAISE EXCEPTION 'only undispatched work may defer' USING ERRCODE='42501';END IF;
 PERFORM markos_control.tool_release(t.task_id,t.fence);
 UPDATE markos_runtime.tasks SET attempts=greatest(0,attempts-1) WHERE task_id=t.task_id;
END $$;
REVOKE ALL ON FUNCTION markos_runtime.defer_tool(uuid,bigint,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_runtime.defer_tool(uuid,bigint,uuid,uuid) TO markos_runtime_client;
