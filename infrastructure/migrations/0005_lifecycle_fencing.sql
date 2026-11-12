-- M0-F: additive lifecycle/restore fencing; never applied by service startup.
CREATE TABLE markos_runtime.runtime_epoch (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), epoch uuid NOT NULL DEFAULT gen_random_uuid()
);
INSERT INTO markos_runtime.runtime_epoch(singleton) VALUES(true);
REVOKE ALL ON markos_runtime.runtime_epoch FROM PUBLIC,markos_runtime_client;
CREATE FUNCTION markos_runtime.current_epoch() RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,markos_runtime AS $$ SELECT epoch FROM markos_runtime.runtime_epoch $$;
CREATE FUNCTION markos_runtime.valid_epoch(p_epoch uuid) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 PERFORM pg_advisory_xact_lock(712093482);
 RETURN p_epoch IS NOT NULL AND p_epoch=markos_runtime.current_epoch();
END $$;
CREATE TABLE markos_runtime.service_heartbeats (
 owner_id uuid NOT NULL,domain text NOT NULL,service_id text NOT NULL,component text NOT NULL,
 instance_id uuid NOT NULL,epoch uuid NOT NULL,status text NOT NULL,
 seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,service_id,component,instance_id),
 CHECK(component IN ('publisher','consumer','worker','scheduler','recovery','review')),
 CHECK(status IN ('ready','degraded','draining'))
);
ALTER TABLE markos_runtime.service_heartbeats ENABLE ROW LEVEL SECURITY;
CREATE POLICY scoped ON markos_runtime.service_heartbeats TO markos_runtime_client
 USING(markos_runtime.can_read(owner_id,domain,service_id));
GRANT SELECT ON markos_runtime.service_heartbeats TO markos_runtime_client;
CREATE FUNCTION markos_runtime.heartbeat(p_component text,p_instance uuid,p_epoch uuid,p_status text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;
BEGIN
 SELECT * INTO STRICT p FROM markos_runtime.service_identities WHERE login_role=session_user;
 IF p_instance IS NULL OR p_instance='00000000-0000-0000-0000-000000000000'
 OR p_component IS NULL OR p_status IS NULL OR NOT markos_runtime.valid_epoch(p_epoch) THEN RAISE EXCEPTION 'invalid heartbeat'; END IF;
 DELETE FROM markos_runtime.service_heartbeats WHERE owner_id=p.owner_id AND domain=p.domain AND service_id=p.service_id
 AND component=p_component AND seen_at<clock_timestamp()-interval '45 seconds';
 IF (SELECT count(*) FROM markos_runtime.service_heartbeats WHERE owner_id=p.owner_id AND domain=p.domain
 AND service_id=p.service_id AND component=p_component AND instance_id<>p_instance)>=4 THEN RAISE EXCEPTION 'instance budget'; END IF;
 INSERT INTO markos_runtime.service_heartbeats VALUES(p.owner_id,p.domain,p.service_id,p_component,p_instance,p_epoch,p_status,clock_timestamp())
 ON CONFLICT(owner_id,domain,service_id,component,instance_id) DO UPDATE SET seen_at=EXCLUDED.seen_at,status=EXCLUDED.status,epoch=EXCLUDED.epoch;
END $$;

CREATE FUNCTION markos_runtime.claim_outbox_epoch(p_limit integer,p_seconds integer) RETURNS TABLE(event_id uuid,wire bytea,fence bigint,attempts integer,consumers text[],epoch uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 PERFORM pg_advisory_xact_lock(712093482);
 RETURN QUERY SELECT c.*,markos_runtime.current_epoch() FROM markos_runtime.claim_outbox(p_limit,p_seconds) c;
END $$;

CREATE FUNCTION markos_runtime.claim_reviews_epoch(p_limit integer,p_seconds integer) RETURNS TABLE(review_id uuid,owner_id uuid,domain text,body_hash text,reason text,fence bigint,attempts integer,epoch uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 PERFORM pg_advisory_xact_lock(712093482);
 RETURN QUERY SELECT c.*,markos_runtime.current_epoch() FROM markos_runtime.claim_reviews(p_limit,p_seconds) c;
END $$;

CREATE FUNCTION markos_runtime.claim_task_epoch(p_executor uuid,p_seconds integer) RETURNS TABLE(task_id uuid,wire bytea,fence bigint,attempt integer,lease_until timestamptz,epoch uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 PERFORM pg_advisory_xact_lock(712093482);
 RETURN QUERY SELECT c.*,markos_runtime.current_epoch() FROM markos_runtime.claim_task(p_executor,p_seconds) c;
END $$;

CREATE FUNCTION markos_runtime.record_publication_epoch(p_event uuid,p_fence bigint,p_epoch uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 IF NOT markos_runtime.valid_epoch(p_epoch) THEN RETURN false; END IF;
 RETURN markos_runtime.record_publication(p_event,p_fence);
END $$;

CREATE FUNCTION markos_runtime.authorize_publication_epoch(p_event uuid,p_fence bigint,p_epoch uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 IF NOT markos_runtime.valid_epoch(p_epoch) THEN RETURN false; END IF;
 RETURN markos_runtime.authorize_publication(p_event,p_fence);
END $$;

CREATE FUNCTION markos_runtime.retry_outbox_epoch(p_event uuid,p_fence bigint,p_delay integer,p_code text,p_epoch uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 IF NOT markos_runtime.valid_epoch(p_epoch) THEN RETURN false; END IF;
 RETURN markos_runtime.retry_outbox(p_event,p_fence,p_delay,p_code);
END $$;

CREATE FUNCTION markos_runtime.finish_review_epoch(p_review uuid,p_fence bigint,p_delay integer,p_epoch uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 IF NOT markos_runtime.valid_epoch(p_epoch) THEN RETURN false; END IF;
 RETURN markos_runtime.finish_review(p_review,p_fence,p_delay);
END $$;

CREATE FUNCTION markos_runtime.hold_for_review_epoch(p_event uuid,p_fence bigint,p_reason text,p_epoch uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 IF NOT markos_runtime.valid_epoch(p_epoch) THEN RETURN false; END IF;
 RETURN markos_runtime.hold_for_review(p_event,p_fence,p_reason);
END $$;

CREATE FUNCTION markos_runtime.finish_task_epoch(p_task uuid,p_fence bigint,p_executor uuid,p_failure text,p_delay integer,p_epoch uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 IF NOT markos_runtime.valid_epoch(p_epoch) THEN RETURN false; END IF;
 RETURN markos_runtime.finish_task(p_task,p_fence,p_executor,p_failure,p_delay);
END $$;

CREATE FUNCTION markos_runtime.renew_task_epoch(p_task uuid,p_fence bigint,p_executor uuid,p_seconds integer,p_epoch uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 IF NOT markos_runtime.valid_epoch(p_epoch) THEN RETURN false; END IF;
 RETURN markos_runtime.renew_task(p_task,p_fence,p_executor,p_seconds);
END $$;

CREATE TABLE markos_runtime.restore_history (
 epoch uuid PRIMARY KEY,restored_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON markos_runtime.restore_history FROM PUBLIC,markos_runtime_client;
CREATE FUNCTION markos_runtime.fence_restored_state() RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_runtime AS $$DECLARE fresh uuid:=gen_random_uuid();BEGIN
 -- Private explicit migration/restore-role operation. No runtime EXECUTE grant.
 PERFORM pg_advisory_xact_lock(712093482);
 UPDATE markos_runtime.runtime_epoch SET epoch=fresh;
 UPDATE markos_runtime.task_attempts SET outcome='abandoned',finished_at=clock_timestamp() WHERE outcome='running';
 UPDATE markos_runtime.tasks SET state='pending',fence=fence+1,lease_until=NULL,executor_id=NULL,slot_id=NULL,
 available_at=clock_timestamp() WHERE state='running';
 UPDATE markos_runtime.execution_slots SET task_id=NULL,fence=NULL,lease_until=NULL;
 UPDATE markos_runtime.outbox SET state='pending',fence=fence+1,lease_until=NULL,available_at=clock_timestamp() WHERE state='leased';
 UPDATE markos_runtime.events SET replay_until=NULL,replay_after=clock_timestamp();
 UPDATE markos_runtime.replay_permits SET expires_at=least(expires_at,clock_timestamp());
 UPDATE markos_runtime.review_outbox o SET state='pending',fence=fence+1,lease_until=NULL,available_at=clock_timestamp()
 WHERE EXISTS(SELECT 1 FROM markos_runtime.delivery_reviews r WHERE r.review_id=o.review_id AND r.resolved_at IS NULL);
 DELETE FROM markos_runtime.service_heartbeats;
 INSERT INTO markos_runtime.restore_history(epoch) VALUES(fresh);
 RETURN fresh;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA markos_runtime FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_runtime.current_epoch(),markos_runtime.heartbeat(text,uuid,uuid,text) TO markos_runtime_client;
REVOKE ALL ON FUNCTION markos_runtime.claim_outbox(integer,integer) FROM markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_runtime.claim_outbox_epoch(integer,integer) TO markos_runtime_client;
REVOKE ALL ON FUNCTION markos_runtime.claim_reviews(integer,integer) FROM markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_runtime.claim_reviews_epoch(integer,integer) TO markos_runtime_client;
REVOKE ALL ON FUNCTION markos_runtime.claim_task(uuid,integer) FROM markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_runtime.claim_task_epoch(uuid,integer) TO markos_runtime_client;
REVOKE ALL ON FUNCTION markos_runtime.record_publication(uuid,bigint) FROM markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_runtime.record_publication_epoch(uuid,bigint,uuid) TO markos_runtime_client;
REVOKE ALL ON FUNCTION markos_runtime.authorize_publication(uuid,bigint) FROM markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_runtime.authorize_publication_epoch(uuid,bigint,uuid) TO markos_runtime_client;
REVOKE ALL ON FUNCTION markos_runtime.retry_outbox(uuid,bigint,integer,text) FROM markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_runtime.retry_outbox_epoch(uuid,bigint,integer,text,uuid) TO markos_runtime_client;
REVOKE ALL ON FUNCTION markos_runtime.finish_review(uuid,bigint,integer) FROM markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_runtime.finish_review_epoch(uuid,bigint,integer,uuid) TO markos_runtime_client;
REVOKE ALL ON FUNCTION markos_runtime.hold_for_review(uuid,bigint,text) FROM markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_runtime.hold_for_review_epoch(uuid,bigint,text,uuid) TO markos_runtime_client;
REVOKE ALL ON FUNCTION markos_runtime.finish_task(uuid,bigint,uuid,text,integer) FROM markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_runtime.finish_task_epoch(uuid,bigint,uuid,text,integer,uuid) TO markos_runtime_client;
REVOKE ALL ON FUNCTION markos_runtime.renew_task(uuid,bigint,uuid,integer) FROM markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_runtime.renew_task_epoch(uuid,bigint,uuid,integer,uuid) TO markos_runtime_client;
