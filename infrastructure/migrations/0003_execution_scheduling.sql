-- Additive M0-D. Only the explicit migration role applies this file.
ALTER TABLE markos_runtime.service_identities
 ADD COLUMN can_execute boolean NOT NULL DEFAULT false,
 ADD COLUMN can_schedule boolean NOT NULL DEFAULT false;
ALTER TABLE markos_runtime.tasks DROP CONSTRAINT tasks_state_check;
ALTER TABLE markos_runtime.tasks ADD CONSTRAINT tasks_state_check
 CHECK(state IN ('pending','running','completed','failed','review'));
ALTER TABLE markos_runtime.tasks
 ADD COLUMN available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 ADD COLUMN attempts integer NOT NULL DEFAULT 0,
 ADD COLUMN fence bigint NOT NULL DEFAULT 0,
 ADD COLUMN lease_until timestamptz,
 ADD COLUMN executor_id uuid,
 ADD COLUMN slot_id integer;
ALTER TABLE markos_runtime.tasks ADD CONSTRAINT tasks_global_id UNIQUE(task_id);
CREATE INDEX tasks_ready ON markos_runtime.tasks(consumer_id,state,available_at,lease_until);
CREATE TABLE markos_runtime.execution_slots (
 slot_id integer PRIMARY KEY CHECK(slot_id BETWEEN 1 AND 2),
 task_id uuid, fence bigint, lease_until timestamptz
);
INSERT INTO markos_runtime.execution_slots(slot_id) VALUES(1),(2);
REVOKE ALL ON markos_runtime.execution_slots FROM PUBLIC,markos_runtime_client;
CREATE TABLE markos_runtime.task_attempts (
 owner_id uuid NOT NULL, domain text NOT NULL, consumer_id text NOT NULL, task_id uuid NOT NULL,
 fence bigint NOT NULL, executor_id uuid NOT NULL,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
 outcome text NOT NULL CHECK(outcome IN ('running','completed','execution','timeout','cancelled','abandoned','review')),
 PRIMARY KEY(owner_id,domain,consumer_id,task_id,fence),
 FOREIGN KEY(owner_id,domain,consumer_id,task_id) REFERENCES markos_runtime.tasks
);
CREATE TABLE markos_runtime.execution_reviews (
 owner_id uuid NOT NULL, domain text NOT NULL, consumer_id text NOT NULL, task_id uuid NOT NULL,
 reason text NOT NULL CHECK(reason IN ('expired','source_unavailable','retry_exhausted','unsupported')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,consumer_id,task_id),
 FOREIGN KEY(owner_id,domain,consumer_id,task_id) REFERENCES markos_runtime.tasks
);
ALTER TABLE markos_runtime.task_attempts ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.task_attempts TO markos_runtime_client
 USING(markos_runtime.can_read(owner_id,domain,consumer_id));
ALTER TABLE markos_runtime.execution_reviews ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.execution_reviews TO markos_runtime_client
 USING(markos_runtime.can_read(owner_id,domain,consumer_id));
GRANT SELECT ON markos_runtime.task_attempts,markos_runtime.execution_reviews TO markos_runtime_client;

-- All execution mutations share one short database-local lock. No network/handler
-- work occurs in this transaction. This makes the two-slot limit global, not per replica.
CREATE FUNCTION markos_runtime.claim_task(p_executor uuid,p_seconds integer)
RETURNS TABLE(task_id uuid,wire bytea,fence bigint,attempt integer,lease_until timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE principal markos_runtime.service_identities; t markos_runtime.tasks;
 ev markos_runtime.events; chosen integer; why text; deadline timestamptz;
BEGIN
 IF p_executor IS NULL OR p_executor='00000000-0000-0000-0000-000000000000'
 OR p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 300 THEN RAISE EXCEPTION 'invalid execution budget'; END IF;
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities
 WHERE login_role=session_user AND can_consume AND can_execute;
 PERFORM pg_advisory_xact_lock(712093482);
 FOR t IN SELECT * FROM markos_runtime.tasks x
 WHERE x.owner_id=principal.owner_id AND x.domain=principal.domain AND x.consumer_id=principal.service_id
 AND ((x.state='pending' AND x.available_at<=clock_timestamp())
 OR (x.state='running' AND x.lease_until<=clock_timestamp()))
 ORDER BY x.created_at,x.task_id LIMIT 100 FOR UPDATE LOOP
  SELECT * INTO STRICT ev FROM markos_runtime.events e
  WHERE (e.owner_id,e.domain,e.event_id)=(t.owner_id,t.domain,t.event_id);
  why:=NULL;
  IF ev.expires_at<=clock_timestamp() THEN why:='expired';
  ELSIF convert_from(ev.wire,'UTF8')::jsonb->'payload_ref' IS DISTINCT FROM 'null'::jsonb
    AND convert_from(ev.wire,'UTF8')::jsonb ? 'payload_ref' THEN why:='source_unavailable';
  ELSIF convert_from(ev.wire,'UTF8')::jsonb->>'event_type'<>'foundation.noop.requested' THEN why:='unsupported';
  ELSIF t.attempts>=3 THEN why:='retry_exhausted'; END IF;
  IF why IS NOT NULL THEN
   UPDATE markos_runtime.tasks x SET state='review',lease_until=NULL,executor_id=NULL,slot_id=NULL
   WHERE (x.owner_id,x.domain,x.consumer_id,x.task_id)=(t.owner_id,t.domain,t.consumer_id,t.task_id);
   INSERT INTO markos_runtime.execution_reviews VALUES(t.owner_id,t.domain,t.consumer_id,t.task_id,why,clock_timestamp())
   ON CONFLICT DO NOTHING;
   UPDATE markos_runtime.task_attempts a SET outcome='review',finished_at=clock_timestamp()
   WHERE (a.owner_id,a.domain,a.consumer_id,a.task_id)=(t.owner_id,t.domain,t.consumer_id,t.task_id) AND a.outcome='running';
   UPDATE markos_runtime.execution_slots s SET task_id=NULL,fence=NULL,lease_until=NULL
   WHERE s.task_id=t.task_id AND s.fence=t.fence;
   CONTINUE;
  END IF;
  SELECT s.slot_id INTO chosen FROM markos_runtime.execution_slots s
  WHERE s.lease_until IS NULL OR s.lease_until<=clock_timestamp() ORDER BY s.slot_id LIMIT 1 FOR UPDATE;
  IF NOT FOUND THEN RETURN; END IF;
  deadline:=clock_timestamp()+make_interval(secs=>p_seconds);
  UPDATE markos_runtime.task_attempts a SET outcome='abandoned',finished_at=clock_timestamp()
  WHERE (a.owner_id,a.domain,a.consumer_id,a.task_id)=(t.owner_id,t.domain,t.consumer_id,t.task_id) AND a.outcome='running';
  UPDATE markos_runtime.tasks x SET state='running',attempts=x.attempts+1,fence=x.fence+1,
   lease_until=deadline,executor_id=p_executor,slot_id=chosen
  WHERE (x.owner_id,x.domain,x.consumer_id,x.task_id)=(t.owner_id,t.domain,t.consumer_id,t.task_id)
  RETURNING * INTO t;
  UPDATE markos_runtime.execution_slots SET task_id=t.task_id,fence=t.fence,lease_until=deadline WHERE slot_id=chosen;
  INSERT INTO markos_runtime.task_attempts(owner_id,domain,consumer_id,task_id,fence,executor_id,outcome)
  VALUES(t.owner_id,t.domain,t.consumer_id,t.task_id,t.fence,p_executor,'running');
  RETURN QUERY SELECT t.task_id,ev.wire,t.fence,t.attempts,deadline; RETURN;
 END LOOP;
END $$;

CREATE FUNCTION markos_runtime.finish_task(p_task uuid,p_fence bigint,p_executor uuid,p_failure text DEFAULT NULL,p_delay integer DEFAULT 1)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE principal markos_runtime.service_identities; t markos_runtime.tasks; ev markos_runtime.events; why text;
BEGIN
 IF p_failure IS NOT NULL AND (p_failure NOT IN ('execution','timeout','cancelled') OR p_delay IS NULL OR p_delay NOT BETWEEN 1 AND 300)
 THEN RAISE EXCEPTION 'invalid retry'; END IF;
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities
 WHERE login_role=session_user AND can_consume AND can_execute;
 PERFORM pg_advisory_xact_lock(712093482);
 SELECT * INTO t FROM markos_runtime.tasks x
 WHERE x.owner_id=principal.owner_id AND x.domain=principal.domain AND x.consumer_id=principal.service_id
 AND x.task_id=p_task AND x.state='running' AND x.fence=p_fence AND x.executor_id=p_executor
 AND x.lease_until>clock_timestamp() FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 PERFORM 1 FROM markos_runtime.execution_slots s WHERE s.slot_id=t.slot_id AND s.task_id=t.task_id
 AND s.fence=t.fence AND s.lease_until>clock_timestamp();
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO STRICT ev FROM markos_runtime.events e
 WHERE (e.owner_id,e.domain,e.event_id)=(t.owner_id,t.domain,t.event_id);
 why:=CASE WHEN ev.expires_at<=clock_timestamp() THEN 'expired'
  WHEN p_failure IS NOT NULL AND t.attempts>=3 THEN 'retry_exhausted' ELSE NULL END;
 IF why IS NOT NULL THEN
  INSERT INTO markos_runtime.execution_reviews VALUES(t.owner_id,t.domain,t.consumer_id,t.task_id,why,clock_timestamp()) ON CONFLICT DO NOTHING;
 ELSIF p_failure IS NULL THEN
  INSERT INTO markos_runtime.task_results(owner_id,domain,consumer_id,task_id,result)
  VALUES(t.owner_id,t.domain,t.consumer_id,t.task_id,
   jsonb_build_object('kind','foundation.noop','event_id',t.event_id,'effects','none'));
 END IF;
 UPDATE markos_runtime.tasks x SET state=CASE WHEN why IS NOT NULL THEN 'review' WHEN p_failure IS NULL THEN 'completed' ELSE 'pending' END,
  available_at=clock_timestamp()+make_interval(secs=>p_delay),lease_until=NULL,executor_id=NULL,slot_id=NULL
 WHERE (x.owner_id,x.domain,x.consumer_id,x.task_id)=(t.owner_id,t.domain,t.consumer_id,t.task_id);
 UPDATE markos_runtime.task_attempts a SET finished_at=clock_timestamp(),
  outcome=CASE WHEN why IS NOT NULL THEN 'review' ELSE coalesce(p_failure,'completed') END
 WHERE (a.owner_id,a.domain,a.consumer_id,a.task_id,a.fence)=(t.owner_id,t.domain,t.consumer_id,t.task_id,t.fence);
 UPDATE markos_runtime.execution_slots SET task_id=NULL,fence=NULL,lease_until=NULL WHERE slot_id=t.slot_id;
 RETURN why IS NULL;
END $$;

CREATE FUNCTION markos_runtime.renew_task(p_task uuid,p_fence bigint,p_executor uuid,p_seconds integer)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE principal markos_runtime.service_identities; t markos_runtime.tasks; deadline timestamptz;
BEGIN
 IF p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 300 THEN RAISE EXCEPTION 'invalid renewal'; END IF;
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities WHERE login_role=session_user AND can_consume AND can_execute;
 PERFORM pg_advisory_xact_lock(712093482);
 SELECT * INTO t FROM markos_runtime.tasks x WHERE x.owner_id=principal.owner_id AND x.domain=principal.domain
 AND x.consumer_id=principal.service_id AND x.task_id=p_task AND x.fence=p_fence AND x.executor_id=p_executor
 AND x.state='running' AND x.lease_until>clock_timestamp() FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT least(clock_timestamp()+make_interval(secs=>p_seconds),a.started_at+interval '300 seconds') INTO deadline
 FROM markos_runtime.task_attempts a WHERE (a.owner_id,a.domain,a.consumer_id,a.task_id,a.fence)=(t.owner_id,t.domain,t.consumer_id,t.task_id,t.fence);
 IF deadline<=clock_timestamp() THEN RETURN false; END IF;
 UPDATE markos_runtime.execution_slots s SET lease_until=deadline WHERE s.slot_id=t.slot_id AND s.task_id=t.task_id AND s.fence=t.fence AND s.lease_until>clock_timestamp();
 IF NOT FOUND THEN RETURN false; END IF;
 UPDATE markos_runtime.tasks x SET lease_until=deadline WHERE (x.owner_id,x.domain,x.consumer_id,x.task_id)=(t.owner_id,t.domain,t.consumer_id,t.task_id);
 RETURN true;
END $$;

CREATE TABLE markos_runtime.schedules (
 owner_id uuid NOT NULL,domain text NOT NULL,scheduler_id text NOT NULL,schedule_id uuid NOT NULL,
 version integer NOT NULL CHECK(version>0),definition jsonb NOT NULL,
 next_due timestamptz NOT NULL,enabled boolean NOT NULL DEFAULT true,
 PRIMARY KEY(owner_id,domain,scheduler_id,schedule_id)
);
CREATE TABLE markos_runtime.schedule_versions (
 owner_id uuid NOT NULL,domain text NOT NULL,scheduler_id text NOT NULL,schedule_id uuid NOT NULL,
 version integer NOT NULL,definition jsonb NOT NULL,recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,scheduler_id,schedule_id,version)
);
ALTER TABLE markos_runtime.schedule_versions ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.schedule_versions TO markos_runtime_client USING(markos_runtime.can_read(owner_id,domain,scheduler_id));
GRANT SELECT ON markos_runtime.schedule_versions TO markos_runtime_client;
CREATE TABLE markos_runtime.schedule_occurrences (
 owner_id uuid NOT NULL,domain text NOT NULL,scheduler_id text NOT NULL,schedule_id uuid NOT NULL,
 version integer NOT NULL,due_at timestamptz NOT NULL,event_id uuid NOT NULL,
 PRIMARY KEY(owner_id,domain,scheduler_id,schedule_id,version,due_at),
 FOREIGN KEY(owner_id,domain,event_id) REFERENCES markos_runtime.events
);
CREATE TABLE markos_runtime.schedule_scans (
 scan_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 owner_id uuid NOT NULL,domain text NOT NULL,scheduler_id text NOT NULL,schedule_id uuid NOT NULL,
 version integer NOT NULL,from_due timestamptz NOT NULL,next_due timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),audit jsonb NOT NULL
);
ALTER TABLE markos_runtime.schedules ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.schedules TO markos_runtime_client USING(markos_runtime.can_read(owner_id,domain,scheduler_id));
ALTER TABLE markos_runtime.schedule_occurrences ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.schedule_occurrences TO markos_runtime_client USING(markos_runtime.can_read(owner_id,domain,scheduler_id));
ALTER TABLE markos_runtime.schedule_scans ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.schedule_scans TO markos_runtime_client USING(markos_runtime.can_read(owner_id,domain,scheduler_id));
GRANT SELECT ON markos_runtime.schedules,markos_runtime.schedule_occurrences,markos_runtime.schedule_scans TO markos_runtime_client;

-- SQL callers receive the same strict shape/budget checks as the Python API.
CREATE FUNCTION markos_runtime.valid_schedule(d jsonb)
RETURNS boolean LANGUAGE plpgsql STABLE SET search_path=pg_catalog,markos_runtime AS $$
DECLARE k text; required text[]:=ARRAY['schema_version','schedule_id','version','owner_id','domain','kind','start_at','timezone','interval_seconds','hour','minute','fold','gap','missed_policy','catch_up_limit','run_grace_seconds','max_age_seconds'];
BEGIN
 IF jsonb_typeof(d) IS DISTINCT FROM 'object' OR NOT(d ?& required) OR d-required<>'{}'::jsonb THEN RETURN false; END IF;
 FOREACH k IN ARRAY ARRAY['schema_version','version','catch_up_limit','run_grace_seconds','max_age_seconds'] LOOP
  IF jsonb_typeof(d->k) IS DISTINCT FROM 'number' OR d->>k !~ '^[0-9]+$' THEN RETURN false; END IF;
 END LOOP;
 FOREACH k IN ARRAY ARRAY['schedule_id','owner_id','domain','kind','start_at','timezone','fold','gap','missed_policy'] LOOP
  IF jsonb_typeof(d->k) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
 END LOOP;
 IF d->>'schema_version'<>'1' OR (d->>'version')::bigint NOT BETWEEN 1 AND 2147483647
 OR (d->>'schedule_id')::uuid='00000000-0000-0000-0000-000000000000'
 OR (d->>'owner_id')::uuid='00000000-0000-0000-0000-000000000000'
 OR d->>'domain' NOT IN ('personal','research','work') OR d->>'kind' NOT IN ('interval','daily')
 OR d->>'missed_policy' NOT IN ('catch_up','coalesce','skip') OR d->>'fold' NOT IN ('first','second') OR d->>'gap'<>'shift_forward'
 OR (d->>'run_grace_seconds')::integer NOT BETWEEN 0 AND 59
 OR (d->>'catch_up_limit')::integer NOT BETWEEN 1 AND 100 OR (d->>'max_age_seconds')::integer NOT BETWEEN 60 AND 2592000
 OR d->>'start_at' !~ '[Tt].*(Z|[+]00:00)$'
 OR NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name=d->>'timezone') THEN RETURN false; END IF;
 PERFORM (d->>'start_at')::timestamptz;
 IF d->>'kind'='interval' THEN
  RETURN jsonb_typeof(d->'interval_seconds')='number' AND d->>'interval_seconds' ~ '^[0-9]+$'
   AND (d->>'interval_seconds')::integer BETWEEN 60 AND 2678400 AND d->'hour'='null'::jsonb AND d->'minute'='null'::jsonb;
 END IF;
 RETURN d->'interval_seconds'='null'::jsonb AND jsonb_typeof(d->'hour')='number' AND d->>'hour' ~ '^[0-9]+$'
  AND (d->>'hour')::integer BETWEEN 0 AND 23 AND jsonb_typeof(d->'minute')='number' AND d->>'minute' ~ '^[0-9]+$'
  AND (d->>'minute')::integer BETWEEN 0 AND 59;
END $$;
CREATE FUNCTION markos_runtime.register_schedule(p_definition jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE principal markos_runtime.service_identities; existing markos_runtime.schedules;
BEGIN
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities WHERE login_role=session_user AND can_publish AND can_schedule;
 IF markos_runtime.valid_schedule(p_definition) IS DISTINCT FROM true
 OR p_definition->>'owner_id' IS DISTINCT FROM principal.owner_id::text OR p_definition->>'domain' IS DISTINCT FROM principal.domain
 OR p_definition->>'schema_version'<>'1' OR (p_definition->>'version')::integer NOT BETWEEN 1 AND 2147483647
 OR p_definition->>'kind' NOT IN ('interval','daily') OR p_definition->>'missed_policy' NOT IN ('catch_up','coalesce','skip')
 THEN RAISE EXCEPTION 'invalid schedule'; END IF;
 -- Immutable versions: an update must increment exactly one version. Old occurrences remain.
 INSERT INTO markos_runtime.schedules(owner_id,domain,scheduler_id,schedule_id,version,definition,next_due)
 VALUES(principal.owner_id,principal.domain,principal.service_id,(p_definition->>'schedule_id')::uuid,
 (p_definition->>'version')::integer,p_definition,(p_definition->>'start_at')::timestamptz)
 ON CONFLICT DO NOTHING;
 IF FOUND THEN
  INSERT INTO markos_runtime.schedule_versions(owner_id,domain,scheduler_id,schedule_id,version,definition)
  VALUES(principal.owner_id,principal.domain,principal.service_id,(p_definition->>'schedule_id')::uuid,(p_definition->>'version')::integer,p_definition);
  RETURN;
 END IF;
 SELECT * INTO STRICT existing FROM markos_runtime.schedules s WHERE s.owner_id=principal.owner_id AND s.domain=principal.domain
 AND s.scheduler_id=principal.service_id AND s.schedule_id=(p_definition->>'schedule_id')::uuid FOR UPDATE;
 IF existing.definition=p_definition THEN RETURN; END IF;
 IF (p_definition->>'version')::integer<>existing.version+1 THEN RAISE EXCEPTION 'stale schedule version'; END IF;
 INSERT INTO markos_runtime.schedule_versions(owner_id,domain,scheduler_id,schedule_id,version,definition)
 VALUES(principal.owner_id,principal.domain,principal.service_id,existing.schedule_id,(p_definition->>'version')::integer,p_definition);
 UPDATE markos_runtime.schedules SET version=(p_definition->>'version')::integer,definition=p_definition,
 next_due=(p_definition->>'start_at')::timestamptz WHERE owner_id=principal.owner_id AND domain=principal.domain AND scheduler_id=principal.service_id AND schedule_id=existing.schedule_id;
END $$;

CREATE FUNCTION markos_runtime.due_schedules(p_limit integer)
RETURNS TABLE(definition jsonb,next_due timestamptz,db_now timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE principal markos_runtime.service_identities;
BEGIN
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities WHERE login_role=session_user AND can_publish AND can_schedule;
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 10 THEN RAISE EXCEPTION 'invalid scan budget'; END IF;
 RETURN QUERY SELECT s.definition,s.next_due,clock_timestamp() FROM markos_runtime.schedules s
 WHERE s.owner_id=principal.owner_id AND s.domain=principal.domain AND s.scheduler_id=principal.service_id
 AND s.enabled AND s.next_due<=clock_timestamp() ORDER BY s.next_due,s.schedule_id LIMIT p_limit FOR UPDATE SKIP LOCKED;
END $$;

CREATE FUNCTION markos_runtime.advance_schedule(p_id uuid,p_version integer,p_expected timestamptz,p_next timestamptz,p_entries jsonb,p_audit jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE principal markos_runtime.service_identities; s markos_runtime.schedules; entry jsonb; body bytea; doc jsonb;
BEGIN
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities WHERE login_role=session_user AND can_publish AND can_schedule;
 SELECT * INTO STRICT s FROM markos_runtime.schedules x WHERE x.owner_id=principal.owner_id AND x.domain=principal.domain
 AND x.scheduler_id=principal.service_id AND x.schedule_id=p_id FOR UPDATE;
 IF p_version IS NULL OR p_expected IS NULL OR NOT s.enabled OR s.version<>p_version OR s.next_due<>p_expected OR p_next IS NULL OR p_next<=p_expected
 OR jsonb_typeof(p_entries) IS DISTINCT FROM 'array' OR jsonb_array_length(p_entries)>100
 OR jsonb_typeof(p_audit) IS DISTINCT FROM 'object' OR octet_length(p_audit::text)>4096 THEN RAISE EXCEPTION 'invalid schedule transition'; END IF;
 IF p_next>p_expected+interval '62 days'
 OR NOT(p_audit ?& ARRAY['policy','covered','skipped','created','pending','age_limit_seconds'])
 OR p_audit->>'policy' IS DISTINCT FROM s.definition->>'missed_policy'
 OR jsonb_typeof(p_audit->'created') IS DISTINCT FROM 'number'
 OR jsonb_typeof(p_audit->'covered') IS DISTINCT FROM 'number'
 OR jsonb_typeof(p_audit->'skipped') IS DISTINCT FROM 'number'
 OR jsonb_typeof(p_audit->'pending') IS DISTINCT FROM 'boolean'
 OR (p_audit->>'created')::integer<>jsonb_array_length(p_entries)
 OR (p_audit->>'covered')::integer NOT BETWEEN 1 AND 44641
 OR (p_audit->>'skipped')::integer<>(p_audit->>'covered')::integer-jsonb_array_length(p_entries)
 OR (p_audit->>'skipped')::integer<0
 OR p_audit->'age_limit_seconds' IS DISTINCT FROM s.definition->'max_age_seconds'
 THEN RAISE EXCEPTION 'invalid schedule audit'; END IF;
 FOR entry IN SELECT * FROM jsonb_array_elements(p_entries) LOOP
  body:=convert_to(entry->>'wire','UTF8');doc:=convert_from(body,'UTF8')::jsonb;
  IF entry->>'due_at' IS NULL OR entry->>'wire' IS NULL OR (entry->>'due_at')::timestamptz<p_expected OR (entry->>'due_at')::timestamptz>=p_next
  OR (entry->>'due_at')::timestamptz>clock_timestamp()
  OR (doc->>'occurred_at')::timestamptz IS DISTINCT FROM (entry->>'due_at')::timestamptz
  OR doc->>'operation_key' IS DISTINCT FROM 'schedule:'||replace(p_id::text,'-','')||':v'||p_version::text||':'||
    ((extract(epoch FROM (entry->>'due_at')::timestamptz)*1000000)::bigint)::text
  OR doc->'payload' IS DISTINCT FROM '{"label":"scheduled synthetic noop"}'::jsonb
  OR doc->'payload_ref' IS DISTINCT FROM 'null'::jsonb
  THEN RAISE EXCEPTION 'invalid occurrence'; END IF;
  IF markos_runtime.append_event(body)<>'created' THEN RAISE EXCEPTION 'occurrence event conflict'; END IF;
  INSERT INTO markos_runtime.schedule_occurrences VALUES(principal.owner_id,principal.domain,principal.service_id,p_id,p_version,(entry->>'due_at')::timestamptz,(doc->>'event_id')::uuid);
 END LOOP;
 INSERT INTO markos_runtime.schedule_scans(owner_id,domain,scheduler_id,schedule_id,version,from_due,next_due,audit)
 VALUES(principal.owner_id,principal.domain,principal.service_id,p_id,p_version,p_expected,p_next,p_audit);
 UPDATE markos_runtime.schedules SET next_due=p_next WHERE owner_id=principal.owner_id AND domain=principal.domain AND scheduler_id=principal.service_id AND schedule_id=p_id;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA markos_runtime FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_runtime.claim_task(uuid,integer),markos_runtime.finish_task(uuid,bigint,uuid,text,integer),
 markos_runtime.renew_task(uuid,bigint,uuid,integer),markos_runtime.register_schedule(jsonb),markos_runtime.due_schedules(integer),
 markos_runtime.advance_schedule(uuid,integer,timestamptz,timestamptz,jsonb,jsonb) TO markos_runtime_client;

CREATE TABLE markos_runtime.schedule_controls (
 control_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 owner_id uuid NOT NULL,domain text NOT NULL,scheduler_id text NOT NULL,schedule_id uuid NOT NULL,
 version integer NOT NULL,enabled boolean NOT NULL,recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE markos_runtime.schedule_controls ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.schedule_controls TO markos_runtime_client USING(markos_runtime.can_read(owner_id,domain,scheduler_id));
GRANT SELECT ON markos_runtime.schedule_controls TO markos_runtime_client;
CREATE FUNCTION markos_runtime.enable_schedule(p_id uuid,p_version integer,p_enabled boolean)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE principal markos_runtime.service_identities;
BEGIN
 IF p_enabled IS NULL THEN RAISE EXCEPTION 'invalid enable flag'; END IF;
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities WHERE login_role=session_user AND can_publish AND can_schedule;
 UPDATE markos_runtime.schedules SET enabled=p_enabled WHERE owner_id=principal.owner_id AND domain=principal.domain AND scheduler_id=principal.service_id AND schedule_id=p_id AND version=p_version;
 IF NOT FOUND THEN RETURN false; END IF;
 INSERT INTO markos_runtime.schedule_controls(owner_id,domain,scheduler_id,schedule_id,version,enabled)
 VALUES(principal.owner_id,principal.domain,principal.service_id,p_id,p_version,p_enabled);
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION markos_runtime.enable_schedule(uuid,integer,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_runtime.enable_schedule(uuid,integer,boolean) TO markos_runtime_client;
