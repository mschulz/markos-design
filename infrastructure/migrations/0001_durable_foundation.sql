-- Explicit migration-role execution only; never worker/preflight startup.
-- Bootstrap must provide the NOLOGIN markos_runtime_client group separately.
REVOKE ALL ON SCHEMA markos_runtime FROM PUBLIC;
GRANT USAGE ON SCHEMA markos_runtime TO markos_runtime_client;

CREATE TABLE markos_runtime.service_identities (
    login_role name PRIMARY KEY,
    service_id text UNIQUE NOT NULL CHECK (service_id ~ '^[a-z][a-z0-9.-]{0,63}$'),
    owner_id uuid NOT NULL CHECK (owner_id <> '00000000-0000-0000-0000-000000000000'),
    domain text NOT NULL CHECK (domain IN ('personal','research','work')),
    can_publish boolean NOT NULL DEFAULT false,
    can_consume boolean NOT NULL DEFAULT false,
    CHECK (can_publish OR can_consume)
);
REVOKE ALL ON markos_runtime.service_identities FROM PUBLIC, markos_runtime_client;

CREATE TABLE markos_runtime.events (
    owner_id uuid NOT NULL,
    domain text NOT NULL CHECK (domain IN ('personal','research','work')),
    event_id uuid NOT NULL CHECK (event_id <> '00000000-0000-0000-0000-000000000000'),
    publisher_id text NOT NULL,
    operation_key text NOT NULL CHECK (length(operation_key) BETWEEN 1 AND 128),
    wire bytea NOT NULL CHECK (octet_length(wire) BETWEEN 1 AND 65536),
    body_hash text NOT NULL CHECK (length(body_hash)=64),
    operation_hash text NOT NULL CHECK (length(operation_hash)=64),
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (owner_id, domain, event_id)
);
CREATE TABLE markos_runtime.event_consumers (
    owner_id uuid NOT NULL, domain text NOT NULL, event_id uuid NOT NULL,
    consumer_id text NOT NULL,
    handed_off_at timestamptz,
    PRIMARY KEY (owner_id,domain,event_id,consumer_id),
    FOREIGN KEY (owner_id,domain,event_id) REFERENCES markos_runtime.events
);
CREATE TABLE markos_runtime.outbox (
    owner_id uuid NOT NULL, domain text NOT NULL, event_id uuid NOT NULL,
    publisher_id text NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','leased','published')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    fence bigint NOT NULL DEFAULT 0,
    lease_until timestamptz,
    published_at timestamptz,
    PRIMARY KEY (owner_id,domain,event_id),
    FOREIGN KEY (owner_id,domain,event_id) REFERENCES markos_runtime.events,
    CHECK ((state='leased') = (lease_until IS NOT NULL))
);
CREATE INDEX outbox_pending ON markos_runtime.outbox (state,lease_until);
CREATE TABLE markos_runtime.tasks (
    owner_id uuid NOT NULL, domain text NOT NULL, consumer_id text NOT NULL,
    task_id uuid NOT NULL DEFAULT gen_random_uuid(),
    event_id uuid NOT NULL,
    operation_key text NOT NULL, operation_hash text NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','running','completed','failed')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (owner_id,domain,consumer_id,task_id),
    UNIQUE (owner_id,domain,consumer_id,operation_key),
    FOREIGN KEY (owner_id,domain,event_id) REFERENCES markos_runtime.events
);
CREATE TABLE markos_runtime.inbox (
    owner_id uuid NOT NULL, domain text NOT NULL, consumer_id text NOT NULL, event_id uuid NOT NULL,
    task_id uuid NOT NULL, received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (owner_id,domain,consumer_id,event_id),
    FOREIGN KEY (owner_id,domain,event_id,consumer_id) REFERENCES markos_runtime.event_consumers,
    FOREIGN KEY (owner_id,domain,consumer_id,task_id) REFERENCES markos_runtime.tasks
);
CREATE TABLE markos_runtime.task_results (
    owner_id uuid NOT NULL, domain text NOT NULL, consumer_id text NOT NULL, task_id uuid NOT NULL,
    result jsonb NOT NULL CHECK (octet_length(result::text) <= 65536),
    completed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (owner_id,domain,consumer_id,task_id),
    FOREIGN KEY (owner_id,domain,consumer_id,task_id) REFERENCES markos_runtime.tasks
);
CREATE TABLE markos_runtime.quarantine (
    owner_id uuid NOT NULL, domain text NOT NULL, service_id text NOT NULL, event_id uuid NOT NULL,
    incoming_hash text NOT NULL CHECK(length(incoming_hash)=64),
    reason text NOT NULL CHECK(reason IN ('event_conflict','operation_conflict','expired')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(owner_id,domain,service_id,event_id,incoming_hash,reason)
);

-- session_user is the authenticated login. SET ROLE/custom session variables
-- cannot select another principal; runtime cannot read/edit this registry.
CREATE FUNCTION markos_runtime.can_read(p_owner uuid,p_domain text,p_service text DEFAULT NULL)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, markos_runtime
AS $$
 SELECT EXISTS (SELECT 1 FROM markos_runtime.service_identities i
 WHERE i.login_role=session_user AND i.owner_id=p_owner AND i.domain=p_domain
 AND (p_service IS NULL OR i.service_id=p_service))
$$;
CREATE FUNCTION markos_runtime.is_publisher(p_owner uuid,p_domain text,p_publisher text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, markos_runtime
AS $$
 SELECT EXISTS(SELECT 1 FROM markos_runtime.service_identities i
 WHERE i.login_role=session_user AND i.owner_id=p_owner AND i.domain=p_domain
 AND i.service_id=p_publisher AND i.can_publish)
$$;
CREATE FUNCTION markos_runtime.can_read_dispatch(p_owner uuid,p_domain text,p_consumer text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, markos_runtime
AS $$
 SELECT EXISTS(SELECT 1 FROM markos_runtime.service_identities i
 WHERE i.login_role=session_user AND i.owner_id=p_owner AND i.domain=p_domain
 AND (i.can_publish OR (i.can_consume AND i.service_id=p_consumer)))
$$;

ALTER TABLE markos_runtime.events ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.events TO markos_runtime_client
 USING(markos_runtime.can_read(owner_id,domain));
ALTER TABLE markos_runtime.event_consumers ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.event_consumers TO markos_runtime_client
 USING(markos_runtime.can_read_dispatch(owner_id,domain,consumer_id));
ALTER TABLE markos_runtime.outbox ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.outbox TO markos_runtime_client
 USING(markos_runtime.is_publisher(owner_id,domain,publisher_id));
ALTER TABLE markos_runtime.tasks ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.tasks TO markos_runtime_client
 USING(markos_runtime.can_read(owner_id,domain,consumer_id));
ALTER TABLE markos_runtime.inbox ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.inbox TO markos_runtime_client
 USING(markos_runtime.can_read(owner_id,domain,consumer_id));
ALTER TABLE markos_runtime.task_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.task_results TO markos_runtime_client
 USING(markos_runtime.can_read(owner_id,domain,consumer_id));
ALTER TABLE markos_runtime.quarantine ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.quarantine TO markos_runtime_client
 USING(markos_runtime.can_read(owner_id,domain,service_id));

-- Writes are only through scoped, fixed-search-path functions. Direct DML,
-- TRUNCATE, grants, identity registration and migration access are not granted.
GRANT SELECT ON markos_runtime.events,markos_runtime.event_consumers,markos_runtime.outbox,
 markos_runtime.tasks,markos_runtime.inbox,markos_runtime.task_results,markos_runtime.quarantine
 TO markos_runtime_client;

-- SQL callers cannot bypass wire/schema scope checks by skipping the Python API.
CREATE FUNCTION markos_runtime.unique_json_keys(doc json)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,markos_runtime
AS $$
DECLARE child json;
BEGIN
 IF json_typeof(doc)='object' THEN
  IF EXISTS(SELECT key FROM json_each(doc) GROUP BY key HAVING count(*)>1) THEN RETURN false; END IF;
  FOR child IN SELECT value FROM json_each(doc) LOOP
   IF NOT markos_runtime.unique_json_keys(child) THEN RETURN false; END IF;
  END LOOP;
 ELSIF json_typeof(doc)='array' THEN
  FOR child IN SELECT value FROM json_array_elements(doc) LOOP
   IF NOT markos_runtime.unique_json_keys(child) THEN RETURN false; END IF;
  END LOOP;
 END IF;
 RETURN true;
END $$;
CREATE FUNCTION markos_runtime.valid_envelope(doc jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,markos_runtime
AS $$
DECLARE key text; ident uuid; ref jsonb;
 required text[]:=ARRAY['schema_version','event_id','event_type','owner_id','domain','source',
 'occurred_at','correlation_id','causation_id','trace_id','sensitivity','operation_key'];
 allowed text[]:=required||ARRAY['expires_at','payload','payload_ref'];
BEGIN
 IF jsonb_typeof(doc)<>'object' OR NOT(doc ?& required) OR doc-allowed<>'{}'::jsonb
 THEN RETURN false; END IF;
 FOREACH key IN ARRAY ARRAY['event_id','event_type','owner_id','domain','source','occurred_at',
 'correlation_id','trace_id','sensitivity','operation_key'] LOOP
  IF jsonb_typeof(doc->key) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
 END LOOP;
 IF jsonb_typeof(doc->'schema_version')<>'number' OR doc->>'schema_version'<>'1'
 OR doc->>'event_type'<>'foundation.noop.requested'
 OR doc->>'domain' NOT IN ('personal','research','work')
 OR doc->>'source' !~ '^[a-z][a-z0-9]*([.-][a-z0-9]+)*$' OR length(doc->>'source')>64
 OR doc->>'operation_key' !~ '^[a-z0-9][a-z0-9._:-]*$' OR length(doc->>'operation_key')>128
 OR doc->>'sensitivity' NOT IN ('public','internal','confidential','restricted')
 THEN RETURN false; END IF;
 FOREACH key IN ARRAY ARRAY['event_id','owner_id','correlation_id','trace_id'] LOOP
  ident:=(doc->>key)::uuid;
  IF ident='00000000-0000-0000-0000-000000000000' THEN RETURN false; END IF;
 END LOOP;
 IF jsonb_typeof(doc->'causation_id')='string' THEN
  ident:=(doc->>'causation_id')::uuid;
  IF ident='00000000-0000-0000-0000-000000000000' THEN RETURN false; END IF;
 ELSIF jsonb_typeof(doc->'causation_id')<>'null' THEN RETURN false; END IF;
 IF doc->>'occurred_at' !~ '[Tt].*(Z|[+]00:00)$' THEN RETURN false; END IF;
 PERFORM (doc->>'occurred_at')::timestamptz;
 IF doc ? 'expires_at' AND jsonb_typeof(doc->'expires_at')<>'null' THEN
  IF jsonb_typeof(doc->'expires_at')<>'string' OR doc->>'expires_at' !~ '[Tt].*(Z|[+]00:00)$'
  OR (doc->>'expires_at')::timestamptz <= (doc->>'occurred_at')::timestamptz
  THEN RETURN false; END IF;
 END IF;
 IF ((jsonb_typeof(doc->'payload')='object') IS TRUE)
    = ((jsonb_typeof(doc->'payload_ref')='object') IS TRUE) THEN RETURN false; END IF;
 IF jsonb_typeof(doc->'payload')='object' THEN
  IF (doc->'payload')-'label'<>'{}'::jsonb THEN RETURN false; END IF;
  IF doc->'payload' ? 'label' AND jsonb_typeof(doc->'payload'->'label')<>'string'
  THEN RETURN false; END IF;
  IF doc ? 'payload_ref' AND jsonb_typeof(doc->'payload_ref')<>'null' THEN RETURN false; END IF;
 ELSE
  ref:=doc->'payload_ref';
  IF NOT(ref ?& ARRAY['source_id','owner_id','domain','sha256'])
  OR ref-ARRAY['source_id','owner_id','domain','sha256']<>'{}'::jsonb
  THEN RETURN false; END IF;
  FOREACH key IN ARRAY ARRAY['source_id','owner_id','domain','sha256'] LOOP
   IF jsonb_typeof(ref->key)<>'string' THEN RETURN false; END IF;
  END LOOP;
  ident:=(ref->>'source_id')::uuid;
  IF ident='00000000-0000-0000-0000-000000000000'
  OR ref->>'owner_id' IS DISTINCT FROM doc->>'owner_id'
  OR ref->>'domain' IS DISTINCT FROM doc->>'domain'
  OR ref->>'sha256' !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
  IF doc ? 'payload' AND jsonb_typeof(doc->'payload')<>'null' THEN RETURN false; END IF;
 END IF;
 RETURN true;
END $$;

CREATE FUNCTION markos_runtime.append_event(p_wire bytea)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_runtime
AS $$
DECLARE principal markos_runtime.service_identities;
 doc jsonb; eid uuid; incoming text; existing text; business_hash text;
BEGIN
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities
 WHERE login_role=session_user AND can_publish;
 IF octet_length(p_wire)>65536 THEN RAISE EXCEPTION 'invalid event'; END IF;
 IF NOT markos_runtime.unique_json_keys(convert_from(p_wire,'UTF8')::json)
 THEN RAISE EXCEPTION 'invalid event'; END IF;
 doc := convert_from(p_wire,'UTF8')::jsonb;
 IF NOT markos_runtime.valid_envelope(doc) THEN RAISE EXCEPTION 'invalid envelope'; END IF;
 IF doc->>'event_type' IS DISTINCT FROM 'foundation.noop.requested'
 OR doc->>'schema_version' IS DISTINCT FROM '1'
 OR doc->>'owner_id' IS DISTINCT FROM principal.owner_id::text
 OR doc->>'domain' IS DISTINCT FROM principal.domain
 OR doc->>'source' IS DISTINCT FROM principal.service_id
 OR jsonb_typeof(doc->'operation_key') IS DISTINCT FROM 'string'
 OR NOT ((jsonb_typeof(doc->'payload')='object') IS TRUE
         <> (jsonb_typeof(doc->'payload_ref')='object') IS TRUE)
 THEN RAISE EXCEPTION 'event not authorized'; END IF;
 eid := (doc->>'event_id')::uuid;
 incoming := encode(sha256(p_wire),'hex');
 business_hash := encode(sha256(convert_to(jsonb_build_object(
   'event_type',doc->'event_type','payload',doc->'payload','payload_ref',doc->'payload_ref'
 )::text,'UTF8')),'hex');
 INSERT INTO markos_runtime.events(owner_id,domain,event_id,publisher_id,operation_key,wire,
 body_hash,operation_hash,expires_at)
 VALUES(principal.owner_id,principal.domain,eid,principal.service_id,doc->>'operation_key',
 p_wire,incoming,business_hash,(doc->>'expires_at')::timestamptz)
 ON CONFLICT DO NOTHING;
 IF NOT FOUND THEN
  SELECT body_hash INTO existing FROM markos_runtime.events
  WHERE owner_id=principal.owner_id AND domain=principal.domain AND event_id=eid;
  IF existing=incoming THEN RETURN 'duplicate'; END IF;
  INSERT INTO markos_runtime.quarantine(owner_id,domain,service_id,event_id,incoming_hash,reason)
  VALUES(principal.owner_id,principal.domain,principal.service_id,eid,incoming,'event_conflict')
  ON CONFLICT DO NOTHING;
  RETURN 'conflict';
 END IF;
 INSERT INTO markos_runtime.event_consumers(owner_id,domain,event_id,consumer_id)
 SELECT principal.owner_id,principal.domain,eid,service_id FROM markos_runtime.service_identities
 WHERE owner_id=principal.owner_id AND domain=principal.domain AND can_consume;
 IF NOT FOUND THEN RAISE EXCEPTION 'no intended consumer'; END IF;
 INSERT INTO markos_runtime.outbox(owner_id,domain,event_id,publisher_id)
 VALUES(principal.owner_id,principal.domain,eid,principal.service_id);
 RETURN 'created';
END $$;

CREATE FUNCTION markos_runtime.handoff(p_event uuid,p_hash text)
RETURNS TABLE(status text,task_id uuid) LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_runtime
AS $$
DECLARE principal markos_runtime.service_identities; ev markos_runtime.events;
 existing_task markos_runtime.tasks; chosen uuid; is_new boolean;
BEGIN
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities
 WHERE login_role=session_user AND can_consume;
 SELECT * INTO STRICT ev FROM markos_runtime.events
 WHERE owner_id=principal.owner_id AND domain=principal.domain AND event_id=p_event;
 -- Serialize one event/consumer receipt while allowing unrelated events in parallel.
 PERFORM 1 FROM markos_runtime.event_consumers c WHERE c.owner_id=principal.owner_id
 AND c.domain=principal.domain AND c.event_id=p_event AND c.consumer_id=principal.service_id
 FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'consumer not intended'; END IF;
 IF ev.body_hash<>p_hash THEN
  INSERT INTO markos_runtime.quarantine(owner_id,domain,service_id,event_id,incoming_hash,reason)
  VALUES(principal.owner_id,principal.domain,principal.service_id,p_event,p_hash,'event_conflict')
  ON CONFLICT DO NOTHING;
  RETURN QUERY SELECT 'conflict'::text,NULL::uuid; RETURN;
 END IF;
 SELECT i.task_id INTO chosen FROM markos_runtime.inbox i
 WHERE i.owner_id=principal.owner_id AND i.domain=principal.domain
 AND i.consumer_id=principal.service_id AND i.event_id=p_event;
 IF FOUND THEN RETURN QUERY SELECT 'duplicate'::text,chosen; RETURN; END IF;
 IF ev.expires_at IS NOT NULL AND ev.expires_at<=clock_timestamp() THEN
  INSERT INTO markos_runtime.quarantine(owner_id,domain,service_id,event_id,incoming_hash,reason)
  VALUES(principal.owner_id,principal.domain,principal.service_id,p_event,p_hash,'expired')
  ON CONFLICT DO NOTHING;
  RETURN QUERY SELECT 'expired'::text,NULL::uuid; RETURN;
 END IF;
 INSERT INTO markos_runtime.tasks(owner_id,domain,consumer_id,event_id,operation_key,operation_hash)
 VALUES(principal.owner_id,principal.domain,principal.service_id,p_event,ev.operation_key,ev.operation_hash)
 ON CONFLICT(owner_id,domain,consumer_id,operation_key) DO NOTHING
 RETURNING markos_runtime.tasks.task_id INTO chosen;
 is_new := FOUND;
 IF NOT is_new THEN
  SELECT * INTO STRICT existing_task FROM markos_runtime.tasks t
  WHERE t.owner_id=principal.owner_id AND t.domain=principal.domain
  AND t.consumer_id=principal.service_id AND t.operation_key=ev.operation_key;
  IF existing_task.operation_hash<>ev.operation_hash THEN
   INSERT INTO markos_runtime.quarantine(owner_id,domain,service_id,event_id,incoming_hash,reason)
   VALUES(principal.owner_id,principal.domain,principal.service_id,p_event,p_hash,'operation_conflict')
   ON CONFLICT DO NOTHING;
   RETURN QUERY SELECT 'conflict'::text,NULL::uuid; RETURN;
  END IF;
  chosen := existing_task.task_id;
 END IF;
 INSERT INTO markos_runtime.inbox(owner_id,domain,consumer_id,event_id,task_id)
 VALUES(principal.owner_id,principal.domain,principal.service_id,p_event,chosen);
 UPDATE markos_runtime.event_consumers c SET handed_off_at=clock_timestamp()
 WHERE c.owner_id=principal.owner_id AND c.domain=principal.domain
 AND c.event_id=p_event AND c.consumer_id=principal.service_id;
 RETURN QUERY SELECT CASE WHEN is_new THEN 'created' ELSE 'duplicate' END, chosen;
END $$;

CREATE FUNCTION markos_runtime.claim_outbox(p_limit integer,p_lease_seconds integer)
RETURNS TABLE(event_id uuid,wire bytea,fence bigint) LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_runtime
AS $$
DECLARE principal markos_runtime.service_identities;
BEGIN
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities
 WHERE login_role=session_user AND can_publish;
 IF p_limit NOT BETWEEN 1 AND 10 OR p_lease_seconds NOT BETWEEN 1 AND 300
 THEN RAISE EXCEPTION 'invalid claim budget'; END IF;
 RETURN QUERY WITH candidates AS (
 SELECT o.owner_id,o.domain,o.event_id FROM markos_runtime.outbox o
 WHERE o.owner_id=principal.owner_id AND o.domain=principal.domain
 AND o.publisher_id=principal.service_id
 AND (o.state='pending' OR (o.state='leased' AND o.lease_until<=clock_timestamp()))
 ORDER BY o.created_at,o.event_id FOR UPDATE SKIP LOCKED LIMIT p_limit
 ), claimed AS (
 UPDATE markos_runtime.outbox o SET state='leased',fence=o.fence+1,
 lease_until=clock_timestamp()+make_interval(secs=>p_lease_seconds)
 FROM candidates c WHERE (o.owner_id,o.domain,o.event_id)=(c.owner_id,c.domain,c.event_id)
 RETURNING o.event_id,o.owner_id,o.domain,o.fence
 ) SELECT c.event_id,e.wire,c.fence FROM claimed c JOIN markos_runtime.events e
 ON (e.owner_id,e.domain,e.event_id)=(c.owner_id,c.domain,c.event_id);
END $$;
CREATE FUNCTION markos_runtime.record_publication(p_event uuid,p_fence bigint)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_runtime
AS $$
DECLARE principal markos_runtime.service_identities;
BEGIN
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities
 WHERE login_role=session_user AND can_publish;
 UPDATE markos_runtime.outbox SET state='published',lease_until=NULL,published_at=clock_timestamp()
 WHERE owner_id=principal.owner_id AND domain=principal.domain AND event_id=p_event
 AND publisher_id=principal.service_id AND state='leased' AND fence=p_fence
 AND lease_until>clock_timestamp();
 RETURN FOUND;
END $$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA markos_runtime FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_runtime.can_read(uuid,text,text),
 markos_runtime.is_publisher(uuid,text,text),markos_runtime.can_read_dispatch(uuid,text,text),
 markos_runtime.append_event(bytea),markos_runtime.handoff(uuid,text),
 markos_runtime.claim_outbox(integer,integer),markos_runtime.record_publication(uuid,bigint)
 TO markos_runtime_client;
