-- M1-E: scoped, shared activity-stream admission; no implicit provisioning or migrations.
CREATE TABLE markos_control.activity_streams(
 stream_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),session_id uuid NOT NULL REFERENCES markos_control.sessions,
 principal_id uuid NOT NULL REFERENCES markos_control.principals,owner_id uuid NOT NULL,domain text NOT NULL,
 epoch uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),lease_until timestamptz NOT NULL
);
ALTER TABLE markos_control.activity_streams ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_control.activity_streams FROM PUBLIC,markos_control_gateway,markos_runtime_client;
CREATE FUNCTION markos_control.activity_head(p_session uuid,p_hash text,p_owner uuid,p_domain text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 RETURN jsonb_build_object('epoch',markos_runtime.current_epoch(),'head',coalesce((SELECT max(sequence) FROM markos_control.activity WHERE owner_id=p_owner AND domain=p_domain),0));
END $$;
CREATE FUNCTION markos_control.open_activity_stream(p_session uuid,p_hash text,p_owner uuid,p_domain text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE actor uuid;fresh uuid;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 DELETE FROM markos_control.activity_streams WHERE lease_until<=clock_timestamp() OR epoch IS DISTINCT FROM markos_runtime.current_epoch() OR created_at+interval '10 minutes'<=clock_timestamp();
 IF (SELECT count(*) FROM markos_control.activity_streams)>=20
 OR (SELECT count(*) FROM markos_control.activity_streams WHERE principal_id=actor)>=4
 OR EXISTS(SELECT 1 FROM markos_control.activity_streams WHERE session_id=p_session) THEN
 RAISE EXCEPTION 'stream admission exhausted' USING ERRCODE='54000';END IF;
 INSERT INTO markos_control.activity_streams(session_id,principal_id,owner_id,domain,epoch,lease_until)
 VALUES(p_session,actor,p_owner,p_domain,markos_runtime.current_epoch(),clock_timestamp()+interval '15 seconds') RETURNING stream_id INTO fresh;
 RETURN fresh;
END $$;
CREATE FUNCTION markos_control.renew_activity_stream(p_session uuid,p_hash text,p_stream uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE r markos_control.activity_streams;BEGIN
 PERFORM markos_control.session_context(p_session,p_hash);
 SELECT * INTO r FROM markos_control.activity_streams WHERE stream_id=p_stream AND session_id=p_session FOR UPDATE;
 IF NOT FOUND OR r.epoch IS DISTINCT FROM markos_runtime.current_epoch() OR r.lease_until<=clock_timestamp() OR r.created_at+interval '10 minutes'<=clock_timestamp() THEN RETURN false;END IF;
 PERFORM markos_control.authorize(p_session,p_hash,r.owner_id,r.domain,'inspect');
 UPDATE markos_control.activity_streams SET lease_until=least(clock_timestamp()+interval '15 seconds',r.created_at+interval '10 minutes') WHERE stream_id=p_stream;
 RETURN true;
END $$;
CREATE FUNCTION markos_control.close_activity_stream(p_session uuid,p_hash text,p_stream uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$BEGIN
 PERFORM markos_control.gateway();
 DELETE FROM markos_control.activity_streams r USING markos_control.sessions s WHERE r.stream_id=p_stream AND r.session_id=p_session AND s.session_id=r.session_id AND s.lookup_hash=p_hash;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA markos_control FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.activity_head(uuid,text,uuid,text),markos_control.open_activity_stream(uuid,text,uuid,text),markos_control.renew_activity_stream(uuid,text,uuid),markos_control.close_activity_stream(uuid,text,uuid) TO markos_control_gateway;

-- Capability hints are derived from current authority; cancellation still reauthorizes separately.
CREATE OR REPLACE FUNCTION markos_control.chat_status(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_conversation uuid,p_message uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE r markos_control.chat_runs;actor uuid;allowed boolean;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 SELECT * INTO r FROM markos_control.chat_runs WHERE owner_id=p_owner AND domain=p_domain AND conversation_id=p_conversation AND message_id=p_message;
 IF NOT FOUND THEN RAISE EXCEPTION 'object unavailable' USING ERRCODE='P0002';END IF;
 allowed:=r.actor_id=actor AND r.state IN ('queued','working') AND EXISTS(SELECT 1 FROM markos_control.memberships WHERE principal_id=actor AND owner_id=p_owner AND domain=p_domain AND 'operator'=ANY(roles));
 RETURN jsonb_build_object('message_id',r.message_id,'event_id',r.event_id,'state',r.state,'reason',r.reason,'reply_id',r.reply_id,'completion_id',r.completion_id,'can_cancel',allowed);
END $$;
