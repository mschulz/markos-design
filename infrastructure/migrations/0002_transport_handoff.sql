-- Preserve 0001 bytes: transport retry/review state is an additive migration.
ALTER TABLE markos_runtime.outbox ADD COLUMN available_at timestamptz NOT NULL DEFAULT clock_timestamp();
ALTER TABLE markos_runtime.outbox ADD COLUMN attempts integer NOT NULL DEFAULT 0;
ALTER TABLE markos_runtime.outbox ADD COLUMN last_failure text;
CREATE FUNCTION markos_runtime.runtime_identity()
RETURNS TABLE(service_id text,owner_id uuid,domain text,can_publish boolean,can_consume boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,markos_runtime
AS $$ SELECT i.service_id,i.owner_id,i.domain,i.can_publish,i.can_consume
 FROM markos_runtime.service_identities i WHERE i.login_role=session_user $$;
DROP FUNCTION markos_runtime.claim_outbox(integer,integer);
CREATE FUNCTION markos_runtime.claim_outbox(p_limit integer,p_lease_seconds integer)
RETURNS TABLE(event_id uuid,wire bytea,fence bigint,attempts integer,consumers text[])
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime
AS $$ DECLARE principal markos_runtime.service_identities;
BEGIN
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities
 WHERE login_role=session_user AND can_publish;
 IF p_limit NOT BETWEEN 1 AND 10 OR p_lease_seconds NOT BETWEEN 1 AND 300
 THEN RAISE EXCEPTION 'invalid claim budget'; END IF;
 RETURN QUERY WITH candidates AS (
 SELECT o.owner_id,o.domain,o.event_id FROM markos_runtime.outbox o
 WHERE o.owner_id=principal.owner_id AND o.domain=principal.domain AND o.publisher_id=principal.service_id
 AND ((o.state='pending' AND o.available_at<=clock_timestamp())
 OR (o.state='leased' AND o.lease_until<=clock_timestamp()))
 ORDER BY o.created_at,o.event_id FOR UPDATE SKIP LOCKED LIMIT p_limit
 ), claimed AS (
 UPDATE markos_runtime.outbox o SET state='leased',fence=o.fence+1,
 lease_until=clock_timestamp()+make_interval(secs=>p_lease_seconds)
 FROM candidates c WHERE (o.owner_id,o.domain,o.event_id)=(c.owner_id,c.domain,c.event_id)
 RETURNING o.event_id,o.owner_id,o.domain,o.fence,o.attempts
 ) SELECT c.event_id,e.wire,c.fence,c.attempts,
 ARRAY(SELECT ec.consumer_id FROM markos_runtime.event_consumers ec
 WHERE (ec.owner_id,ec.domain,ec.event_id)=(c.owner_id,c.domain,c.event_id) ORDER BY ec.consumer_id)
 FROM claimed c JOIN markos_runtime.events e ON (e.owner_id,e.domain,e.event_id)=(c.owner_id,c.domain,c.event_id);
END $$;
CREATE FUNCTION markos_runtime.retry_outbox(p_event uuid,p_fence bigint,p_delay integer,p_code text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime
AS $$ DECLARE principal markos_runtime.service_identities;
BEGIN
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities WHERE login_role=session_user AND can_publish;
 IF p_delay NOT BETWEEN 1 AND 300 OR p_code NOT IN ('topology','confirm','unavailable','expired')
 THEN RAISE EXCEPTION 'invalid retry'; END IF;
 UPDATE markos_runtime.outbox SET state='pending',lease_until=NULL,attempts=attempts+1,
 available_at=clock_timestamp()+make_interval(secs=>p_delay),last_failure=p_code
 WHERE owner_id=principal.owner_id AND domain=principal.domain AND event_id=p_event
 AND publisher_id=principal.service_id AND state='leased' AND fence=p_fence AND lease_until>clock_timestamp();
 RETURN FOUND;
END $$;
CREATE TABLE markos_runtime.delivery_reviews (
 owner_id uuid NOT NULL,domain text NOT NULL,service_id text NOT NULL,
 review_id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
 body_hash text NOT NULL CHECK(body_hash ~ '^[0-9a-f]{64}$'),
 reason text NOT NULL CHECK(reason IN ('invalid','unauthorized','conflict','expired','oversize')),
 body_bytes integer NOT NULL CHECK(body_bytes>=0), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,service_id,body_hash,reason)
);
CREATE TABLE markos_runtime.review_outbox (
 review_id uuid PRIMARY KEY REFERENCES markos_runtime.delivery_reviews(review_id),
 owner_id uuid NOT NULL,domain text NOT NULL,service_id text NOT NULL,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','leased','published')),
 fence bigint NOT NULL DEFAULT 0,lease_until timestamptz,attempts integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT clock_timestamp(),published_at timestamptz,
 CHECK((state='leased')=(lease_until IS NOT NULL))
);
ALTER TABLE markos_runtime.delivery_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE markos_runtime.review_outbox ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.delivery_reviews TO markos_runtime_client
 USING(markos_runtime.can_read(owner_id,domain,service_id));
CREATE POLICY scoped ON markos_runtime.review_outbox TO markos_runtime_client
 USING(markos_runtime.can_read(owner_id,domain,service_id));
GRANT SELECT ON markos_runtime.delivery_reviews,markos_runtime.review_outbox TO markos_runtime_client;
CREATE FUNCTION markos_runtime.review_delivery(p_hash text,p_reason text,p_bytes integer)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime
AS $$ DECLARE principal markos_runtime.service_identities; result uuid;
BEGIN
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities WHERE login_role=session_user;
 INSERT INTO markos_runtime.delivery_reviews(owner_id,domain,service_id,body_hash,reason,body_bytes)
 VALUES(principal.owner_id,principal.domain,principal.service_id,p_hash,p_reason,p_bytes)
 ON CONFLICT DO NOTHING;
 SELECT review_id INTO STRICT result FROM markos_runtime.delivery_reviews
 WHERE owner_id=principal.owner_id AND domain=principal.domain AND service_id=principal.service_id
 AND body_hash=p_hash AND reason=p_reason;
 INSERT INTO markos_runtime.review_outbox(review_id,owner_id,domain,service_id)
 VALUES(result,principal.owner_id,principal.domain,principal.service_id) ON CONFLICT DO NOTHING;
 RETURN result;
END $$;
CREATE FUNCTION markos_runtime.claim_reviews(p_limit integer,p_seconds integer)
RETURNS TABLE(review_id uuid,owner_id uuid,domain text,body_hash text,reason text,fence bigint,attempts integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime
AS $$ DECLARE principal markos_runtime.service_identities;
BEGIN
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities WHERE login_role=session_user;
 IF p_limit NOT BETWEEN 1 AND 10 OR p_seconds NOT BETWEEN 1 AND 300 THEN RAISE EXCEPTION 'invalid claim'; END IF;
 RETURN QUERY WITH candidates AS (
 SELECT o.review_id FROM markos_runtime.review_outbox o
 WHERE o.owner_id=principal.owner_id AND o.domain=principal.domain AND o.service_id=principal.service_id
 AND ((o.state='pending' AND o.available_at<=clock_timestamp()) OR (o.state='leased' AND o.lease_until<=clock_timestamp()))
 ORDER BY o.available_at,o.review_id FOR UPDATE SKIP LOCKED LIMIT p_limit
 ), claimed AS (
 UPDATE markos_runtime.review_outbox o SET state='leased',fence=o.fence+1,
 lease_until=clock_timestamp()+make_interval(secs=>p_seconds)
 FROM candidates c WHERE o.review_id=c.review_id RETURNING o.*
 ) SELECT c.review_id,c.owner_id,c.domain,r.body_hash,r.reason,c.fence,c.attempts
 FROM claimed c JOIN markos_runtime.delivery_reviews r ON r.review_id=c.review_id;
END $$;
CREATE FUNCTION markos_runtime.finish_review(p_review uuid,p_fence bigint,p_delay integer DEFAULT NULL)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime
AS $$ DECLARE principal markos_runtime.service_identities;
BEGIN
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities WHERE login_role=session_user;
 IF p_delay IS NOT NULL AND p_delay NOT BETWEEN 1 AND 300 THEN RAISE EXCEPTION 'invalid retry'; END IF;
 UPDATE markos_runtime.review_outbox SET
 state=CASE WHEN p_delay IS NULL THEN 'published' ELSE 'pending' END,lease_until=NULL,
 available_at=clock_timestamp()+make_interval(secs=>coalesce(p_delay,0)),
 attempts=attempts+CASE WHEN p_delay IS NULL THEN 0 ELSE 1 END,
 published_at=CASE WHEN p_delay IS NULL THEN clock_timestamp() ELSE NULL END
 WHERE review_id=p_review AND owner_id=principal.owner_id AND domain=principal.domain
 AND service_id=principal.service_id AND state='leased' AND fence=p_fence AND lease_until>clock_timestamp();
 RETURN FOUND;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA markos_runtime FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_runtime.runtime_identity(),markos_runtime.claim_outbox(integer,integer),
 markos_runtime.retry_outbox(uuid,bigint,integer,text),markos_runtime.review_delivery(text,text,integer),
 markos_runtime.claim_reviews(integer,integer),markos_runtime.finish_review(uuid,bigint,integer)
 TO markos_runtime_client;

-- Permanent registration/intent rejection is distinct from transient database failure.
ALTER FUNCTION markos_runtime.handoff(uuid,text) RENAME TO handoff_v1;
REVOKE ALL ON FUNCTION markos_runtime.handoff_v1(uuid,text) FROM markos_runtime_client;
CREATE FUNCTION markos_runtime.handoff(p_event uuid,p_hash text)
RETURNS TABLE(status text,task_id uuid) LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_runtime AS $$
BEGIN
 RETURN QUERY SELECT * FROM markos_runtime.handoff_v1(p_event,p_hash);
EXCEPTION WHEN no_data_found THEN
 RAISE EXCEPTION USING ERRCODE='M0001',MESSAGE='handoff not registered';
WHEN raise_exception THEN
 IF SQLERRM='consumer not intended' THEN
  RAISE EXCEPTION USING ERRCODE='M0001',MESSAGE='handoff not intended';
 ELSE RAISE; END IF;
END $$;
ALTER TABLE markos_runtime.outbox DROP CONSTRAINT outbox_state_check;
ALTER TABLE markos_runtime.outbox ADD CONSTRAINT outbox_state_check CHECK(state IN ('pending','leased','published','review'));
CREATE FUNCTION markos_runtime.hold_expired(p_event uuid,p_fence bigint)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime
AS $$ DECLARE principal markos_runtime.service_identities;
BEGIN
 SELECT * INTO STRICT principal FROM markos_runtime.service_identities WHERE login_role=session_user AND can_publish;
 UPDATE markos_runtime.outbox SET state='review',lease_until=NULL,last_failure='expired'
 WHERE owner_id=principal.owner_id AND domain=principal.domain AND event_id=p_event
 AND publisher_id=principal.service_id AND state='leased' AND fence=p_fence AND lease_until>clock_timestamp();
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION markos_runtime.handoff(uuid,text),markos_runtime.hold_expired(uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_runtime.handoff(uuid,text),markos_runtime.hold_expired(uuid,bigint) TO markos_runtime_client;
