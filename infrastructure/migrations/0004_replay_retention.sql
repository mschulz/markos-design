-- Synthetic M0-E only. No provider/object credentials or runtime migrations.
ALTER TABLE markos_runtime.service_identities
 ADD COLUMN can_replay boolean NOT NULL DEFAULT false,
 ADD COLUMN can_manage_retention boolean NOT NULL DEFAULT false,
 ADD COLUMN can_approve_replay boolean NOT NULL DEFAULT false;
CREATE FUNCTION markos_runtime.recovery_now() RETURNS timestamptz LANGUAGE sql VOLATILE
 SET search_path=pg_catalog AS $$ SELECT clock_timestamp() $$;
ALTER TABLE markos_runtime.events ALTER COLUMN wire DROP NOT NULL;
ALTER TABLE markos_runtime.events
 ADD COLUMN occurred_at timestamptz,
 ADD COLUMN source_id uuid,
 ADD COLUMN source_hash text,
 ADD COLUMN deleted_at timestamptz,
 ADD COLUMN retired_at timestamptz,
 ADD COLUMN terminal_at timestamptz,
 ADD COLUMN activity_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 ADD COLUMN replay_until timestamptz;
UPDATE markos_runtime.events SET occurred_at=(convert_from(wire,'UTF8')::jsonb->>'occurred_at')::timestamptz,
 source_id=(convert_from(wire,'UTF8')::jsonb->'payload_ref'->>'source_id')::uuid,
 source_hash=convert_from(wire,'UTF8')::jsonb->'payload_ref'->>'sha256';
ALTER TABLE markos_runtime.events ALTER COLUMN occurred_at SET NOT NULL;
ALTER TABLE markos_runtime.events ALTER COLUMN occurred_at SET DEFAULT clock_timestamp();
ALTER TABLE markos_runtime.events ADD CONSTRAINT wire_retirement
 CHECK(wire IS NOT NULL OR deleted_at IS NOT NULL OR retired_at IS NOT NULL);
ALTER TABLE markos_runtime.event_consumers ADD COLUMN cancelled_at timestamptz;
ALTER TABLE markos_runtime.quarantine ADD COLUMN resolved_at timestamptz;
ALTER TABLE markos_runtime.delivery_reviews ADD COLUMN resolved_at timestamptz;
ALTER TABLE markos_runtime.execution_reviews ADD COLUMN resolved_at timestamptz;
ALTER TABLE markos_runtime.execution_reviews DROP CONSTRAINT execution_reviews_reason_check;
ALTER TABLE markos_runtime.execution_reviews ADD CONSTRAINT execution_reviews_reason_check
 CHECK(reason IN ('expired','source_unavailable','retry_exhausted','unsupported','aged','deleted','retired','checksum','approval_expired'));
CREATE TABLE markos_runtime.source_observations (
 owner_id uuid NOT NULL,domain text NOT NULL,source_id uuid NOT NULL,
 sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),accessible boolean NOT NULL,
 kind text NOT NULL CHECK(kind='synthetic'),deleted_at timestamptz,
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,source_id)
);
CREATE TABLE markos_runtime.source_grants (
 owner_id uuid NOT NULL,domain text NOT NULL,source_id uuid NOT NULL,service_id text NOT NULL,
 PRIMARY KEY(owner_id,domain,source_id,service_id),
 FOREIGN KEY(owner_id,domain,source_id) REFERENCES markos_runtime.source_observations
);
CREATE TABLE markos_runtime.event_tombstones (
 owner_id uuid NOT NULL,domain text NOT NULL,event_id uuid NOT NULL,publisher_id text NOT NULL,
 reason text NOT NULL CHECK(reason IN ('deleted','retired')),body_hash text NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,event_id)
);
CREATE TABLE markos_runtime.recovery_reviews (
 owner_id uuid NOT NULL,domain text NOT NULL,service_id text NOT NULL,event_id uuid NOT NULL,
 reason text NOT NULL CHECK(reason IN ('aged','deleted','retired','expired','source_unavailable','checksum','approval_expired')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),resolved_at timestamptz,
 PRIMARY KEY(owner_id,domain,service_id,event_id,reason)
);
CREATE TABLE markos_runtime.replay_permits (
 permit_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),owner_id uuid NOT NULL,domain text NOT NULL,
 event_id uuid NOT NULL,publisher_id text NOT NULL,body_hash text NOT NULL,consumers text[] NOT NULL,
 expires_at timestamptz NOT NULL,used_at timestamptz,
 suppress_effects boolean NOT NULL DEFAULT true CHECK(suppress_effects)
);
CREATE TABLE markos_runtime.replay_history (
 replay_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),owner_id uuid NOT NULL,domain text NOT NULL,
 event_id uuid NOT NULL,publisher_id text NOT NULL,consumers text[] NOT NULL,
 requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),available_at timestamptz NOT NULL,
 permit_id uuid REFERENCES markos_runtime.replay_permits,
 suppress_effects boolean NOT NULL DEFAULT true CHECK(suppress_effects)
);
CREATE TABLE markos_runtime.retention_audit (
 audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,owner_id uuid NOT NULL,domain text NOT NULL,
 service_id text NOT NULL,event_id uuid NOT NULL,action text NOT NULL CHECK(action IN ('deleted','payload_retired','metadata_purged','reviews_resolved')),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE markos_runtime.replay_budget (id integer PRIMARY KEY CHECK(id=1),next_at timestamptz NOT NULL);
INSERT INTO markos_runtime.replay_budget VALUES(1,'epoch');
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['source_observations','source_grants','event_tombstones','recovery_reviews','replay_permits','replay_history','retention_audit'] LOOP
  EXECUTE format('ALTER TABLE markos_runtime.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY scoped ON markos_runtime.%I TO markos_runtime_client USING(markos_runtime.can_read(owner_id,domain))',t);
  EXECUTE format('GRANT SELECT ON markos_runtime.%I TO markos_runtime_client',t);
 END LOOP;
END $$;
REVOKE ALL ON markos_runtime.replay_budget FROM PUBLIC,markos_runtime_client;

CREATE FUNCTION markos_runtime.event_gate(p_owner uuid,p_domain text,p_event uuid,p_service text,p_allow_age boolean DEFAULT false)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE e markos_runtime.events;s markos_runtime.source_observations;n timestamptz:=markos_runtime.recovery_now();
BEGIN
 SELECT * INTO e FROM markos_runtime.events WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
 IF NOT FOUND THEN
  SELECT reason INTO p_service FROM markos_runtime.event_tombstones WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
  RETURN coalesce(p_service,'retired');
 END IF;
 IF e.deleted_at IS NOT NULL THEN RETURN 'deleted'; END IF;
 IF e.wire IS NULL THEN RETURN 'retired'; END IF;
 IF e.expires_at<=n THEN RETURN 'expired'; END IF;
 IF NOT p_allow_age AND e.occurred_at<n-interval '30 days' AND NOT coalesce(e.replay_until>n,false) THEN
  RETURN CASE WHEN e.replay_until IS NOT NULL THEN 'approval_expired' ELSE 'aged' END;
 END IF;
 IF e.source_id IS NOT NULL THEN
  SELECT * INTO s FROM markos_runtime.source_observations WHERE owner_id=p_owner AND domain=p_domain AND source_id=e.source_id;
  IF NOT FOUND OR NOT s.accessible OR s.deleted_at IS NOT NULL THEN RETURN 'source_unavailable'; END IF;
  IF s.sha256<>e.source_hash THEN RETURN 'checksum'; END IF;
  IF NOT EXISTS(SELECT 1 FROM markos_runtime.source_grants g WHERE g.owner_id=p_owner AND g.domain=p_domain AND g.source_id=e.source_id AND g.service_id=p_service)
  THEN RETURN 'source_unavailable'; END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION markos_runtime.recovery_review(p_owner uuid,p_domain text,p_service text,p_event uuid,p_reason text)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
 INSERT INTO markos_runtime.recovery_reviews(owner_id,domain,service_id,event_id,reason,created_at)
 VALUES(p_owner,p_domain,p_service,p_event,p_reason,markos_runtime.recovery_now())
 ON CONFLICT(owner_id,domain,service_id,event_id,reason) DO UPDATE SET resolved_at=NULL
$$;
CREATE FUNCTION markos_runtime.hold_recovery(p_owner uuid,p_domain text,p_event uuid,p_reason text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE publisher text;
BEGIN
 SELECT publisher_id INTO STRICT publisher FROM markos_runtime.events WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
 PERFORM markos_runtime.recovery_review(p_owner,p_domain,publisher,p_event,p_reason);
 UPDATE markos_runtime.outbox SET state='review',lease_until=NULL,fence=fence+1,last_failure=p_reason
 WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
END $$;
CREATE FUNCTION markos_runtime.block_task(p_task uuid,p_reason text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE t markos_runtime.tasks;
BEGIN
 SELECT * INTO STRICT t FROM markos_runtime.tasks WHERE task_id=p_task FOR UPDATE;
 UPDATE markos_runtime.execution_slots SET task_id=NULL,fence=NULL,lease_until=NULL WHERE task_id=p_task AND fence=t.fence;
 UPDATE markos_runtime.task_attempts SET outcome='review',finished_at=markos_runtime.recovery_now()
 WHERE task_id=p_task AND outcome='running';
 UPDATE markos_runtime.tasks SET state='review',fence=fence+1,lease_until=NULL,executor_id=NULL,slot_id=NULL WHERE task_id=p_task;
 INSERT INTO markos_runtime.execution_reviews(owner_id,domain,consumer_id,task_id,reason)
 VALUES(t.owner_id,t.domain,t.consumer_id,t.task_id,p_reason) ON CONFLICT DO NOTHING;
END $$;
CREATE FUNCTION markos_runtime.refresh_terminal(p_owner uuid,p_domain text)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
 UPDATE markos_runtime.events e SET terminal_at=markos_runtime.recovery_now()
 WHERE e.owner_id=p_owner AND e.domain=p_domain AND e.terminal_at IS NULL
 AND (e.deleted_at IS NOT NULL OR (
  EXISTS(SELECT 1 FROM markos_runtime.outbox o WHERE (o.owner_id,o.domain,o.event_id)=(e.owner_id,e.domain,e.event_id) AND o.state='published')
  AND NOT EXISTS(SELECT 1 FROM markos_runtime.event_consumers c WHERE (c.owner_id,c.domain,c.event_id)=(e.owner_id,e.domain,e.event_id) AND c.handed_off_at IS NULL)
  AND NOT EXISTS(SELECT 1 FROM markos_runtime.inbox i JOIN markos_runtime.tasks t USING(owner_id,domain,consumer_id,task_id)
   WHERE (i.owner_id,i.domain,i.event_id)=(e.owner_id,e.domain,e.event_id) AND t.state NOT IN ('completed','failed'))))
$$;

CREATE FUNCTION markos_runtime.observe_source(p_id uuid,p_hash text,p_access boolean,p_services text[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;sv text;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_manage_retention;
 IF p_id IS NULL OR p_id='00000000-0000-0000-0000-000000000000' OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$'
 OR p_access IS NULL OR p_services IS NULL OR cardinality(p_services)>64 THEN RAISE EXCEPTION 'invalid synthetic source'; END IF;
 PERFORM pg_advisory_xact_lock(712093482);
 IF EXISTS(SELECT 1 FROM markos_runtime.source_observations WHERE owner_id=p.owner_id AND domain=p.domain AND source_id=p_id AND deleted_at IS NOT NULL)
 THEN RAISE EXCEPTION 'source tombstoned'; END IF;
 FOREACH sv IN ARRAY p_services LOOP
  IF NOT EXISTS(SELECT 1 FROM markos_runtime.service_identities WHERE service_id=sv AND owner_id=p.owner_id AND domain=p.domain)
  THEN RAISE EXCEPTION 'source grant not scoped'; END IF;
 END LOOP;
 INSERT INTO markos_runtime.source_observations VALUES(p.owner_id,p.domain,p_id,p_hash,p_access,'synthetic',NULL,markos_runtime.recovery_now())
 ON CONFLICT(owner_id,domain,source_id) DO UPDATE SET sha256=EXCLUDED.sha256,accessible=EXCLUDED.accessible,observed_at=EXCLUDED.observed_at;
 DELETE FROM markos_runtime.source_grants WHERE owner_id=p.owner_id AND domain=p.domain AND source_id=p_id;
 INSERT INTO markos_runtime.source_grants SELECT DISTINCT p.owner_id,p.domain,p_id,unnest(p_services);
END $$;

-- Wrap every durable entry point; old bodies remain preserved, but are no longer callable by runtime.
ALTER FUNCTION markos_runtime.append_event(bytea) RENAME TO append_event_m0b;
CREATE FUNCTION markos_runtime.append_event(p_wire bytea) RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;doc jsonb;eid uuid;status text;why text;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_publish;
 PERFORM pg_advisory_xact_lock(712093482);
 doc:=convert_from(p_wire,'UTF8')::jsonb;eid:=(doc->>'event_id')::uuid;
 IF EXISTS(SELECT 1 FROM markos_runtime.event_tombstones WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=eid) THEN
  PERFORM markos_runtime.recovery_review(p.owner_id,p.domain,p.service_id,eid,'retired');RETURN 'review'; END IF;
 status:=markos_runtime.append_event_m0b(p_wire);
 IF status='conflict' THEN RETURN status; END IF;
 UPDATE markos_runtime.events SET occurred_at=(doc->>'occurred_at')::timestamptz,
 source_id=(doc->'payload_ref'->>'source_id')::uuid,source_hash=doc->'payload_ref'->>'sha256',activity_at=markos_runtime.recovery_now()
 WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=eid;
 why:=markos_runtime.event_gate(p.owner_id,p.domain,eid,p.service_id);
 IF EXISTS(SELECT 1 FROM markos_runtime.source_observations so WHERE so.owner_id=p.owner_id AND so.domain=p.domain
 AND so.source_id=(doc->'payload_ref'->>'source_id')::uuid AND so.deleted_at IS NOT NULL) THEN
  PERFORM markos_runtime.redact_event(p.owner_id,p.domain,eid);RETURN 'review'; END IF;
 IF why IS NOT NULL THEN PERFORM markos_runtime.hold_recovery(p.owner_id,p.domain,eid,why);RETURN 'review'; END IF;
 RETURN status;
END $$;
ALTER FUNCTION markos_runtime.handoff(uuid,text) RENAME TO handoff_m0c;
CREATE FUNCTION markos_runtime.handoff(p_event uuid,p_hash text) RETURNS TABLE(status text,task_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;why text;expected text;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_consume;
 PERFORM pg_advisory_xact_lock(712093482);
 SELECT e.body_hash INTO expected FROM markos_runtime.events e JOIN markos_runtime.event_consumers c USING(owner_id,domain,event_id)
 WHERE e.owner_id=p.owner_id AND e.domain=p.domain AND e.event_id=p_event AND c.consumer_id=p.service_id;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='M0001',MESSAGE='handoff not registered'; END IF;
 IF p_hash IS DISTINCT FROM expected THEN RETURN QUERY SELECT * FROM markos_runtime.handoff_m0c(p_event,p_hash); RETURN; END IF;
 why:=markos_runtime.event_gate(p.owner_id,p.domain,p_event,p.service_id);
 IF why IS NOT NULL THEN
  PERFORM markos_runtime.recovery_review(p.owner_id,p.domain,p.service_id,p_event,why);
  RETURN QUERY SELECT 'review'::text,NULL::uuid; RETURN;
 END IF;
 RETURN QUERY SELECT * FROM markos_runtime.handoff_m0c(p_event,p_hash);
 UPDATE markos_runtime.events SET activity_at=markos_runtime.recovery_now() WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event;
 PERFORM markos_runtime.refresh_terminal(p.owner_id,p.domain);
END $$;
ALTER FUNCTION markos_runtime.claim_outbox(integer,integer) RENAME TO claim_outbox_m0c;
CREATE FUNCTION markos_runtime.claim_outbox(p_limit integer,p_seconds integer)
RETURNS TABLE(event_id uuid,wire bytea,fence bigint,attempts integer,consumers text[])
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;r record;why text;sv text;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_publish;
 PERFORM pg_advisory_xact_lock(712093482);
 FOR r IN SELECT * FROM markos_runtime.claim_outbox_m0c(p_limit,p_seconds) LOOP
  why:=markos_runtime.event_gate(p.owner_id,p.domain,r.event_id,p.service_id);
  FOREACH sv IN ARRAY r.consumers LOOP
   why:=coalesce(why,markos_runtime.event_gate(p.owner_id,p.domain,r.event_id,sv));
  END LOOP;
  IF why IS NOT NULL THEN PERFORM markos_runtime.hold_recovery(p.owner_id,p.domain,r.event_id,why);CONTINUE; END IF;
  RETURN QUERY SELECT r.event_id,r.wire,r.fence,r.attempts,r.consumers;
 END LOOP;
END $$;
ALTER FUNCTION markos_runtime.record_publication(uuid,bigint) RENAME TO record_publication_m0b;
CREATE FUNCTION markos_runtime.record_publication(p_event uuid,p_fence bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;ok boolean;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_publish;
 PERFORM pg_advisory_xact_lock(712093482);
 IF NOT markos_runtime.authorize_publication(p_event,p_fence) THEN RETURN false; END IF;
 ok:=markos_runtime.record_publication_m0b(p_event,p_fence);
 IF ok THEN UPDATE markos_runtime.events SET activity_at=markos_runtime.recovery_now() WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event;END IF;
 PERFORM markos_runtime.refresh_terminal(p.owner_id,p.domain);RETURN ok;
END $$;
ALTER FUNCTION markos_runtime.claim_task(uuid,integer) RENAME TO claim_task_m0d;
CREATE FUNCTION markos_runtime.claim_task(p_executor uuid,p_seconds integer)
RETURNS TABLE(task_id uuid,wire bytea,fence bigint,attempt integer,lease_until timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;r record;eid uuid;why text;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_consume AND can_execute;
 PERFORM pg_advisory_xact_lock(712093482);
 FOR r IN SELECT * FROM markos_runtime.claim_task_m0d(p_executor,p_seconds) LOOP
  SELECT t.event_id INTO STRICT eid FROM markos_runtime.tasks t WHERE t.task_id=r.task_id;
  why:=markos_runtime.event_gate(p.owner_id,p.domain,eid,p.service_id);
  IF why IS NOT NULL THEN PERFORM markos_runtime.block_task(r.task_id,why);RETURN; END IF;
  UPDATE markos_runtime.events SET activity_at=markos_runtime.recovery_now() WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=eid;
  RETURN QUERY SELECT r.task_id,r.wire,r.fence,r.attempt,r.lease_until;
 END LOOP;
END $$;
ALTER FUNCTION markos_runtime.finish_task(uuid,bigint,uuid,text,integer) RENAME TO finish_task_m0d;
CREATE FUNCTION markos_runtime.finish_task(p_task uuid,p_fence bigint,p_executor uuid,p_failure text DEFAULT NULL,p_delay integer DEFAULT 1)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;t markos_runtime.tasks;why text;ok boolean;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_consume AND can_execute;
 PERFORM pg_advisory_xact_lock(712093482);
 SELECT * INTO t FROM markos_runtime.tasks WHERE task_id=p_task AND owner_id=p.owner_id AND domain=p.domain AND consumer_id=p.service_id
 AND state='running' AND fence=p_fence AND executor_id=p_executor AND lease_until>clock_timestamp();
 IF NOT FOUND THEN RETURN false; END IF;
 why:=markos_runtime.event_gate(p.owner_id,p.domain,t.event_id,p.service_id);
 IF why IS NOT NULL THEN PERFORM markos_runtime.block_task(p_task,why);RETURN false; END IF;
 ok:=markos_runtime.finish_task_m0d(p_task,p_fence,p_executor,p_failure,p_delay);
 UPDATE markos_runtime.events SET activity_at=markos_runtime.recovery_now() WHERE owner_id=p.owner_id AND domain=p.domain AND event_id IN
 (SELECT i.event_id FROM markos_runtime.inbox i WHERE i.task_id=p_task);
 PERFORM markos_runtime.refresh_terminal(p.owner_id,p.domain);RETURN ok;
END $$;
CREATE FUNCTION markos_runtime.approve_replay(p_event uuid,p_seconds integer)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;e markos_runtime.events;ident uuid;targets text[];why text;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_approve_replay;
 IF p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 3600 THEN RAISE EXCEPTION 'invalid permit lifetime'; END IF;
 PERFORM pg_advisory_xact_lock(712093482);
 SELECT * INTO STRICT e FROM markos_runtime.events WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event;
 why:=markos_runtime.event_gate(p.owner_id,p.domain,p_event,e.publisher_id,true);
 IF why IS NOT NULL THEN RAISE EXCEPTION 'replay not eligible'; END IF;
 SELECT array_agg(consumer_id ORDER BY consumer_id) INTO targets FROM markos_runtime.event_consumers
 WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event;
 INSERT INTO markos_runtime.replay_permits(owner_id,domain,event_id,publisher_id,body_hash,consumers,expires_at)
 VALUES(p.owner_id,p.domain,p_event,e.publisher_id,e.body_hash,targets,markos_runtime.recovery_now()+make_interval(secs=>p_seconds)) RETURNING permit_id INTO ident;
 RETURN ident;
END $$;
CREATE FUNCTION markos_runtime.request_replay(p_event uuid,p_permit uuid DEFAULT NULL)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;e markos_runtime.events;why text;targets text[];sv text;
 permit markos_runtime.replay_permits;tick timestamptz;n timestamptz:=markos_runtime.recovery_now();
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_publish AND can_replay;
 PERFORM pg_advisory_xact_lock(712093482);
 SELECT * INTO e FROM markos_runtime.events WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event AND publisher_id=p.service_id FOR UPDATE;
 IF NOT FOUND THEN
  IF EXISTS(SELECT 1 FROM markos_runtime.event_tombstones WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event AND publisher_id=p.service_id) THEN RETURN 'retired'; END IF;
  RAISE EXCEPTION 'replay not registered';
 END IF;
 SELECT array_agg(consumer_id ORDER BY consumer_id) INTO targets FROM markos_runtime.event_consumers
 WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event;
 IF p_permit IS NOT NULL THEN
  SELECT * INTO permit FROM markos_runtime.replay_permits WHERE permit_id=p_permit AND owner_id=p.owner_id AND domain=p.domain
   AND event_id=p_event AND publisher_id=p.service_id AND body_hash=e.body_hash AND consumers=targets AND used_at IS NULL AND expires_at>n;
  IF NOT FOUND THEN
   PERFORM markos_runtime.recovery_review(p.owner_id,p.domain,p.service_id,p_event,'approval_expired');RETURN 'review'; END IF;
 END IF;
 why:=markos_runtime.event_gate(p.owner_id,p.domain,p_event,p.service_id,p_permit IS NOT NULL);
 FOREACH sv IN ARRAY targets LOOP
  why:=coalesce(why,markos_runtime.event_gate(p.owner_id,p.domain,p_event,sv,p_permit IS NOT NULL));
 END LOOP;
 IF why IS NOT NULL THEN PERFORM markos_runtime.hold_recovery(p.owner_id,p.domain,p_event,why);RETURN 'review'; END IF;
 IF p_permit IS NOT NULL THEN
  UPDATE markos_runtime.replay_permits SET used_at=n WHERE permit_id=p_permit;
  UPDATE markos_runtime.events SET replay_until=permit.expires_at WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event;
  UPDATE markos_runtime.recovery_reviews SET resolved_at=n WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event AND reason IN ('aged','approval_expired');
 END IF;
 IF NOT EXISTS(SELECT 1 FROM markos_runtime.event_consumers WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event AND handed_off_at IS NULL AND cancelled_at IS NULL) THEN
  IF e.source_id IS NULL THEN
   UPDATE markos_runtime.tasks t SET state='pending',available_at=clock_timestamp()
   WHERE t.owner_id=p.owner_id AND t.domain=p.domain AND t.state='review' AND t.attempts<3
   AND EXISTS(SELECT 1 FROM markos_runtime.inbox i WHERE i.task_id=t.task_id AND i.event_id=p_event)
   AND EXISTS(SELECT 1 FROM markos_runtime.execution_reviews r WHERE r.task_id=t.task_id AND r.reason IN ('aged','approval_expired'));
   IF FOUND THEN
    UPDATE markos_runtime.execution_reviews SET resolved_at=n WHERE owner_id=p.owner_id AND domain=p.domain AND reason IN ('aged','approval_expired') AND task_id IN
     (SELECT task_id FROM markos_runtime.inbox WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event);
    UPDATE markos_runtime.events SET terminal_at=NULL,activity_at=n WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event;
    INSERT INTO markos_runtime.replay_history(owner_id,domain,event_id,publisher_id,consumers,requested_at,available_at,permit_id)
    VALUES(p.owner_id,p.domain,p_event,p.service_id,targets,n,clock_timestamp(),p_permit);
    RETURN 'resumed';
   END IF;
  END IF;
  RETURN 'complete';
 END IF;
 IF EXISTS(SELECT 1 FROM markos_runtime.outbox WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event AND state IN ('pending','leased'))
 AND p_permit IS NULL THEN RETURN 'pending'; END IF;
 SELECT greatest(next_at,clock_timestamp()) INTO tick FROM markos_runtime.replay_budget WHERE id=1 FOR UPDATE;
 UPDATE markos_runtime.replay_budget SET next_at=tick+interval '0.1 second' WHERE id=1;
 UPDATE markos_runtime.outbox SET state='pending',lease_until=NULL,fence=fence+1,available_at=tick,last_failure=NULL
 WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event;
 UPDATE markos_runtime.events SET activity_at=n WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event;
 INSERT INTO markos_runtime.replay_history(owner_id,domain,event_id,publisher_id,consumers,requested_at,available_at,permit_id)
 VALUES(p.owner_id,p.domain,p_event,p.service_id,targets,n,tick,p_permit);
 RETURN 'queued';
END $$;
CREATE FUNCTION markos_runtime.reconcile(p_limit integer)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;eid uuid;total integer:=0;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_publish AND can_replay;
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 10 THEN RAISE EXCEPTION 'invalid recovery batch'; END IF;
 PERFORM pg_advisory_xact_lock(712093482);
 FOR eid IN SELECT e.event_id FROM markos_runtime.events e JOIN markos_runtime.outbox o USING(owner_id,domain,event_id)
 WHERE e.owner_id=p.owner_id AND e.domain=p.domain AND e.publisher_id=p.service_id AND o.state='published'
 AND EXISTS(SELECT 1 FROM markos_runtime.event_consumers c WHERE (c.owner_id,c.domain,c.event_id)=(e.owner_id,e.domain,e.event_id) AND c.handed_off_at IS NULL AND c.cancelled_at IS NULL)
 ORDER BY e.created_at,e.event_id LIMIT p_limit LOOP
  IF markos_runtime.request_replay(eid)='queued' THEN total:=total+1;END IF;
 END LOOP;
 RETURN total;
END $$;

CREATE FUNCTION markos_runtime.redact_event(p_owner uuid,p_domain text,p_event uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE e markos_runtime.events;tid uuid;n timestamptz:=markos_runtime.recovery_now();
BEGIN
 SELECT * INTO STRICT e FROM markos_runtime.events WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event FOR UPDATE;
 INSERT INTO markos_runtime.event_tombstones(owner_id,domain,event_id,publisher_id,reason,body_hash,recorded_at)
 VALUES(p_owner,p_domain,p_event,e.publisher_id,'deleted',e.body_hash,n)
 ON CONFLICT(owner_id,domain,event_id) DO UPDATE SET reason='deleted';
 UPDATE markos_runtime.events SET wire=NULL,deleted_at=coalesce(deleted_at,n),replay_until=NULL,terminal_at=n,activity_at=n
 WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
 UPDATE markos_runtime.outbox SET state='review',lease_until=NULL,fence=fence+1,last_failure='deleted'
 WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
 UPDATE markos_runtime.event_consumers SET cancelled_at=n WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
 FOR tid IN SELECT DISTINCT i.task_id FROM markos_runtime.inbox i WHERE i.owner_id=p_owner AND i.domain=p_domain AND i.event_id=p_event LOOP
  PERFORM markos_runtime.block_task(tid,'deleted');
 END LOOP;
 UPDATE markos_runtime.replay_permits SET expires_at=least(expires_at,n) WHERE owner_id=p_owner AND domain=p_domain AND event_id=p_event;
 INSERT INTO markos_runtime.retention_audit(owner_id,domain,service_id,event_id,action,recorded_at)
 VALUES(p_owner,p_domain,e.publisher_id,p_event,'deleted',n);
END $$;
CREATE FUNCTION markos_runtime.delete_event(p_event uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_manage_retention;
 PERFORM pg_advisory_xact_lock(712093482);
 PERFORM 1 FROM markos_runtime.events WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event AND publisher_id=p.service_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'event not scoped'; END IF;
 PERFORM markos_runtime.redact_event(p.owner_id,p.domain,p_event);
END $$;
CREATE FUNCTION markos_runtime.delete_source(p_source uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;eid uuid;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_manage_retention;
 PERFORM pg_advisory_xact_lock(712093482);
 UPDATE markos_runtime.source_observations SET deleted_at=coalesce(deleted_at,markos_runtime.recovery_now()),accessible=false
 WHERE owner_id=p.owner_id AND domain=p.domain AND source_id=p_source;
 IF NOT FOUND THEN RAISE EXCEPTION 'source not registered'; END IF;
 DELETE FROM markos_runtime.source_grants WHERE owner_id=p.owner_id AND domain=p.domain AND source_id=p_source;
 FOR eid IN SELECT event_id FROM markos_runtime.events WHERE owner_id=p.owner_id AND domain=p.domain AND source_id=p_source LOOP
  PERFORM markos_runtime.redact_event(p.owner_id,p.domain,eid);
 END LOOP;
END $$;
CREATE FUNCTION markos_runtime.resolve_event_reviews(p_event uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;digest text;n timestamptz:=markos_runtime.recovery_now();
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_manage_retention;
 PERFORM pg_advisory_xact_lock(712093482);
 SELECT body_hash INTO STRICT digest FROM markos_runtime.events WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event AND publisher_id=p.service_id;
 UPDATE markos_runtime.recovery_reviews SET resolved_at=n WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event;
 UPDATE markos_runtime.quarantine SET resolved_at=n WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event;
 UPDATE markos_runtime.execution_reviews SET resolved_at=n WHERE owner_id=p.owner_id AND domain=p.domain AND task_id IN
 (SELECT task_id FROM markos_runtime.inbox WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event);
 UPDATE markos_runtime.delivery_reviews SET resolved_at=n WHERE owner_id=p.owner_id AND domain=p.domain AND body_hash=digest;
 UPDATE markos_runtime.events SET activity_at=n WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event;
 INSERT INTO markos_runtime.retention_audit(owner_id,domain,service_id,event_id,action,recorded_at) VALUES(p.owner_id,p.domain,p.service_id,p_event,'reviews_resolved',n);
END $$;
CREATE FUNCTION markos_runtime.cleanup_retention(p_limit integer,p_dry_run boolean)
RETURNS TABLE(event_id uuid,action text) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
#variable_conflict use_column
DECLARE p markos_runtime.service_identities;e markos_runtime.events;n timestamptz:=markos_runtime.recovery_now();choice text;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_manage_retention;
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 OR p_dry_run IS NULL THEN RAISE EXCEPTION 'invalid cleanup budget'; END IF;
 PERFORM pg_advisory_xact_lock(712093482);
 FOR e IN SELECT * FROM markos_runtime.events x WHERE x.owner_id=p.owner_id AND x.domain=p.domain AND x.publisher_id=p.service_id
 AND x.terminal_at IS NOT NULL ORDER BY x.created_at,x.event_id LIMIT p_limit FOR UPDATE LOOP
  IF EXISTS(SELECT 1 FROM markos_runtime.recovery_reviews r WHERE r.owner_id=e.owner_id AND r.domain=e.domain AND r.event_id=e.event_id AND r.resolved_at IS NULL)
  OR EXISTS(SELECT 1 FROM markos_runtime.quarantine r WHERE r.owner_id=e.owner_id AND r.domain=e.domain AND r.event_id=e.event_id AND r.resolved_at IS NULL)
  OR EXISTS(SELECT 1 FROM markos_runtime.execution_reviews r JOIN markos_runtime.inbox i USING(owner_id,domain,consumer_id,task_id)
   WHERE i.owner_id=e.owner_id AND i.domain=e.domain AND i.event_id=e.event_id AND r.resolved_at IS NULL)
  OR EXISTS(SELECT 1 FROM markos_runtime.delivery_reviews r WHERE r.owner_id=e.owner_id AND r.domain=e.domain AND r.body_hash=e.body_hash AND r.resolved_at IS NULL)
  THEN CONTINUE;END IF;
  IF e.deleted_at IS NULL AND (
   EXISTS(SELECT 1 FROM markos_runtime.outbox o WHERE o.owner_id=e.owner_id AND o.domain=e.domain AND o.event_id=e.event_id AND o.state<>'published')
   OR EXISTS(SELECT 1 FROM markos_runtime.event_consumers c WHERE c.owner_id=e.owner_id AND c.domain=e.domain AND c.event_id=e.event_id AND c.handed_off_at IS NULL)
   OR EXISTS(SELECT 1 FROM markos_runtime.inbox i JOIN markos_runtime.tasks t USING(owner_id,domain,consumer_id,task_id)
    WHERE i.owner_id=e.owner_id AND i.domain=e.domain AND i.event_id=e.event_id AND t.state NOT IN ('completed','failed')))
  THEN CONTINUE;END IF;
  choice:=NULL;
  IF greatest(e.created_at,e.terminal_at,e.activity_at)<=n-interval '90 days'
   AND NOT EXISTS(SELECT 1 FROM markos_runtime.tasks t JOIN markos_runtime.inbox i USING(owner_id,domain,consumer_id,task_id)
    WHERE t.owner_id=e.owner_id AND t.domain=e.domain AND t.event_id=e.event_id AND i.event_id<>e.event_id)
  THEN choice:='metadata_purged';
  ELSIF e.wire IS NOT NULL AND greatest(e.created_at,e.terminal_at)<=n-interval '30 days' THEN choice:='payload_retired';END IF;
  IF choice IS NULL THEN CONTINUE;END IF;
  RETURN QUERY SELECT e.event_id,choice;
  IF p_dry_run THEN CONTINUE;END IF;
  INSERT INTO markos_runtime.event_tombstones(owner_id,domain,event_id,publisher_id,reason,body_hash,recorded_at)
  VALUES(e.owner_id,e.domain,e.event_id,e.publisher_id,'retired',e.body_hash,n) ON CONFLICT DO NOTHING;
  IF choice='payload_retired' THEN
   UPDATE markos_runtime.events SET wire=NULL,retired_at=n WHERE owner_id=e.owner_id AND domain=e.domain AND event_id=e.event_id;
  ELSE
   DELETE FROM markos_runtime.task_results WHERE owner_id=e.owner_id AND domain=e.domain AND task_id IN(SELECT task_id FROM markos_runtime.tasks WHERE event_id=e.event_id AND owner_id=e.owner_id AND domain=e.domain);
   DELETE FROM markos_runtime.task_attempts WHERE owner_id=e.owner_id AND domain=e.domain AND task_id IN(SELECT task_id FROM markos_runtime.tasks WHERE event_id=e.event_id AND owner_id=e.owner_id AND domain=e.domain);
   DELETE FROM markos_runtime.execution_reviews WHERE owner_id=e.owner_id AND domain=e.domain AND task_id IN(SELECT task_id FROM markos_runtime.tasks WHERE event_id=e.event_id AND owner_id=e.owner_id AND domain=e.domain);
   DELETE FROM markos_runtime.inbox WHERE owner_id=e.owner_id AND domain=e.domain AND event_id=e.event_id;
   DELETE FROM markos_runtime.tasks WHERE owner_id=e.owner_id AND domain=e.domain AND event_id=e.event_id;
   DELETE FROM markos_runtime.schedule_occurrences WHERE owner_id=e.owner_id AND domain=e.domain AND event_id=e.event_id;
   DELETE FROM markos_runtime.event_consumers WHERE owner_id=e.owner_id AND domain=e.domain AND event_id=e.event_id;
   DELETE FROM markos_runtime.outbox WHERE owner_id=e.owner_id AND domain=e.domain AND event_id=e.event_id;
   DELETE FROM markos_runtime.quarantine WHERE owner_id=e.owner_id AND domain=e.domain AND event_id=e.event_id;
   DELETE FROM markos_runtime.events WHERE owner_id=e.owner_id AND domain=e.domain AND event_id=e.event_id;
  END IF;
  INSERT INTO markos_runtime.retention_audit(owner_id,domain,service_id,event_id,action,recorded_at) VALUES(e.owner_id,e.domain,p.service_id,e.event_id,choice,n);
 END LOOP;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA markos_runtime FROM PUBLIC;
REVOKE ALL ON FUNCTION markos_runtime.append_event_m0b(bytea),markos_runtime.handoff_m0c(uuid,text),markos_runtime.claim_outbox_m0c(integer,integer),
 markos_runtime.record_publication_m0b(uuid,bigint),markos_runtime.claim_task_m0d(uuid,integer),markos_runtime.finish_task_m0d(uuid,bigint,uuid,text,integer) FROM markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_runtime.append_event(bytea),markos_runtime.handoff(uuid,text),markos_runtime.claim_outbox(integer,integer),
 markos_runtime.record_publication(uuid,bigint),markos_runtime.claim_task(uuid,integer),markos_runtime.finish_task(uuid,bigint,uuid,text,integer),
 markos_runtime.observe_source(uuid,text,boolean,text[]),markos_runtime.approve_replay(uuid,integer),markos_runtime.request_replay(uuid,uuid),
 markos_runtime.reconcile(integer),markos_runtime.delete_event(uuid),markos_runtime.delete_source(uuid),markos_runtime.resolve_event_reviews(uuid),
 markos_runtime.cleanup_retention(integer,boolean) TO markos_runtime_client;

CREATE FUNCTION markos_runtime.authorize_publication(p_event uuid,p_fence bigint)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;why text;sv text;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_publish;
 PERFORM pg_advisory_xact_lock(712093482);
 PERFORM 1 FROM markos_runtime.outbox WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event
 AND publisher_id=p.service_id AND state='leased' AND fence=p_fence AND lease_until>clock_timestamp();
 IF NOT FOUND THEN RETURN false; END IF;
 why:=markos_runtime.event_gate(p.owner_id,p.domain,p_event,p.service_id);
 FOR sv IN SELECT consumer_id FROM markos_runtime.event_consumers WHERE owner_id=p.owner_id AND domain=p.domain AND event_id=p_event LOOP
  why:=coalesce(why,markos_runtime.event_gate(p.owner_id,p.domain,p_event,sv));
 END LOOP;
 IF why IS NOT NULL THEN PERFORM markos_runtime.hold_recovery(p.owner_id,p.domain,p_event,why);RETURN false; END IF;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION markos_runtime.authorize_publication(uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_runtime.authorize_publication(uuid,bigint) TO markos_runtime_client;

-- Existing completed records get a conservative migration-time floor, never an inferred old deletion date.
DO $$ DECLARE r record;BEGIN
 FOR r IN SELECT DISTINCT owner_id,domain FROM markos_runtime.events LOOP
  PERFORM markos_runtime.refresh_terminal(r.owner_id,r.domain);
 END LOOP;
END $$;
