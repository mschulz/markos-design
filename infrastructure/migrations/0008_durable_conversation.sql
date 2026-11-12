-- M1-D: explicit synthetic conversation execution. Prior migrations are immutable.
-- Provision scoped runtime logins and the separate gateway mapping outside this migration.
ALTER TABLE markos_runtime.service_identities DROP CONSTRAINT service_identities_service_id_key;
ALTER TABLE markos_runtime.service_identities ADD UNIQUE(owner_id,domain,service_id);
ALTER TABLE markos_control.messages ALTER COLUMN author_id DROP NOT NULL;
ALTER TABLE markos_control.messages ADD COLUMN author_kind text NOT NULL DEFAULT 'human', ADD COLUMN in_reply_to uuid;
ALTER TABLE markos_control.messages ADD CHECK((author_kind='human' AND author_id IS NOT NULL AND in_reply_to IS NULL)
 OR (author_kind='test-agent' AND author_id IS NULL AND in_reply_to IS NOT NULL AND in_reply_to<>message_id));
ALTER TABLE markos_control.messages ADD FOREIGN KEY(owner_id,domain,in_reply_to) REFERENCES markos_control.messages(owner_id,domain,message_id);
CREATE UNIQUE INDEX one_chat_reply ON markos_control.messages(owner_id,domain,in_reply_to) WHERE author_kind='test-agent';
ALTER TABLE markos_control.message_intents DROP CONSTRAINT message_intents_state_check;
ALTER TABLE markos_control.message_intents ADD CHECK(state IN ('held','promoted'));
ALTER TABLE markos_control.activity DROP CONSTRAINT activity_kind_check;
ALTER TABLE markos_control.activity ADD CHECK(kind IN ('queued','working','succeeded','failed'));
CREATE TABLE markos_control.chat_runs(
 owner_id uuid NOT NULL,domain text NOT NULL,message_id uuid NOT NULL,event_id uuid NOT NULL,
 conversation_id uuid NOT NULL,actor_id uuid NOT NULL,session_id uuid NOT NULL REFERENCES markos_control.sessions,
 epoch uuid NOT NULL,agent_revision integer NOT NULL,config_revision integer NOT NULL,
 state text NOT NULL CHECK(state IN ('queued','working','succeeded','failed','cancelled')),
 reason text,receipt jsonb NOT NULL,reply_id uuid,completion_id uuid,completion_handed_off_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),finished_at timestamptz,
 PRIMARY KEY(owner_id,domain,event_id),UNIQUE(owner_id,domain,message_id),UNIQUE(owner_id,domain,completion_id),
 FOREIGN KEY(owner_id,domain,message_id) REFERENCES markos_control.messages,
 FOREIGN KEY(owner_id,domain,reply_id) REFERENCES markos_control.messages
);
ALTER TABLE markos_control.chat_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_control.chat_runs FROM PUBLIC,markos_control_gateway,markos_runtime_client;

CREATE FUNCTION markos_control.chat_authority(p_owner uuid,p_domain text,p_event uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE r markos_control.chat_runs;s markos_control.sessions;BEGIN
 PERFORM pg_advisory_xact_lock(712093482);
 SELECT * INTO r FROM markos_control.chat_runs WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
 IF NOT FOUND OR r.state NOT IN ('queued','working') OR r.epoch IS DISTINCT FROM markos_runtime.current_epoch() THEN RETURN false;END IF;
 SELECT * INTO s FROM markos_control.sessions WHERE session_id=r.session_id AND principal_id=r.actor_id;
 IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.epoch IS DISTINCT FROM r.epoch OR s.expires_at<=clock_timestamp()
 OR s.provider_expires_at<=clock_timestamp() OR (s.kind='browser' AND s.last_seen_at+interval '30 minutes'<=clock_timestamp()) THEN RETURN false;END IF;
 RETURN EXISTS(SELECT 1 FROM markos_control.principals WHERE principal_id=r.actor_id AND NOT disabled)
 AND EXISTS(SELECT 1 FROM markos_control.memberships WHERE principal_id=r.actor_id AND owner_id=p_owner AND domain=p_domain AND 'operator'=ANY(roles))
 AND EXISTS(SELECT 1 FROM markos_control.agents a JOIN markos_control.configurations c USING(owner_id,domain)
 WHERE a.owner_id=p_owner AND a.domain=p_domain AND a.agent_id='control-test-agent' AND a.enabled
 AND a.revision=r.agent_revision AND c.revision=r.config_revision AND c.document->'agent'=a.manifest);
END $$;

-- Private constructor: callers cannot choose routing, references, consumers or arbitrary output.
CREATE FUNCTION markos_control.chat_event(p_owner uuid,p_domain text,p_event uuid,p_message uuid,p_request uuid DEFAULT NULL)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control SET TimeZone='UTC' AS $$
DECLARE m markos_control.messages;doc jsonb;body bytea;producer text;target text;kind text;BEGIN
 SELECT * INTO STRICT m FROM markos_control.messages WHERE owner_id=p_owner AND domain=p_domain AND message_id=p_message;
 producer:=CASE WHEN p_request IS NULL THEN 'control-gateway' ELSE 'control-test-agent' END;
 target:=CASE WHEN p_request IS NULL THEN 'control-test-agent' ELSE 'control-activity' END;
 kind:=CASE WHEN p_request IS NULL THEN 'control.chat.requested' ELSE 'control.chat.completed' END;
 IF NOT EXISTS(SELECT 1 FROM markos_runtime.service_identities WHERE owner_id=p_owner AND domain=p_domain AND service_id=producer AND can_publish)
 OR NOT EXISTS(SELECT 1 FROM markos_runtime.service_identities WHERE owner_id=p_owner AND domain=p_domain AND service_id=target AND can_consume) THEN
 RAISE EXCEPTION 'conversation transport unavailable' USING ERRCODE='42501';END IF;
 doc:=jsonb_build_object('schema_version',1,'owner_id',p_owner,'domain',p_domain,'event_id',p_event,'event_type',kind,'source',producer,
 'occurred_at',clock_timestamp(),'correlation_id',coalesce(p_request,p_event),'causation_id',p_request,'trace_id',coalesce(p_request,p_event),
 'sensitivity','internal','operation_key',CASE WHEN p_request IS NULL THEN 'chat-request:' ELSE 'chat-complete:' END||replace(coalesce(p_request,p_event)::text,'-',''),
 'expires_at',NULL,'message',jsonb_build_object('schema_version',1,'owner_id',p_owner,'domain',p_domain,'conversation_id',m.conversation_id,
 'message_id',m.message_id,'sha256',m.body_sha256,'kind',CASE WHEN p_request IS NULL THEN 'human-message' ELSE 'assistant-message' END));
 body:=convert_to(doc::text,'UTF8');
 INSERT INTO markos_runtime.events(owner_id,domain,event_id,publisher_id,operation_key,wire,body_hash,operation_hash,occurred_at)
 VALUES(p_owner,p_domain,p_event,producer,doc->>'operation_key',body,encode(sha256(body),'hex'),encode(sha256(convert_to((doc->'message')::text,'UTF8')),'hex'),(doc->>'occurred_at')::timestamptz);
 INSERT INTO markos_runtime.event_consumers(owner_id,domain,event_id,consumer_id) VALUES(p_owner,p_domain,p_event,target);
 INSERT INTO markos_runtime.outbox(owner_id,domain,event_id,publisher_id) VALUES(p_owner,p_domain,p_event,producer);
END $$;
CREATE FUNCTION markos_control.admit_message(p_session uuid,p_hash text,p_owner uuid,p_domain text,d jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$
DECLARE staged jsonb;i markos_control.message_intents;r markos_control.chat_runs;actor uuid;result jsonb;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 staged:=markos_control.stage_message(p_session,p_hash,p_owner,p_domain,d);
 SELECT * INTO r FROM markos_control.chat_runs WHERE owner_id=p_owner AND domain=p_domain AND event_id=(staged->>'event_id')::uuid;
 IF FOUND THEN RETURN r.receipt;END IF;
 SELECT * INTO STRICT i FROM markos_control.message_intents WHERE owner_id=p_owner AND domain=p_domain AND event_id=(staged->>'event_id')::uuid FOR UPDATE;
 IF i.epoch IS DISTINCT FROM markos_runtime.current_epoch() OR i.actor_id<>actor THEN RAISE EXCEPTION 'stale held proposal' USING ERRCODE='40001';END IF;
 result:=staged||jsonb_build_object('state','queued');
 INSERT INTO markos_control.chat_runs(owner_id,domain,message_id,event_id,conversation_id,actor_id,session_id,epoch,agent_revision,config_revision,state,receipt)
 VALUES(p_owner,p_domain,i.message_id,i.event_id,(d->>'conversation_id')::uuid,actor,p_session,i.epoch,i.agent_revision,i.config_revision,'queued',result);
 IF NOT markos_control.chat_authority(p_owner,p_domain,i.event_id) THEN RAISE EXCEPTION 'stale held proposal' USING ERRCODE='40001';END IF;
 PERFORM markos_control.chat_event(p_owner,p_domain,i.event_id,i.message_id);
 UPDATE markos_control.message_intents SET state='promoted' WHERE owner_id=p_owner AND domain=p_domain AND event_id=i.event_id;
 UPDATE markos_control.idempotency SET receipt=result WHERE owner_id=p_owner AND domain=p_domain AND actor_id=actor AND operation_key=d->>'operation_key';
 INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id) VALUES(actor,p_owner,p_domain,'message.admit',i.message_id);
 RETURN result;
END $$;

CREATE FUNCTION markos_control.end_chat(p_owner uuid,p_domain text,p_event uuid,p_state text,p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE r markos_control.chat_runs;ordinal bigint;BEGIN
 SELECT * INTO r FROM markos_control.chat_runs WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event FOR UPDATE;
 IF NOT FOUND OR r.state NOT IN ('queued','working') THEN RETURN;END IF;
 IF p_state NOT IN ('failed','cancelled') OR p_reason NOT IN ('authority','cancelled','execution','timeout','dependency','abandoned') THEN RAISE EXCEPTION 'invalid terminal state';END IF;
 UPDATE markos_control.chat_runs SET state=p_state,reason=p_reason,finished_at=clock_timestamp() WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
 UPDATE markos_runtime.execution_slots SET task_id=NULL,fence=NULL,lease_until=NULL WHERE task_id IN
 (SELECT task_id FROM markos_runtime.tasks WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event);
 UPDATE markos_runtime.task_attempts SET outcome='cancelled',finished_at=clock_timestamp() WHERE outcome='running' AND task_id IN
 (SELECT task_id FROM markos_runtime.tasks WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event);
 UPDATE markos_runtime.tasks SET state='failed',fence=fence+1,lease_until=NULL,executor_id=NULL,slot_id=NULL WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
 UPDATE markos_runtime.event_consumers SET cancelled_at=clock_timestamp() WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event AND handed_off_at IS NULL;
 UPDATE markos_runtime.outbox SET state='review',fence=fence+1,lease_until=NULL,last_failure='control_unavailable' WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event AND state<>'published';
 DELETE FROM markos_control.message_intents WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
 ordinal:=markos_control.next_scope_sequence(p_owner,p_domain);
 INSERT INTO markos_control.activity(owner_id,domain,sequence,conversation_id,object_id,correlation_id,kind) VALUES(p_owner,p_domain,ordinal,r.conversation_id,r.message_id,p_event,'failed');
 INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id) VALUES(r.actor_id,p_owner,p_domain,'message.'||p_state,r.message_id);
END $$;
CREATE FUNCTION markos_control.chat_status(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_conversation uuid,p_message uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE r markos_control.chat_runs;BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 SELECT * INTO r FROM markos_control.chat_runs WHERE owner_id=p_owner AND domain=p_domain AND conversation_id=p_conversation AND message_id=p_message;
 IF NOT FOUND THEN RAISE EXCEPTION 'object unavailable' USING ERRCODE='P0002';END IF;
 RETURN jsonb_build_object('message_id',r.message_id,'event_id',r.event_id,'state',r.state,'reason',r.reason,'reply_id',r.reply_id,'completion_id',r.completion_id);
END $$;
CREATE FUNCTION markos_control.cancel_chat(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_conversation uuid,p_message uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE r markos_control.chat_runs;actor uuid;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 SELECT * INTO r FROM markos_control.chat_runs WHERE owner_id=p_owner AND domain=p_domain AND conversation_id=p_conversation AND message_id=p_message AND actor_id=actor FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'object unavailable' USING ERRCODE='P0002';END IF;
 PERFORM markos_control.end_chat(p_owner,p_domain,r.event_id,'cancelled','cancelled');
 RETURN markos_control.chat_status(p_session,p_hash,p_owner,p_domain,p_conversation,p_message);
END $$;

-- Runtime identity is separately provisioned and scoped; no gateway tokens or grants go to agents.
CREATE FUNCTION markos_runtime.chat_identity(p_service text) RETURNS markos_runtime.service_identities
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$DECLARE p markos_runtime.service_identities;BEGIN
 IF p_service NOT IN ('control-gateway','control-test-agent','control-activity') OR p_service IS NULL THEN RAISE EXCEPTION 'invalid chat service';END IF;
 IF EXISTS(SELECT 1 FROM pg_roles elevated WHERE (elevated.rolsuper OR elevated.rolbypassrls OR elevated.rolcreaterole OR elevated.rolcreatedb
 OR elevated.oid IN (SELECT nspowner FROM pg_namespace WHERE nspname IN ('markos_runtime','markos_control')))
 AND pg_has_role(session_user,elevated.oid,'MEMBER')) THEN RAISE EXCEPTION 'invalid runtime role' USING ERRCODE='42501';END IF;
 SELECT * INTO p FROM markos_runtime.service_identities WHERE login_role=session_user AND service_id=p_service;
 IF NOT FOUND OR NOT pg_has_role(session_user,'markos_runtime_client','MEMBER') THEN RAISE EXCEPTION 'chat identity unavailable' USING ERRCODE='42501';END IF;
 RETURN p;
END $$;

-- Gate transport/recovery against current durable human and version authority.
ALTER TABLE markos_runtime.recovery_reviews DROP CONSTRAINT recovery_reviews_reason_check;
ALTER TABLE markos_runtime.recovery_reviews ADD CHECK(reason IN ('aged','deleted','retired','expired','source_unavailable','checksum','approval_expired','control_unavailable'));
ALTER TABLE markos_runtime.execution_reviews DROP CONSTRAINT execution_reviews_reason_check;
ALTER TABLE markos_runtime.execution_reviews ADD CHECK(reason IN ('expired','source_unavailable','retry_exhausted','unsupported','aged','deleted','retired','checksum','approval_expired','control_unavailable'));
ALTER FUNCTION markos_runtime.event_gate(uuid,text,uuid,text,boolean) RENAME TO event_gate_m0e;
CREATE FUNCTION markos_runtime.event_gate(p_owner uuid,p_domain text,p_event uuid,p_service text,p_allow_age boolean DEFAULT false) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$DECLARE doc jsonb;r markos_control.chat_runs;why text;BEGIN
 why:=markos_runtime.event_gate_m0e(p_owner,p_domain,p_event,p_service,p_allow_age);
 IF why IS NOT NULL THEN RETURN why;END IF;
 SELECT convert_from(wire,'UTF8')::jsonb INTO doc FROM markos_runtime.events WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
 IF doc->>'event_type' NOT IN ('control.chat.requested','control.chat.completed') THEN RETURN NULL;END IF;
 SELECT * INTO r FROM markos_control.chat_runs WHERE owner_id=p_owner AND domain=p_domain AND (event_id=p_event OR completion_id=p_event);
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM markos_control.messages m WHERE m.owner_id=p_owner AND m.domain=p_domain
 AND m.message_id=(doc->'message'->>'message_id')::uuid AND m.body_sha256=doc->'message'->>'sha256') THEN RETURN 'control_unavailable';END IF;
 IF doc->>'event_type'='control.chat.completed' THEN
  IF r.state='succeeded' THEN RETURN NULL;END IF;RETURN 'control_unavailable';
 END IF;
 IF r.state='succeeded' THEN RETURN NULL;END IF;
 IF NOT markos_control.chat_authority(p_owner,p_domain,r.event_id) THEN
  PERFORM markos_control.end_chat(p_owner,p_domain,r.event_id,'failed','authority');RETURN 'control_unavailable';
 END IF;
 RETURN NULL;
END $$;
-- Existing no-op appends never fan out to newly provisioned chat consumers.
ALTER FUNCTION markos_runtime.append_event(bytea) RENAME TO append_event_m0e;
CREATE FUNCTION markos_runtime.append_event(p_wire bytea) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_runtime AS $$DECLARE status text;doc jsonb;eid uuid;p markos_runtime.service_identities;BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_publish;
 IF p.service_id IN ('control-gateway','control-test-agent','control-activity') THEN RAISE EXCEPTION 'typed chat entry required';END IF;
 status:=markos_runtime.append_event_m0e(p_wire);
 IF status='created' THEN
  doc:=convert_from(p_wire,'UTF8')::jsonb;eid:=(doc->>'event_id')::uuid;
  DELETE FROM markos_runtime.event_consumers WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=eid AND consumer_id IN ('control-test-agent','control-activity');
  IF NOT EXISTS(SELECT 1 FROM markos_runtime.event_consumers WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=eid) THEN RAISE EXCEPTION 'no intended no-op consumer';END IF;
 END IF;
 RETURN status;
END $$;
REVOKE ALL ON FUNCTION markos_runtime.append_event_m0e(bytea) FROM PUBLIC,markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_runtime.append_event(bytea) TO markos_runtime_client;

-- Completion subscriptions commit only a durable activity receipt; no executable agent task.
ALTER FUNCTION markos_runtime.handoff(uuid,text) RENAME TO handoff_m0e;
CREATE FUNCTION markos_runtime.handoff(p_event uuid,p_hash text) RETURNS TABLE(status text,task_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$DECLARE p markos_runtime.service_identities;r record;doc jsonb;BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_consume;
 SELECT convert_from(wire,'UTF8')::jsonb INTO doc FROM markos_runtime.events WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event;
 IF doc->>'event_type' IN ('control.chat.requested','control.chat.completed') THEN PERFORM markos_runtime.chat_identity(p.service_id);END IF;
 FOR r IN SELECT * FROM markos_runtime.handoff_m0e(p_event,p_hash) LOOP
  IF doc->>'event_type'='control.chat.completed' AND r.status IN ('created','duplicate') THEN
   UPDATE markos_runtime.tasks x SET state='completed' WHERE x.task_id=r.task_id;
   INSERT INTO markos_runtime.task_results(owner_id,domain,consumer_id,task_id,result) VALUES(p.owner_id,p.domain,p.service_id,r.task_id,jsonb_build_object('kind','control.activity','effects','none')) ON CONFLICT DO NOTHING;
   UPDATE markos_control.chat_runs SET completion_handed_off_at=coalesce(completion_handed_off_at,clock_timestamp()) WHERE owner_id=p.owner_id AND domain=p.domain AND completion_id=p_event;
  END IF;
  RETURN QUERY SELECT r.status::text,r.task_id::uuid;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION markos_runtime.handoff_m0e(uuid,text) FROM PUBLIC,markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_runtime.handoff(uuid,text) TO markos_runtime_client;

CREATE FUNCTION markos_runtime.claim_chat(p_executor uuid,p_seconds integer) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_runtime AS $$DECLARE p markos_runtime.service_identities;t markos_runtime.tasks;r markos_control.chat_runs;m markos_control.messages;s markos_control.sessions;subject text;chosen integer;deadline timestamptz;ordinal bigint;BEGIN
 p:=markos_runtime.chat_identity('control-test-agent');
 IF NOT p.can_consume OR NOT p.can_execute OR p_executor IS NULL OR p_executor='00000000-0000-0000-0000-000000000000' OR p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 30 THEN RAISE EXCEPTION 'invalid chat claim';END IF;
 PERFORM pg_advisory_xact_lock(712093482);
 FOR t IN SELECT * FROM markos_runtime.tasks WHERE owner_id=p.owner_id AND domain=p.domain AND consumer_id=p.service_id
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
CREATE FUNCTION markos_runtime.finish_chat(p_task uuid,p_fence bigint,p_executor uuid,p_epoch uuid,p_reply text,p_failure text DEFAULT NULL) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$DECLARE p markos_runtime.service_identities;t markos_runtime.tasks;r markos_control.chat_runs;m markos_control.messages;reply uuid;completion uuid;seq bigint;ordinal bigint;BEGIN
 p:=markos_runtime.chat_identity('control-test-agent');
 IF NOT markos_runtime.valid_epoch(p_epoch) THEN RETURN false;END IF;
 SELECT * INTO t FROM markos_runtime.tasks WHERE task_id=p_task AND owner_id=p.owner_id AND domain=p.domain AND consumer_id=p.service_id
 AND state='running' AND fence=p_fence AND executor_id=p_executor AND lease_until>clock_timestamp() FOR UPDATE;
 IF NOT FOUND OR NOT p.can_execute OR NOT p.can_consume THEN RETURN false;END IF;
 PERFORM 1 FROM markos_runtime.execution_slots WHERE slot_id=t.slot_id AND task_id=t.task_id AND fence=t.fence AND lease_until>clock_timestamp();
 IF NOT FOUND THEN RETURN false;END IF;
 SELECT * INTO STRICT r FROM markos_control.chat_runs WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=t.event_id;
 IF markos_runtime.event_gate(p.owner_id,p.domain,t.event_id,p.service_id) IS NOT NULL OR NOT markos_control.chat_authority(p.owner_id,p.domain,t.event_id) THEN PERFORM markos_control.end_chat(p.owner_id,p.domain,t.event_id,'failed','authority');RETURN false;END IF;
 IF p_failure IS NOT NULL THEN PERFORM markos_control.end_chat(p.owner_id,p.domain,t.event_id,'failed',p_failure);RETURN false;END IF;
 SELECT * INTO STRICT m FROM markos_control.messages WHERE owner_id=p.owner_id AND domain=p.domain AND message_id=r.message_id;
 IF p_reply IS DISTINCT FROM 'Synthetic acknowledgement: '||m.body_sha256 THEN RAISE EXCEPTION 'invalid deterministic reply';END IF;
 UPDATE markos_control.conversations SET last_sequence=last_sequence+1 WHERE owner_id=p.owner_id AND domain=p.domain AND conversation_id=r.conversation_id RETURNING last_sequence INTO seq;
 INSERT INTO markos_control.messages(owner_id,domain,conversation_id,sequence,body,body_sha256,author_id,author_kind,in_reply_to)
 VALUES(p.owner_id,p.domain,r.conversation_id,seq,p_reply,encode(sha256(convert_to(p_reply,'UTF8')),'hex'),NULL,'test-agent',r.message_id) RETURNING message_id INTO reply;
 completion:=gen_random_uuid();
 PERFORM markos_control.chat_event(p.owner_id,p.domain,completion,reply,r.event_id);
 ordinal:=markos_control.next_scope_sequence(p.owner_id,p.domain);
 INSERT INTO markos_control.activity(owner_id,domain,sequence,conversation_id,object_id,correlation_id,kind) VALUES(p.owner_id,p.domain,ordinal,r.conversation_id,reply,r.event_id,'succeeded');
 INSERT INTO markos_runtime.task_results(owner_id,domain,consumer_id,task_id,result) VALUES(p.owner_id,p.domain,p.service_id,t.task_id,jsonb_build_object('kind','control.chat','effects','none','reply_id',reply,'completion_id',completion));
 UPDATE markos_control.chat_runs SET state='succeeded',reply_id=reply,completion_id=completion,finished_at=clock_timestamp() WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=r.event_id;
 DELETE FROM markos_control.message_intents WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=r.event_id;
 UPDATE markos_runtime.tasks SET state='completed',lease_until=NULL,executor_id=NULL,slot_id=NULL WHERE task_id=t.task_id;
 UPDATE markos_runtime.task_attempts SET outcome='completed',finished_at=clock_timestamp() WHERE task_id=t.task_id AND fence=t.fence;
 UPDATE markos_runtime.execution_slots SET task_id=NULL,fence=NULL,lease_until=NULL WHERE slot_id=t.slot_id;
 INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id) VALUES(r.actor_id,p.owner_id,p.domain,'message.complete',reply);
 PERFORM markos_runtime.refresh_terminal(p.owner_id,p.domain);
 RETURN true;
END $$;
-- Old generic no-op finalizers cannot claim or complete a typed conversation.
ALTER FUNCTION markos_runtime.claim_task(uuid,integer) RENAME TO claim_task_m0e;
CREATE FUNCTION markos_runtime.claim_task(p_executor uuid,p_seconds integer) RETURNS TABLE(task_id uuid,wire bytea,fence bigint,attempt integer,lease_until timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 IF EXISTS(SELECT 1 FROM markos_runtime.service_identities WHERE login_role=session_user AND service_id IN ('control-test-agent','control-activity')) THEN RAISE EXCEPTION 'typed chat claim required';END IF;
 RETURN QUERY SELECT * FROM markos_runtime.claim_task_m0e(p_executor,p_seconds);
END $$;
ALTER FUNCTION markos_runtime.finish_task(uuid,bigint,uuid,text,integer) RENAME TO finish_task_m0e;
CREATE FUNCTION markos_runtime.finish_task(p_task uuid,p_fence bigint,p_executor uuid,p_failure text DEFAULT NULL,p_delay integer DEFAULT 1) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 IF EXISTS(SELECT 1 FROM markos_runtime.service_identities WHERE login_role=session_user AND service_id IN ('control-test-agent','control-activity')) THEN RAISE EXCEPTION 'typed chat completion required';END IF;
 RETURN markos_runtime.finish_task_m0e(p_task,p_fence,p_executor,p_failure,p_delay);
END $$;
REVOKE ALL ON FUNCTION markos_runtime.claim_task_m0e(uuid,integer),markos_runtime.finish_task_m0e(uuid,bigint,uuid,text,integer) FROM PUBLIC,markos_runtime_client;

ALTER FUNCTION markos_runtime.renew_task(uuid,bigint,uuid,integer) RENAME TO renew_task_m0e;
CREATE FUNCTION markos_runtime.renew_task(p_task uuid,p_fence bigint,p_executor uuid,p_seconds integer) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 IF EXISTS(SELECT 1 FROM markos_runtime.service_identities WHERE login_role=session_user AND service_id IN ('control-test-agent','control-activity')) THEN RAISE EXCEPTION 'conversation leases are not renewable';END IF;
 RETURN markos_runtime.renew_task_m0e(p_task,p_fence,p_executor,p_seconds);
END $$;
REVOKE ALL ON FUNCTION markos_runtime.renew_task_m0e(uuid,bigint,uuid,integer) FROM PUBLIC,markos_runtime_client;

-- Share the existing four identity-operation slots between API and synthetic worker.
CREATE FUNCTION markos_control.identity_claim() RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE chosen integer;fresh uuid:=gen_random_uuid();BEGIN
 PERFORM pg_advisory_xact_lock(712093482);
 IF markos_runtime.current_epoch() IS NULL THEN RAISE EXCEPTION 'authority unavailable';END IF;
 SELECT slot INTO chosen FROM markos_control.api_auth_slots WHERE lease_until IS NULL OR lease_until<=clock_timestamp() OR epoch IS DISTINCT FROM markos_runtime.current_epoch() ORDER BY slot FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL;END IF;
 UPDATE markos_control.api_auth_slots SET attempt=fresh,lease_until=clock_timestamp()+interval '5 seconds',epoch=markos_runtime.current_epoch() WHERE slot=chosen;RETURN fresh;
END $$;
CREATE OR REPLACE FUNCTION markos_control.api_auth_claim() RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$BEGIN PERFORM markos_control.gateway();RETURN markos_control.identity_claim();END $$;
CREATE FUNCTION markos_runtime.chat_auth_claim() RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN PERFORM markos_runtime.chat_identity('control-test-agent');RETURN markos_control.identity_claim();END $$;
CREATE FUNCTION markos_runtime.chat_auth_release(p_attempt uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 PERFORM markos_runtime.chat_identity('control-test-agent');UPDATE markos_control.api_auth_slots SET attempt=NULL,lease_until=NULL WHERE attempt=p_attempt;END $$;

-- Read both human and deterministic assistant messages, still scoped before bounded pagination.
CREATE OR REPLACE FUNCTION markos_control.read_messages(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_conversation uuid,p_after bigint,p_limit integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE r record;result jsonb:='[]'::jsonb;item jsonb;BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 IF p_after IS NULL OR p_after<0 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'invalid page' USING ERRCODE='22023';END IF;
 IF NOT EXISTS(SELECT 1 FROM markos_control.conversations WHERE owner_id=p_owner AND domain=p_domain AND conversation_id=p_conversation) THEN RAISE EXCEPTION 'object unavailable' USING ERRCODE='P0002';END IF;
 FOR r IN SELECT * FROM markos_control.messages WHERE owner_id=p_owner AND domain=p_domain AND conversation_id=p_conversation AND sequence>p_after ORDER BY sequence LIMIT p_limit LOOP
  item:=jsonb_build_object('schema_version',1,'owner_id',r.owner_id,'domain',r.domain,'conversation_id',r.conversation_id,'message_id',r.message_id,'sequence',r.sequence,'author_kind',r.author_kind,'body',r.body,'created_at',r.created_at,'in_reply_to',r.in_reply_to);
  IF octet_length((result||jsonb_build_array(item))::text)>262144 THEN EXIT;END IF;result:=result||jsonb_build_array(item);
 END LOOP;
 RETURN result;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA markos_control FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA markos_runtime FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.admit_message(uuid,text,uuid,text,jsonb),markos_control.chat_status(uuid,text,uuid,text,uuid,uuid),markos_control.cancel_chat(uuid,text,uuid,text,uuid,uuid) TO markos_control_gateway;
GRANT EXECUTE ON FUNCTION markos_runtime.claim_chat(uuid,integer),markos_runtime.finish_chat(uuid,bigint,uuid,uuid,text,text),markos_runtime.chat_auth_claim(),markos_runtime.chat_auth_release(uuid) TO markos_runtime_client;
