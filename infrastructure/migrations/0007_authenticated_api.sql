-- M1-C: synthetic identity API state. Existing migrations are immutable.
CREATE TABLE markos_control.api_flows(
 flow_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),state_hash text UNIQUE NOT NULL CHECK(state_hash~'^[0-9a-f]{64}$'),
 binding_hash text NOT NULL CHECK(binding_hash~'^[0-9a-f]{64}$'),verifier_cipher text,
 challenge text NOT NULL CHECK(challenge~'^[A-Za-z0-9_-]{43}$'),nonce text NOT NULL CHECK(nonce~'^[A-Za-z0-9_-]{43}$'),
 kind text NOT NULL CHECK(kind IN ('browser','cli')),epoch uuid NOT NULL,
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '5 minutes',used boolean NOT NULL DEFAULT false,
 CHECK((kind='browser' AND verifier_cipher IS NOT NULL AND length(verifier_cipher) BETWEEN 1 AND 2048) OR (kind='cli' AND verifier_cipher IS NULL))
);
CREATE TABLE markos_control.api_credentials(
 session_id uuid PRIMARY KEY REFERENCES markos_control.sessions,
 access_cipher text NOT NULL CHECK(length(access_cipher) BETWEEN 1 AND 16384),
 refresh_cipher text NOT NULL CHECK(length(refresh_cipher) BETWEEN 1 AND 16384),
 nonce text NOT NULL CHECK(nonce~'^[A-Za-z0-9_-]{43}$')
);
CREATE TABLE markos_control.api_key_cache(
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),epoch uuid NOT NULL,payload jsonb,
 expires_at timestamptz NOT NULL DEFAULT '-infinity',next_refresh_at timestamptz NOT NULL DEFAULT '-infinity',attempt uuid
);
CREATE TABLE markos_control.api_auth_slots(
 slot integer PRIMARY KEY CHECK(slot BETWEEN 1 AND 4),attempt uuid,lease_until timestamptz,epoch uuid
);
INSERT INTO markos_control.api_auth_slots(slot) SELECT generate_series(1,4);
CREATE TABLE markos_control.api_write_buckets(
 principal_id uuid PRIMARY KEY REFERENCES markos_control.principals,epoch uuid NOT NULL,tokens numeric NOT NULL,last_seen timestamptz NOT NULL
);
ALTER TABLE markos_control.api_flows ENABLE ROW LEVEL SECURITY;
ALTER TABLE markos_control.api_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE markos_control.api_key_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE markos_control.api_auth_slots ENABLE ROW LEVEL SECURITY;
ALTER TABLE markos_control.api_write_buckets ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_control.api_flows,markos_control.api_credentials,markos_control.api_key_cache,markos_control.api_auth_slots,markos_control.api_write_buckets FROM PUBLIC,markos_control_gateway;

CREATE FUNCTION markos_control.api_begin_flow(p_state text,p_binding text,p_cipher text,p_challenge text,p_nonce text,p_kind text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE fresh uuid;BEGIN
 PERFORM markos_control.gateway();PERFORM pg_advisory_xact_lock(712093482);
 IF markos_runtime.current_epoch() IS NULL THEN RAISE EXCEPTION 'authority unavailable' USING ERRCODE='42501';END IF;
 DELETE FROM markos_control.api_flows WHERE expires_at<=clock_timestamp() OR epoch IS DISTINCT FROM markos_runtime.current_epoch();
 DELETE FROM markos_control.api_credentials c USING markos_control.sessions s WHERE c.session_id=s.session_id AND
 (coalesce(s.revoked_at,s.expires_at)+interval '24 hours'<=clock_timestamp() OR s.epoch IS DISTINCT FROM markos_runtime.current_epoch());
 IF (SELECT count(*) FROM markos_control.api_flows)>=100 THEN RAISE EXCEPTION 'login admission exhausted' USING ERRCODE='54000';END IF;
 INSERT INTO markos_control.api_flows(state_hash,binding_hash,verifier_cipher,challenge,nonce,kind,epoch)
 VALUES(p_state,p_binding,p_cipher,p_challenge,p_nonce,p_kind,markos_runtime.current_epoch()) RETURNING flow_id INTO fresh;
 RETURN fresh;
END $$;
CREATE FUNCTION markos_control.api_consume_flow(p_state text,p_binding text,p_kind text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE f markos_control.api_flows;BEGIN
 PERFORM markos_control.gateway();PERFORM pg_advisory_xact_lock(712093482);
 SELECT * INTO f FROM markos_control.api_flows WHERE state_hash=p_state AND binding_hash=p_binding AND kind=p_kind FOR UPDATE;
 IF NOT FOUND OR f.used OR f.expires_at<=clock_timestamp() OR f.epoch IS DISTINCT FROM markos_runtime.current_epoch() THEN
 RAISE EXCEPTION 'login unavailable' USING ERRCODE='42501';END IF;
 UPDATE markos_control.api_flows SET used=true,verifier_cipher=NULL WHERE flow_id=f.flow_id;
 -- Browser cipher is returned only once. Used rows retain minimal replay evidence until expiry.
 RETURN jsonb_build_object('flow_id',f.flow_id,'verifier_cipher',f.verifier_cipher,'challenge',f.challenge,'nonce',f.nonce);
END $$;
-- Consumed browser rows no longer need a verifier ciphertext.
ALTER TABLE markos_control.api_flows DROP CONSTRAINT api_flows_check;
ALTER TABLE markos_control.api_flows ADD CHECK(used OR (kind='browser' AND verifier_cipher IS NOT NULL AND length(verifier_cipher) BETWEEN 1 AND 2048) OR (kind='cli' AND verifier_cipher IS NULL));

CREATE FUNCTION markos_control.api_complete_login(p_subject text,p_provider uuid,p_hash text,p_kind text,p_expiry timestamptz,p_access text,p_refresh text,p_nonce text,p_old_id uuid,p_old_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE opened jsonb;sid uuid;BEGIN
 PERFORM markos_control.gateway();
 IF p_old_id IS NOT NULL AND markos_control.revoke_session(p_old_id,p_old_hash) THEN
  DELETE FROM markos_control.api_credentials WHERE session_id=p_old_id;
 END IF;
 opened:=markos_control.open_session('synthetic-issuer',p_subject,p_provider,p_hash,p_kind,p_expiry);
 sid:=(opened->>'session_id')::uuid;
 INSERT INTO markos_control.api_credentials VALUES(sid,p_access,p_refresh,p_nonce);
 RETURN opened;
END $$;
CREATE FUNCTION markos_control.api_credentials_for(p_session uuid,p_hash text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE s markos_control.sessions;p markos_control.principals;c markos_control.api_credentials;now_at timestamptz;BEGIN
 PERFORM markos_control.gateway();PERFORM pg_advisory_xact_lock(712093482);
 SELECT * INTO s FROM markos_control.sessions WHERE session_id=p_session AND lookup_hash=p_hash FOR SHARE;
 now_at:=clock_timestamp();
 IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.epoch IS DISTINCT FROM markos_runtime.current_epoch()
 OR s.expires_at<=now_at OR s.provider_expires_at<=now_at OR (s.kind='browser' AND s.last_seen_at+interval '30 minutes'<=now_at) THEN
 RAISE EXCEPTION 'session unavailable' USING ERRCODE='42501';END IF;
 SELECT * INTO p FROM markos_control.principals WHERE principal_id=s.principal_id AND NOT disabled FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'session unavailable' USING ERRCODE='42501';END IF;
 SELECT * INTO c FROM markos_control.api_credentials WHERE session_id=p_session;
 IF NOT FOUND THEN RAISE EXCEPTION 'session unavailable' USING ERRCODE='42501';END IF;
 RETURN jsonb_build_object('principal_id',p.principal_id,'subject',p.subject,'provider_session_id',s.provider_session_id,'kind',s.kind,'access_cipher',c.access_cipher,'refresh_cipher',c.refresh_cipher,'nonce',c.nonce);
END $$;
CREATE FUNCTION markos_control.api_refresh_credentials(p_session uuid,p_hash text,p_subject text,p_provider uuid,p_expiry timestamptz,p_access text,p_refresh text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE binding jsonb;result jsonb;BEGIN
 binding:=markos_control.api_credentials_for(p_session,p_hash);
 IF binding->>'subject' IS DISTINCT FROM p_subject OR binding->>'provider_session_id' IS DISTINCT FROM p_provider::text THEN
 RAISE EXCEPTION 'refresh binding mismatch' USING ERRCODE='42501';END IF;
 result:=markos_control.refresh_session(p_session,p_hash,p_expiry);
 UPDATE markos_control.api_credentials SET access_cipher=p_access,refresh_cipher=p_refresh WHERE session_id=p_session;
 RETURN result;
END $$;
CREATE FUNCTION markos_control.api_logout(p_session uuid,p_hash text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE result boolean;BEGIN
 result:=markos_control.revoke_session(p_session,p_hash);
 IF result THEN DELETE FROM markos_control.api_credentials WHERE session_id=p_session;END IF;
 RETURN result;
END $$;
CREATE FUNCTION markos_control.api_me(p_session uuid,p_hash text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE actor uuid;members jsonb;BEGIN
 actor:=markos_control.session_context(p_session,p_hash);
 SELECT coalesce(jsonb_agg(jsonb_build_object('owner_id',owner_id,'domain',domain,'revision',revision,'roles',roles) ORDER BY owner_id,domain),'[]'::jsonb) INTO members
 FROM markos_control.memberships WHERE principal_id=actor;
 IF jsonb_array_length(members)>64 OR octet_length(members::text)>262144 THEN RAISE EXCEPTION 'membership response exhausted' USING ERRCODE='54000';END IF;
 RETURN jsonb_build_object('principal_id',actor,'memberships',members);
END $$;
CREATE FUNCTION markos_control.api_status(p_session uuid,p_hash text,p_owner uuid,p_domain text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE rows jsonb;BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 SELECT coalesce(jsonb_agg(jsonb_build_object('service_id',service_id,'component',component,'seen_at',seen_at,'state',
 CASE WHEN epoch IS DISTINCT FROM markos_runtime.current_epoch() OR seen_at<clock_timestamp()-interval '45 seconds' THEN 'stale'
 WHEN status='ready' THEN 'fresh' ELSE 'degraded' END)),'[]'::jsonb) INTO rows
 FROM (SELECT DISTINCT ON(service_id,component) * FROM markos_runtime.service_heartbeats WHERE owner_id=p_owner AND domain=p_domain ORDER BY service_id,component,seen_at DESC LIMIT 100) recent;
 RETURN jsonb_build_object('state',CASE WHEN jsonb_array_length(rows)=0 THEN 'unknown' ELSE 'observed' END,'services',rows);
END $$;
CREATE FUNCTION markos_control.api_admit_write(p_session uuid,p_hash text) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE actor uuid;b markos_control.api_write_buckets;now_at timestamptz;available numeric;epoch_now uuid;BEGIN
 actor:=markos_control.session_context(p_session,p_hash);now_at:=clock_timestamp();epoch_now:=markos_runtime.current_epoch();
 SELECT * INTO b FROM markos_control.api_write_buckets WHERE principal_id=actor FOR UPDATE;
 available:=CASE WHEN NOT FOUND OR b.epoch IS DISTINCT FROM epoch_now THEN 5 ELSE least(5,b.tokens+greatest(0,extract(epoch FROM(now_at-b.last_seen)))*0.5) END;
 IF available<1 THEN RETURN false;END IF;
 INSERT INTO markos_control.api_write_buckets VALUES(actor,epoch_now,available-1,now_at)
 ON CONFLICT(principal_id) DO UPDATE SET epoch=EXCLUDED.epoch,tokens=EXCLUDED.tokens,last_seen=EXCLUDED.last_seen;
 RETURN true;
END $$;
CREATE FUNCTION markos_control.api_auth_claim() RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE chosen integer;fresh uuid:=gen_random_uuid();BEGIN
 PERFORM markos_control.gateway();PERFORM pg_advisory_xact_lock(712093482);
 IF markos_runtime.current_epoch() IS NULL THEN RAISE EXCEPTION 'authority unavailable' USING ERRCODE='42501';END IF;
 SELECT slot INTO chosen FROM markos_control.api_auth_slots WHERE lease_until IS NULL OR lease_until<=clock_timestamp() OR epoch IS DISTINCT FROM markos_runtime.current_epoch() ORDER BY slot FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL;END IF;
 UPDATE markos_control.api_auth_slots SET attempt=fresh,lease_until=clock_timestamp()+interval '5 seconds',epoch=markos_runtime.current_epoch() WHERE slot=chosen;
 RETURN fresh;
END $$;
CREATE FUNCTION markos_control.api_auth_release(p_attempt uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$BEGIN
 PERFORM markos_control.gateway();UPDATE markos_control.api_auth_slots SET attempt=NULL,lease_until=NULL WHERE attempt=p_attempt;
END $$;
CREATE FUNCTION markos_control.api_keys() RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE k markos_control.api_key_cache;BEGIN
 PERFORM markos_control.gateway();SELECT * INTO k FROM markos_control.api_key_cache;
 IF NOT FOUND OR k.epoch IS DISTINCT FROM markos_runtime.current_epoch() THEN RETURN NULL;END IF;
 RETURN jsonb_build_object('payload',k.payload,'fresh',k.expires_at>clock_timestamp());
END $$;
CREATE FUNCTION markos_control.api_keys_claim() RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$DECLARE k markos_control.api_key_cache;fresh uuid:=gen_random_uuid();epoch_now uuid;BEGIN
 PERFORM markos_control.gateway();PERFORM pg_advisory_xact_lock(712093482);epoch_now:=markos_runtime.current_epoch();
 IF epoch_now IS NULL THEN RAISE EXCEPTION 'authority unavailable' USING ERRCODE='42501';END IF;
 SELECT * INTO k FROM markos_control.api_key_cache FOR UPDATE;
 IF FOUND AND k.epoch=epoch_now AND k.next_refresh_at>clock_timestamp() THEN RETURN NULL;END IF;
 INSERT INTO markos_control.api_key_cache(singleton,epoch,attempt,next_refresh_at) VALUES(true,epoch_now,fresh,clock_timestamp()+interval '60 seconds')
 ON CONFLICT(singleton) DO UPDATE SET
 payload=CASE WHEN markos_control.api_key_cache.epoch=EXCLUDED.epoch THEN markos_control.api_key_cache.payload ELSE NULL END,
 expires_at=CASE WHEN markos_control.api_key_cache.epoch=EXCLUDED.epoch THEN markos_control.api_key_cache.expires_at ELSE '-infinity'::timestamptz END,
 epoch=EXCLUDED.epoch,attempt=EXCLUDED.attempt,next_refresh_at=EXCLUDED.next_refresh_at;
 RETURN fresh;
END $$;
CREATE FUNCTION markos_control.api_keys_save(p_attempt uuid,p_payload jsonb) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$BEGIN
 PERFORM markos_control.gateway();
 IF p_payload IS NULL OR octet_length(p_payload::text)>65536 THEN RAISE EXCEPTION 'invalid public keys' USING ERRCODE='22023';END IF;
 UPDATE markos_control.api_key_cache SET payload=p_payload,expires_at=clock_timestamp()+interval '60 seconds',attempt=NULL
 WHERE attempt=p_attempt AND epoch=markos_runtime.current_epoch();RETURN FOUND;
END $$;
CREATE FUNCTION markos_control.api_ready(p_expected jsonb) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,markos_control AS $$BEGIN PERFORM markos_control.gateway();RETURN markos_runtime.current_epoch() IS NOT NULL
 AND p_expected IS NOT NULL AND (SELECT jsonb_object_agg(version::text,sha256) FROM markos_runtime.schema_migrations) = p_expected;END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA markos_control FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.api_begin_flow(text,text,text,text,text,text),markos_control.api_consume_flow(text,text,text),
 markos_control.api_complete_login(text,uuid,text,text,timestamptz,text,text,text,uuid,text),
 markos_control.api_credentials_for(uuid,text),markos_control.api_refresh_credentials(uuid,text,text,uuid,timestamptz,text,text),markos_control.api_logout(uuid,text),
 markos_control.api_me(uuid,text),markos_control.api_status(uuid,text,uuid,text),markos_control.api_admit_write(uuid,text),markos_control.api_auth_claim(),markos_control.api_auth_release(uuid),
 markos_control.api_keys(),markos_control.api_keys_claim(),markos_control.api_keys_save(uuid,jsonb),markos_control.api_ready(jsonb) TO markos_control_gateway;
