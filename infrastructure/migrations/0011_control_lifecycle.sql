-- M1-G: process readiness and a shared short-request budget only.
CREATE TABLE markos_control.http_requests(request_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),expires_at timestamptz NOT NULL);
ALTER TABLE markos_control.http_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_control.http_requests FROM PUBLIC,markos_control_gateway,markos_runtime_client;
CREATE FUNCTION markos_control.process_probe(p_expected jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_control AS $$BEGIN
 PERFORM markos_control.gateway();
 IF NOT markos_control.api_ready(p_expected) THEN RAISE EXCEPTION 'reviewed schema required';END IF;
 RETURN jsonb_build_object('epoch',markos_runtime.current_epoch());
END $$;
CREATE FUNCTION markos_control.runtime_process_probe(p_expected jsonb) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_control AS $$BEGIN
 PERFORM markos_runtime.runtime_identity();
 IF p_expected IS NULL OR (SELECT jsonb_object_agg(version::text,sha256) FROM markos_runtime.schema_migrations) IS DISTINCT FROM p_expected THEN RAISE EXCEPTION 'reviewed schema required';END IF;
 RETURN markos_runtime.current_epoch();
END $$;
CREATE FUNCTION markos_control.claim_http_request() RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_control AS $$DECLARE fresh uuid;BEGIN
 PERFORM markos_control.gateway();PERFORM pg_advisory_xact_lock(712093482);
 DELETE FROM markos_control.http_requests WHERE expires_at<=clock_timestamp();
 IF (SELECT count(*) FROM markos_control.http_requests)>=16 THEN RETURN NULL;END IF;
 INSERT INTO markos_control.http_requests(expires_at) VALUES(clock_timestamp()+interval '15 seconds') RETURNING request_id INTO fresh;RETURN fresh;
END $$;
CREATE FUNCTION markos_control.release_http_request(p_request uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_control AS $$BEGIN PERFORM markos_control.gateway();DELETE FROM markos_control.http_requests WHERE request_id=p_request;END $$;
REVOKE ALL ON FUNCTION markos_control.process_probe(jsonb),markos_control.runtime_process_probe(jsonb),markos_control.claim_http_request(),markos_control.release_http_request(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.process_probe(jsonb),markos_control.claim_http_request(),markos_control.release_http_request(uuid) TO markos_control_gateway;
GRANT USAGE ON SCHEMA markos_control TO markos_runtime_client;
GRANT EXECUTE ON FUNCTION markos_control.runtime_process_probe(jsonb) TO markos_runtime_client;
