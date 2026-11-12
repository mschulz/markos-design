-- M1-B synthetic persistence. Privileged bootstrap must create markos_control_gateway NOLOGIN.
-- No authentication service, topology provisioning, chat publisher or executor is enabled.
CREATE SCHEMA markos_control;
REVOKE ALL ON SCHEMA markos_control FROM PUBLIC;
GRANT USAGE ON SCHEMA markos_control TO markos_control_gateway;

CREATE TABLE markos_control.gateway_logins(login_role name PRIMARY KEY);
CREATE TABLE markos_control.principals(
 principal_id uuid PRIMARY KEY CHECK(principal_id<>'00000000-0000-0000-0000-000000000000'),
 issuer_id text NOT NULL CHECK(issuer_id='synthetic-issuer'),subject text NOT NULL CHECK(length(subject) BETWEEN 1 AND 256),
 disabled boolean NOT NULL DEFAULT false,UNIQUE(issuer_id,subject)
);
CREATE FUNCTION markos_control.valid_roles(r text[]) RETURNS boolean LANGUAGE sql IMMUTABLE
 SET search_path=pg_catalog AS $$SELECT cardinality(r) BETWEEN 1 AND 4
 AND r<@ARRAY['viewer','operator','approver','administrator']::text[]
 AND array_position(r,NULL) IS NULL AND cardinality(r)=(SELECT count(DISTINCT v) FROM unnest(r) v)$$;
CREATE TABLE markos_control.memberships(
 principal_id uuid REFERENCES markos_control.principals,owner_id uuid NOT NULL,
 domain text NOT NULL CHECK(domain IN ('personal','research','work')),
 revision integer NOT NULL CHECK(revision>0),roles text[] NOT NULL,
 PRIMARY KEY(principal_id,owner_id,domain),CHECK(owner_id<>'00000000-0000-0000-0000-000000000000'),
 CHECK(markos_control.valid_roles(roles))
);
CREATE TABLE markos_control.sessions(
 session_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),principal_id uuid NOT NULL REFERENCES markos_control.principals,
 provider_session_id uuid NOT NULL CHECK(provider_session_id<>'00000000-0000-0000-0000-000000000000'),
 lookup_hash text UNIQUE NOT NULL CHECK(lookup_hash~'^[0-9a-f]{64}$'),kind text NOT NULL CHECK(kind IN ('browser','cli')),
 epoch uuid NOT NULL,created_at timestamptz NOT NULL,last_seen_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL,provider_expires_at timestamptz NOT NULL,revoked_at timestamptz,
 CHECK(expires_at>created_at AND provider_expires_at>created_at)
);
CREATE INDEX control_sessions_active ON markos_control.sessions(principal_id,epoch,expires_at);
CREATE TABLE markos_control.agents(
 owner_id uuid NOT NULL,domain text NOT NULL CHECK(domain IN ('personal','research','work')),
 agent_id text NOT NULL CHECK(agent_id='control-test-agent'),revision integer NOT NULL CHECK(revision>0),
 enabled boolean NOT NULL DEFAULT false,manifest jsonb NOT NULL,
 PRIMARY KEY(owner_id,domain,agent_id)
);
CREATE TABLE markos_control.agent_history(
 owner_id uuid NOT NULL,domain text NOT NULL,agent_id text NOT NULL,revision integer NOT NULL,
 enabled boolean NOT NULL,manifest jsonb NOT NULL,actor_id uuid NOT NULL REFERENCES markos_control.principals,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(owner_id,domain,agent_id,revision)
);
CREATE TABLE markos_control.configurations(
 owner_id uuid NOT NULL,domain text NOT NULL,revision integer NOT NULL CHECK(revision>0),document jsonb NOT NULL,
 PRIMARY KEY(owner_id,domain)
);
CREATE TABLE markos_control.configuration_history(
 owner_id uuid NOT NULL,domain text NOT NULL,revision integer NOT NULL,document jsonb NOT NULL,
 actor_id uuid NOT NULL REFERENCES markos_control.principals,recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,revision)
);
CREATE TABLE markos_control.scope_counters(
 owner_id uuid NOT NULL,domain text NOT NULL,sequence bigint NOT NULL DEFAULT 0,PRIMARY KEY(owner_id,domain)
);
CREATE TABLE markos_control.conversations(
 owner_id uuid NOT NULL,domain text NOT NULL,conversation_id uuid NOT NULL CHECK(conversation_id<>'00000000-0000-0000-0000-000000000000'),
 created_by uuid NOT NULL REFERENCES markos_control.principals,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 ordinal bigint NOT NULL,last_sequence bigint NOT NULL DEFAULT 0,
 PRIMARY KEY(owner_id,domain,conversation_id),UNIQUE(owner_id,domain,ordinal)
);
CREATE TABLE markos_control.messages(
 owner_id uuid NOT NULL,domain text NOT NULL,conversation_id uuid NOT NULL,message_id uuid NOT NULL DEFAULT gen_random_uuid(),
 sequence bigint NOT NULL CHECK(sequence>0),body text NOT NULL CHECK(octet_length(body) BETWEEN 1 AND 16384 AND length(btrim(body))>0),
 body_sha256 text NOT NULL CHECK(body_sha256=encode(sha256(convert_to(body,'UTF8')),'hex')),
 author_id uuid NOT NULL REFERENCES markos_control.principals,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,message_id),UNIQUE(owner_id,domain,conversation_id,sequence),
 FOREIGN KEY(owner_id,domain,conversation_id) REFERENCES markos_control.conversations
);
-- Nonpublishable work proposal. M1-D must atomically promote it to reviewed M0 event/outbox contracts.
CREATE TABLE markos_control.message_intents(
 owner_id uuid NOT NULL,domain text NOT NULL,message_id uuid NOT NULL,event_id uuid NOT NULL DEFAULT gen_random_uuid(),
 actor_id uuid NOT NULL,agent_revision integer NOT NULL,config_revision integer NOT NULL,epoch uuid NOT NULL,
 intended_consumer text NOT NULL DEFAULT 'control-test-agent' CHECK(intended_consumer='control-test-agent'),
 state text NOT NULL DEFAULT 'held' CHECK(state='held'),
 PRIMARY KEY(owner_id,domain,event_id),UNIQUE(owner_id,domain,message_id),
 FOREIGN KEY(owner_id,domain,message_id) REFERENCES markos_control.messages
);
CREATE TABLE markos_control.activity(
 owner_id uuid NOT NULL,domain text NOT NULL,activity_id uuid NOT NULL DEFAULT gen_random_uuid(),sequence bigint NOT NULL,
 conversation_id uuid NOT NULL,object_id uuid NOT NULL,correlation_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind='queued'),recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,sequence),UNIQUE(owner_id,domain,activity_id)
);
CREATE TABLE markos_control.idempotency(
 owner_id uuid NOT NULL,domain text NOT NULL,actor_id uuid NOT NULL,
 operation_key text NOT NULL CHECK(operation_key~'^[a-z0-9][a-z0-9._:-]{0,127}$'),
 kind text NOT NULL CHECK(kind IN ('conversation','message')),request_hash text NOT NULL,receipt jsonb NOT NULL,
 PRIMARY KEY(owner_id,domain,actor_id,operation_key)
);
CREATE TABLE markos_control.audit(
 audit_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),actor_id uuid NOT NULL,owner_id uuid,domain text,
 operation text NOT NULL,object_id uuid NOT NULL,revision integer,recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- No direct gateway table access. Definer entry points independently scope every operation.
DO $$DECLARE t record;BEGIN
 FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='markos_control' LOOP
  EXECUTE format('ALTER TABLE markos_control.%I ENABLE ROW LEVEL SECURITY',t.tablename);
 END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA markos_control FROM PUBLIC,markos_control_gateway;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA markos_control FROM PUBLIC,markos_control_gateway;

CREATE FUNCTION markos_control.gateway() RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$BEGIN
 PERFORM 1 FROM markos_control.gateway_logins WHERE login_role=session_user FOR SHARE;
 IF NOT FOUND
 OR NOT pg_has_role(session_user,'markos_control_gateway','MEMBER')
 OR pg_has_role(session_user,'markos_runtime_client','MEMBER')
 OR EXISTS(SELECT 1 FROM pg_roles elevated WHERE
 (elevated.rolsuper OR elevated.rolbypassrls OR elevated.rolcreaterole OR elevated.rolcreatedb)
 AND pg_has_role(session_user,elevated.oid,'MEMBER'))
 OR EXISTS(SELECT 1 FROM pg_roles r JOIN pg_namespace n ON n.nspname='markos_control'
 WHERE r.rolname=session_user AND (r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb
 OR pg_has_role(session_user,n.nspowner,'MEMBER'))) THEN
 RAISE EXCEPTION 'control access denied' USING ERRCODE='42501';END IF;
END $$;
CREATE FUNCTION markos_control.session_context(p_session uuid,p_hash text) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE s markos_control.sessions; now_at timestamptz:=clock_timestamp();BEGIN
 PERFORM markos_control.gateway();
 -- Same lock as restore generation; serializes authority checks and mutation transactions.
 PERFORM pg_advisory_xact_lock(712093482);
 SELECT * INTO s FROM markos_control.sessions WHERE session_id=p_session AND lookup_hash=p_hash FOR UPDATE;
 now_at:=clock_timestamp();
 IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.epoch IS DISTINCT FROM markos_runtime.current_epoch()
 OR now_at>=s.expires_at OR now_at>=s.provider_expires_at
 OR (s.kind='browser' AND now_at>=s.last_seen_at+interval '30 minutes') THEN
 RAISE EXCEPTION 'control session unavailable' USING ERRCODE='42501';END IF;
 PERFORM 1 FROM markos_control.principals WHERE principal_id=s.principal_id AND NOT disabled FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'control session unavailable' USING ERRCODE='42501';END IF;
 UPDATE markos_control.sessions SET last_seen_at=now_at WHERE session_id=s.session_id;
 RETURN s.principal_id;
END $$;
CREATE FUNCTION markos_control.authorize(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_capability text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$
DECLARE actor uuid; allowed text[];BEGIN
 actor:=markos_control.session_context(p_session,p_hash);
 SELECT roles INTO allowed FROM markos_control.memberships WHERE principal_id=actor AND owner_id=p_owner AND domain=p_domain FOR SHARE;
 IF NOT FOUND OR p_capability IS NULL OR NOT (CASE p_capability
 WHEN 'inspect' THEN allowed&&ARRAY['viewer','operator','approver','administrator']::text[]
 WHEN 'chat' THEN 'operator'=ANY(allowed) WHEN 'configure' THEN 'administrator'=ANY(allowed)
 ELSE false END) THEN RAISE EXCEPTION 'control scope denied' USING ERRCODE='42501';END IF;
 RETURN actor;
END $$;
CREATE FUNCTION markos_control.open_session(p_issuer text,p_subject text,p_provider uuid,p_hash text,p_kind text,p_provider_expires timestamptz)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$
DECLARE actor uuid; fresh uuid; epoch_now uuid; now_at timestamptz:=clock_timestamp(); deadline timestamptz;BEGIN
 PERFORM markos_control.gateway();PERFORM pg_advisory_xact_lock(712093482);
 IF p_issuer IS DISTINCT FROM 'synthetic-issuer' OR p_subject IS NULL OR length(p_subject) NOT BETWEEN 1 AND 256
 OR p_provider IS NULL OR p_provider='00000000-0000-0000-0000-000000000000' OR p_hash IS NULL OR p_hash!~'^[0-9a-f]{64}$'
 OR p_kind IS NULL OR p_kind NOT IN ('browser','cli') OR p_provider_expires IS NULL OR p_provider_expires<=now_at THEN
 RAISE EXCEPTION 'invalid control session' USING ERRCODE='22023';END IF;
 SELECT principal_id INTO actor FROM markos_control.principals WHERE issuer_id=p_issuer AND subject=p_subject AND NOT disabled FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'control principal unavailable' USING ERRCODE='42501';END IF;
 now_at:=clock_timestamp();
 IF p_provider_expires<=now_at THEN RAISE EXCEPTION 'invalid control session' USING ERRCODE='22023';END IF;
 epoch_now:=markos_runtime.current_epoch();
 IF (SELECT count(*) FROM markos_control.sessions WHERE epoch=epoch_now AND revoked_at IS NULL AND expires_at>now_at
 AND provider_expires_at>now_at AND (kind='cli' OR last_seen_at>now_at-interval '30 minutes'))>=100
 OR (SELECT count(*) FROM markos_control.sessions WHERE principal_id=actor AND epoch=epoch_now AND revoked_at IS NULL AND expires_at>now_at
 AND provider_expires_at>now_at AND (kind='cli' OR last_seen_at>now_at-interval '30 minutes'))>=10 THEN
 RAISE EXCEPTION 'control session budget' USING ERRCODE='54000';END IF;
 deadline:=least(p_provider_expires,now_at+CASE p_kind WHEN 'browser' THEN interval '8 hours' ELSE interval '5 minutes' END);
 INSERT INTO markos_control.sessions(principal_id,provider_session_id,lookup_hash,kind,epoch,created_at,last_seen_at,expires_at,provider_expires_at)
 VALUES(actor,p_provider,p_hash,p_kind,epoch_now,now_at,now_at,deadline,p_provider_expires) RETURNING session_id INTO fresh;
 INSERT INTO markos_control.audit(actor_id,operation,object_id) VALUES(actor,'session.open',fresh);
 RETURN jsonb_build_object('session_id',fresh,'principal_id',actor,'epoch',epoch_now,'expires_at',deadline);
END $$;
CREATE FUNCTION markos_control.revoke_session(p_session uuid,p_hash text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE s markos_control.sessions;BEGIN
 PERFORM markos_control.gateway();PERFORM pg_advisory_xact_lock(712093482);
 SELECT * INTO s FROM markos_control.sessions WHERE session_id=p_session AND lookup_hash=p_hash FOR UPDATE;
 IF NOT FOUND OR s.revoked_at IS NOT NULL THEN RETURN false;END IF;
 UPDATE markos_control.sessions SET revoked_at=clock_timestamp() WHERE session_id=p_session;
 INSERT INTO markos_control.audit(actor_id,operation,object_id) VALUES(s.principal_id,'session.revoke',s.session_id);
 RETURN true;
END $$;
CREATE FUNCTION markos_control.rotate_session(p_session uuid,p_old_hash text,p_issuer text,p_subject text,p_provider uuid,p_new_hash text,p_kind text,p_provider_expires timestamptz)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE actor uuid; target uuid;BEGIN
 actor:=markos_control.session_context(p_session,p_old_hash);
 SELECT principal_id INTO target FROM markos_control.principals WHERE issuer_id=p_issuer AND subject=p_subject AND NOT disabled FOR SHARE;
 IF NOT FOUND OR target<>actor THEN RAISE EXCEPTION 'control rotation denied' USING ERRCODE='42501';END IF;
 PERFORM markos_control.revoke_session(p_session,p_old_hash);
 RETURN markos_control.open_session(p_issuer,p_subject,p_provider,p_new_hash,p_kind,p_provider_expires);
END $$;
CREATE FUNCTION markos_control.refresh_session(p_session uuid,p_hash text,p_provider_expires timestamptz)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE actor uuid;s markos_control.sessions;deadline timestamptz;BEGIN
 actor:=markos_control.session_context(p_session,p_hash);
 SELECT * INTO STRICT s FROM markos_control.sessions WHERE session_id=p_session;
 IF s.kind<>'browser' OR p_provider_expires IS NULL OR p_provider_expires<=clock_timestamp() THEN
 RAISE EXCEPTION 'control refresh denied' USING ERRCODE='42501';END IF;
 deadline:=least(p_provider_expires,s.created_at+interval '8 hours');
 UPDATE markos_control.sessions SET provider_expires_at=p_provider_expires,expires_at=deadline WHERE session_id=p_session;
 INSERT INTO markos_control.audit(actor_id,operation,object_id) VALUES(actor,'session.refresh',p_session);
 RETURN jsonb_build_object('session_id',p_session,'principal_id',actor,'epoch',s.epoch,'expires_at',deadline);
END $$;
CREATE FUNCTION markos_control.next_scope_sequence(p_owner uuid,p_domain text) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE value bigint;BEGIN
 INSERT INTO markos_control.scope_counters VALUES(p_owner,p_domain,1) ON CONFLICT(owner_id,domain)
 DO UPDATE SET sequence=markos_control.scope_counters.sequence+1 RETURNING sequence INTO value;
 RETURN value;
END $$;
CREATE FUNCTION markos_control.valid_agent(d jsonb,p_owner uuid,p_domain text) RETURNS boolean LANGUAGE plpgsql IMMUTABLE
 SET search_path=pg_catalog,markos_control AS $$BEGIN
 RETURN jsonb_typeof(d)='object' AND d ?& ARRAY['schema_version','owner_id','domain','agent_id','revision','description','input_events','output_events','required_tools','model_policy','initially_enabled','timeout_seconds','retries']
 AND d-ARRAY['schema_version','owner_id','domain','agent_id','revision','description','input_events','output_events','required_tools','model_policy','initially_enabled','timeout_seconds','retries']='{}'::jsonb
 AND d->>'schema_version'='1' AND jsonb_typeof(d->'schema_version')='number'
 AND d->>'owner_id'=p_owner::text AND d->>'domain'=p_domain AND d->>'agent_id'='control-test-agent'
 AND jsonb_typeof(d->'revision')='number' AND d->>'revision'~'^[1-9][0-9]*$'
 AND jsonb_typeof(d->'description')='string' AND length(d->>'description') BETWEEN 1 AND 512
 AND d->'input_events'='["control.chat.requested"]'::jsonb AND d->'output_events'='["control.chat.completed"]'::jsonb
 AND d->'required_tools'='[]'::jsonb AND d->>'model_policy'='deterministic-no-model'
 AND d->'initially_enabled'='false'::jsonb AND d->>'timeout_seconds'='10' AND jsonb_typeof(d->'timeout_seconds')='number'
 AND d->>'retries'='0' AND jsonb_typeof(d->'retries')='number';
END $$;
CREATE FUNCTION markos_control.save_agent(p_session uuid,p_hash text,p_owner uuid,p_domain text,d jsonb,p_expected integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE actor uuid; old markos_control.agents;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'configure');
 IF NOT coalesce(markos_control.valid_agent(d,p_owner,p_domain),false) OR p_expected IS NULL OR p_expected<0
 OR (d->>'revision')::bigint<>p_expected::bigint+1 THEN RAISE EXCEPTION 'invalid agent revision' USING ERRCODE='22023';END IF;
 SELECT * INTO old FROM markos_control.agents WHERE owner_id=p_owner AND domain=p_domain AND agent_id='control-test-agent' FOR UPDATE;
 IF (NOT FOUND AND p_expected<>0) OR (FOUND AND old.revision<>p_expected) THEN
 RAISE EXCEPTION 'control revision conflict' USING ERRCODE='40001';END IF;
 INSERT INTO markos_control.agents VALUES(p_owner,p_domain,'control-test-agent',p_expected+1,false,d)
 ON CONFLICT(owner_id,domain,agent_id) DO UPDATE SET revision=EXCLUDED.revision,enabled=false,manifest=EXCLUDED.manifest;
 INSERT INTO markos_control.agent_history(owner_id,domain,agent_id,revision,enabled,manifest,actor_id)
 VALUES(p_owner,p_domain,'control-test-agent',p_expected+1,false,d,actor);
 INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id,revision)
 VALUES(actor,p_owner,p_domain,'agent.revision',p_owner,p_expected+1);
 RETURN jsonb_build_object('manifest',d,'enabled',false);
END $$;
CREATE FUNCTION markos_control.set_agent_state(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_expected integer,p_enabled boolean,p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE actor uuid; a markos_control.agents;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'configure');
 IF p_expected IS NULL OR p_expected<1 OR p_enabled IS NULL OR p_reason IS NULL OR length(p_reason) NOT BETWEEN 1 AND 256 THEN
 RAISE EXCEPTION 'invalid state request' USING ERRCODE='22023';END IF;
 SELECT * INTO a FROM markos_control.agents WHERE owner_id=p_owner AND domain=p_domain AND agent_id='control-test-agent' FOR UPDATE;
 IF NOT FOUND OR a.revision<>p_expected THEN RAISE EXCEPTION 'control revision conflict' USING ERRCODE='40001';END IF;
 a.revision:=a.revision+1;a.enabled:=p_enabled;a.manifest:=jsonb_set(a.manifest,'{revision}',to_jsonb(a.revision));
 UPDATE markos_control.agents SET revision=a.revision,enabled=a.enabled,manifest=a.manifest
 WHERE owner_id=p_owner AND domain=p_domain AND agent_id=a.agent_id;
 INSERT INTO markos_control.agent_history(owner_id,domain,agent_id,revision,enabled,manifest,actor_id)
 VALUES(p_owner,p_domain,a.agent_id,a.revision,a.enabled,a.manifest,actor);
 INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id,revision)
 VALUES(actor,p_owner,p_domain,'agent.state',p_owner,a.revision);
 RETURN jsonb_build_object('manifest',a.manifest,'enabled',a.enabled);
END $$;
CREATE FUNCTION markos_control.save_configuration(p_session uuid,p_hash text,p_owner uuid,p_domain text,d jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE actor uuid; old integer; a jsonb; expected bigint;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'configure');
 IF d IS NULL OR jsonb_typeof(d)<>'object' OR NOT(d ?& ARRAY['schema_version','owner_id','domain','revision','previous_revision','agent','reason'])
 OR d-ARRAY['schema_version','owner_id','domain','revision','previous_revision','agent','reason']<>'{}'::jsonb
 OR d->>'schema_version' IS DISTINCT FROM '1' OR jsonb_typeof(d->'schema_version') IS DISTINCT FROM 'number'
 OR d->>'owner_id' IS DISTINCT FROM p_owner::text OR d->>'domain' IS DISTINCT FROM p_domain
 OR jsonb_typeof(d->'revision') IS DISTINCT FROM 'number' OR d->>'revision' !~'^[1-9][0-9]*$'
 OR jsonb_typeof(d->'previous_revision') IS DISTINCT FROM 'number' OR d->>'previous_revision' !~'^(0|[1-9][0-9]*)$'
 OR jsonb_typeof(d->'reason') IS DISTINCT FROM 'string' OR length(d->>'reason') NOT BETWEEN 1 AND 256
 OR NOT coalesce(markos_control.valid_agent(d->'agent',p_owner,p_domain),false) THEN
 RAISE EXCEPTION 'invalid configuration' USING ERRCODE='22023';END IF;
 expected:=(d->>'previous_revision')::bigint;
 IF (d->>'revision')::bigint<>expected+1 THEN RAISE EXCEPTION 'invalid configuration' USING ERRCODE='22023';END IF;
 SELECT manifest INTO a FROM markos_control.agents WHERE owner_id=p_owner AND domain=p_domain AND agent_id='control-test-agent' FOR SHARE;
 IF NOT FOUND OR a<>d->'agent' THEN RAISE EXCEPTION 'control revision conflict' USING ERRCODE='40001';END IF;
 SELECT revision INTO old FROM markos_control.configurations WHERE owner_id=p_owner AND domain=p_domain FOR UPDATE;
 IF (NOT FOUND AND expected<>0) OR (FOUND AND old<>expected) THEN RAISE EXCEPTION 'control revision conflict' USING ERRCODE='40001';END IF;
 INSERT INTO markos_control.configurations VALUES(p_owner,p_domain,expected+1,d)
 ON CONFLICT(owner_id,domain) DO UPDATE SET revision=EXCLUDED.revision,document=EXCLUDED.document;
 INSERT INTO markos_control.configuration_history(owner_id,domain,revision,document,actor_id) VALUES(p_owner,p_domain,expected+1,d,actor);
 INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id,revision) VALUES(actor,p_owner,p_domain,'configuration.revision',p_owner,expected+1);
 RETURN d;
END $$;
CREATE FUNCTION markos_control.create_conversation(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_id uuid,p_key text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$
DECLARE actor uuid; prior markos_control.idempotency; body_hash text; result jsonb; ordinal bigint;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 IF p_id IS NULL OR p_id='00000000-0000-0000-0000-000000000000' OR p_key IS NULL OR p_key!~'^[a-z0-9][a-z0-9._:-]{0,127}$' THEN
 RAISE EXCEPTION 'invalid conversation' USING ERRCODE='22023';END IF;
 body_hash:=encode(sha256(convert_to(p_id::text,'UTF8')),'hex');
 SELECT * INTO prior FROM markos_control.idempotency WHERE owner_id=p_owner AND domain=p_domain AND actor_id=actor AND operation_key=p_key;
 IF FOUND THEN
  IF prior.kind<>'conversation' OR prior.request_hash<>body_hash THEN RAISE EXCEPTION 'control idempotency conflict' USING ERRCODE='40001';END IF;
  RETURN prior.receipt;
 END IF;
 ordinal:=markos_control.next_scope_sequence(p_owner,p_domain);
 INSERT INTO markos_control.conversations(owner_id,domain,conversation_id,created_by,ordinal) VALUES(p_owner,p_domain,p_id,actor,ordinal)
 RETURNING jsonb_build_object('schema_version',1,'owner_id',owner_id,'domain',domain,'conversation_id',conversation_id,'created_by',created_by,'created_at',created_at) INTO result;
 INSERT INTO markos_control.idempotency VALUES(p_owner,p_domain,actor,p_key,'conversation',body_hash,result);
 INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id) VALUES(actor,p_owner,p_domain,'conversation.create',p_id);
 RETURN result;
END $$;
CREATE FUNCTION markos_control.stage_message(p_session uuid,p_hash text,p_owner uuid,p_domain text,d jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$
DECLARE actor uuid; prior markos_control.idempotency; a markos_control.agents; cfg markos_control.configurations;
 conv uuid; msg uuid; event uuid; key text; body_hash text; seq bigint; activity_seq bigint; result jsonb;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 IF d IS NULL OR jsonb_typeof(d)<>'object' OR NOT(d ?& ARRAY['schema_version','domain','conversation_id','operation_key','agent_id','expected_agent_revision','text'])
 OR d-ARRAY['schema_version','domain','conversation_id','operation_key','agent_id','expected_agent_revision','text']<>'{}'::jsonb
 OR d->>'schema_version' IS DISTINCT FROM '1' OR jsonb_typeof(d->'schema_version') IS DISTINCT FROM 'number'
 OR d->>'domain' IS DISTINCT FROM p_domain OR d->>'agent_id' IS DISTINCT FROM 'control-test-agent'
 OR jsonb_typeof(d->'expected_agent_revision') IS DISTINCT FROM 'number' OR d->>'expected_agent_revision' !~'^[1-9][0-9]*$'
 OR jsonb_typeof(d->'conversation_id') IS DISTINCT FROM 'string' OR d->>'conversation_id' !~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
 OR d->>'conversation_id'='00000000-0000-0000-0000-000000000000'
 OR jsonb_typeof(d->'operation_key') IS DISTINCT FROM 'string' OR d->>'operation_key' !~'^[a-z0-9][a-z0-9._:-]{0,127}$'
 OR jsonb_typeof(d->'text') IS DISTINCT FROM 'string' OR octet_length(d->>'text') NOT BETWEEN 1 AND 16384 OR length(btrim(d->>'text'))=0 THEN
 RAISE EXCEPTION 'invalid message request' USING ERRCODE='22023';END IF;
 conv:=(d->>'conversation_id')::uuid;key:=d->>'operation_key';body_hash:=encode(sha256(convert_to(d::text,'UTF8')),'hex');
 SELECT * INTO prior FROM markos_control.idempotency WHERE owner_id=p_owner AND domain=p_domain AND actor_id=actor AND operation_key=key;
 IF FOUND THEN
  IF prior.kind<>'message' OR prior.request_hash<>body_hash THEN RAISE EXCEPTION 'control idempotency conflict' USING ERRCODE='40001';END IF;
  RETURN prior.receipt;
 END IF;
 SELECT * INTO a FROM markos_control.agents WHERE owner_id=p_owner AND domain=p_domain AND agent_id='control-test-agent' FOR SHARE;
 IF NOT FOUND OR NOT a.enabled OR a.revision::text<>d->>'expected_agent_revision' THEN
 RAISE EXCEPTION 'control agent unavailable' USING ERRCODE='40001';END IF;
 SELECT * INTO cfg FROM markos_control.configurations WHERE owner_id=p_owner AND domain=p_domain FOR SHARE;
 IF NOT FOUND OR cfg.document->'agent'<>a.manifest THEN RAISE EXCEPTION 'control configuration unavailable' USING ERRCODE='40001';END IF;
 IF (SELECT count(*) FROM markos_control.message_intents)>=100 OR
 (SELECT count(*) FROM markos_control.message_intents WHERE owner_id=p_owner AND domain=p_domain)>=10 THEN
 RAISE EXCEPTION 'control staging budget' USING ERRCODE='54000';END IF;
 UPDATE markos_control.conversations SET last_sequence=last_sequence+1 WHERE owner_id=p_owner AND domain=p_domain AND conversation_id=conv RETURNING last_sequence INTO seq;
 IF NOT FOUND THEN RAISE EXCEPTION 'control object unavailable' USING ERRCODE='P0002';END IF;
 INSERT INTO markos_control.messages(owner_id,domain,conversation_id,sequence,body,body_sha256,author_id) VALUES(p_owner,p_domain,conv,seq,d->>'text',encode(sha256(convert_to(d->>'text','UTF8')),'hex'),actor) RETURNING message_id INTO msg;
 INSERT INTO markos_control.message_intents(owner_id,domain,message_id,actor_id,agent_revision,config_revision,epoch)
 VALUES(p_owner,p_domain,msg,actor,a.revision,cfg.revision,markos_runtime.current_epoch()) RETURNING event_id INTO event;
 activity_seq:=markos_control.next_scope_sequence(p_owner,p_domain);
 INSERT INTO markos_control.activity(owner_id,domain,sequence,conversation_id,object_id,correlation_id,kind) VALUES(p_owner,p_domain,activity_seq,conv,msg,event,'queued');
 result:=jsonb_build_object('message_id',msg,'event_id',event,'sequence',seq,'state','held','agent_revision',a.revision,'config_revision',cfg.revision);
 INSERT INTO markos_control.idempotency VALUES(p_owner,p_domain,actor,key,'message',body_hash,result);
 INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id) VALUES(actor,p_owner,p_domain,'message.stage',msg);
 RETURN result;
END $$;
CREATE FUNCTION markos_control.read_agents(p_session uuid,p_hash text,p_owner uuid,p_domain text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE result jsonb;BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 SELECT coalesce(jsonb_agg(jsonb_build_object('manifest',manifest,'enabled',enabled)),'[]'::jsonb) INTO result FROM markos_control.agents WHERE owner_id=p_owner AND domain=p_domain;
 RETURN result;
END $$;
CREATE FUNCTION markos_control.read_configuration(p_session uuid,p_hash text,p_owner uuid,p_domain text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE result jsonb;BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 SELECT document INTO result FROM markos_control.configurations WHERE owner_id=p_owner AND domain=p_domain;
 RETURN result;
END $$;
CREATE FUNCTION markos_control.read_conversations(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_after bigint,p_limit integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE result jsonb;BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 IF p_after IS NULL OR p_after<0 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'invalid page' USING ERRCODE='22023';END IF;
 SELECT coalesce(jsonb_agg(item ORDER BY ordinal),'[]'::jsonb) INTO result FROM (
 SELECT ordinal,jsonb_build_object('schema_version',1,'owner_id',owner_id,'domain',domain,'conversation_id',conversation_id,'created_by',created_by,'created_at',created_at,'cursor',ordinal) AS item
 FROM markos_control.conversations WHERE owner_id=p_owner AND domain=p_domain AND ordinal>p_after ORDER BY ordinal LIMIT p_limit) rows;
 RETURN result;
END $$;
CREATE FUNCTION markos_control.read_messages(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_conversation uuid,p_after bigint,p_limit integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE r record; result jsonb:='[]'::jsonb; item jsonb;BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 IF p_after IS NULL OR p_after<0 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'invalid page' USING ERRCODE='22023';END IF;
 PERFORM 1 FROM markos_control.conversations WHERE owner_id=p_owner AND domain=p_domain AND conversation_id=p_conversation;
 IF NOT FOUND THEN RAISE EXCEPTION 'control object unavailable' USING ERRCODE='P0002';END IF;
 FOR r IN SELECT * FROM markos_control.messages WHERE owner_id=p_owner AND domain=p_domain AND conversation_id=p_conversation AND sequence>p_after ORDER BY sequence LIMIT p_limit LOOP
  item:=jsonb_build_object('schema_version',1,'owner_id',r.owner_id,'domain',r.domain,'conversation_id',r.conversation_id,'message_id',r.message_id,'sequence',r.sequence,'author_kind','human','body',r.body,'created_at',r.created_at,'in_reply_to',NULL);
  IF octet_length((result||jsonb_build_array(item))::text)>262144 THEN EXIT;END IF;
  result:=result||jsonb_build_array(item);
 END LOOP;
 RETURN result;
END $$;
CREATE FUNCTION markos_control.read_activity(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_after bigint,p_limit integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE result jsonb;BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 IF p_after IS NULL OR p_after<0 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'invalid page' USING ERRCODE='22023';END IF;
 SELECT coalesce(jsonb_agg(item ORDER BY sequence),'[]'::jsonb) INTO result FROM (
 SELECT sequence,jsonb_build_object('schema_version',1,'owner_id',owner_id,'domain',domain,'activity_id',activity_id,'sequence',sequence,'conversation_id',conversation_id,'object_id',object_id,'correlation_id',correlation_id,'kind',kind,'recorded_at',recorded_at) AS item
 FROM markos_control.activity WHERE owner_id=p_owner AND domain=p_domain AND sequence>p_after ORDER BY sequence LIMIT p_limit) rows;
 RETURN result;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA markos_control FROM PUBLIC,markos_control_gateway;
GRANT EXECUTE ON FUNCTION markos_control.gateway(),markos_control.open_session(text,text,uuid,text,text,timestamptz),
 markos_control.rotate_session(uuid,text,text,text,uuid,text,text,timestamptz),
 markos_control.refresh_session(uuid,text,timestamptz),
 markos_control.revoke_session(uuid,text),markos_control.save_agent(uuid,text,uuid,text,jsonb,integer),
 markos_control.set_agent_state(uuid,text,uuid,text,integer,boolean,text),markos_control.save_configuration(uuid,text,uuid,text,jsonb),
 markos_control.create_conversation(uuid,text,uuid,text,uuid,text),markos_control.stage_message(uuid,text,uuid,text,jsonb),
 markos_control.read_agents(uuid,text,uuid,text),markos_control.read_configuration(uuid,text,uuid,text),
 markos_control.read_conversations(uuid,text,uuid,text,bigint,integer),markos_control.read_messages(uuid,text,uuid,text,uuid,bigint,integer),
 markos_control.read_activity(uuid,text,uuid,text,bigint,integer) TO markos_control_gateway;
