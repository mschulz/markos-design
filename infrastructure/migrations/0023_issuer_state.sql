-- M7-B1: the issuer keeps its signing key, one-use codes, sessions and refresh tokens in
-- PostgreSQL, so a restart, backup or restore covers them. Only the NOLOGIN group
-- markos_issuer_client calls these functions; bootstrap creates it before migrating.
-- The issuer seals the signing key before it arrives. Codes and refresh tokens are stored as
-- HMAC lookups only. Database time is the only clock: codes last 60 s, sessions 8 h, the
-- gateway's absolute session limit.
CREATE SCHEMA markos_issuer;
REVOKE ALL ON SCHEMA markos_issuer FROM PUBLIC;
GRANT USAGE ON SCHEMA markos_issuer TO markos_issuer_client;

CREATE TABLE markos_issuer.signing_key(
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 kid text NOT NULL CHECK(kid~'^[a-f0-9]{32}$'),
 key_cipher text NOT NULL CHECK(length(key_cipher) BETWEEN 1 AND 16384),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE markos_issuer.codes(
 code_hash text PRIMARY KEY CHECK(code_hash~'^[a-f0-9]{64}$'),
 subject text NOT NULL CHECK(length(subject) BETWEEN 1 AND 256),
 challenge text NOT NULL CHECK(challenge~'^[A-Za-z0-9_-]{43}$'),
 nonce text NOT NULL CHECK(nonce~'^[A-Za-z0-9_-]{43}$'),
 expires_at timestamptz NOT NULL
);
CREATE TABLE markos_issuer.sessions(
 session_id uuid PRIMARY KEY CHECK(session_id<>'00000000-0000-0000-0000-000000000000'),
 subject text NOT NULL CHECK(length(subject) BETWEEN 1 AND 256),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE markos_issuer.refresh_tokens(
 token_hash text PRIMARY KEY CHECK(token_hash~'^[a-f0-9]{64}$'),
 session_id uuid NOT NULL UNIQUE REFERENCES markos_issuer.sessions ON DELETE CASCADE,
 nonce text NOT NULL CHECK(nonce~'^[A-Za-z0-9_-]{43}$')
);
ALTER TABLE markos_issuer.signing_key ENABLE ROW LEVEL SECURITY;
ALTER TABLE markos_issuer.codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE markos_issuer.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE markos_issuer.refresh_tokens ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA markos_issuer FROM PUBLIC,markos_issuer_client;

CREATE FUNCTION markos_issuer.issuer_ready(p_expected jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
 SELECT markos_runtime.current_epoch() IS NOT NULL AND p_expected IS NOT NULL
  AND (SELECT jsonb_object_agg(version::text,sha256) FROM markos_runtime.schema_migrations)=p_expected;
$$;

-- The current key, or NULL before the first start.
CREATE FUNCTION markos_issuer.issuer_key() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,markos_issuer AS $$
 SELECT jsonb_build_object('kid',kid,'key_cipher',key_cipher) FROM markos_issuer.signing_key;
$$;

-- First start installs a key if none exists. Rotation replaces it; old tokens then fail.
CREATE FUNCTION markos_issuer.issuer_install_key(p_kid text,p_cipher text,p_replace boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_issuer AS $$
BEGIN
 IF p_replace THEN
  DELETE FROM markos_issuer.signing_key;
 END IF;
 INSERT INTO markos_issuer.signing_key(kid,key_cipher) VALUES(p_kid,p_cipher) ON CONFLICT DO NOTHING;
 RETURN markos_issuer.issuer_key();
END $$;

CREATE FUNCTION markos_issuer.issuer_authorize(p_code text,p_subject text,p_challenge text,p_nonce text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_issuer AS $$
BEGIN
 DELETE FROM markos_issuer.codes WHERE expires_at<=now();
 IF (SELECT count(*) FROM markos_issuer.codes)>=100 THEN
  RAISE EXCEPTION 'issuer codes full' USING ERRCODE='54000';
 END IF;
 INSERT INTO markos_issuer.codes VALUES(p_code,p_subject,p_challenge,p_nonce,now()+interval '60 seconds');
END $$;

-- Consumes the code even when the PKCE check fails. Returns NULL when denied.
CREATE FUNCTION markos_issuer.issuer_exchange(p_code text,p_challenge text,p_refresh text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_issuer AS $$
DECLARE c markos_issuer.codes;sid uuid:=gen_random_uuid();
BEGIN
 DELETE FROM markos_issuer.codes WHERE code_hash=p_code RETURNING * INTO c;
 IF c IS NULL OR c.expires_at<=now() OR c.challenge<>p_challenge THEN
  RETURN NULL;
 END IF;
 DELETE FROM markos_issuer.sessions WHERE created_at<=now()-interval '8 hours';
 IF (SELECT count(*) FROM markos_issuer.sessions)>=100 THEN
  RAISE EXCEPTION 'issuer sessions full' USING ERRCODE='54000';
 END IF;
 INSERT INTO markos_issuer.sessions(session_id,subject) VALUES(sid,c.subject);
 INSERT INTO markos_issuer.refresh_tokens VALUES(p_refresh,sid,c.nonce);
 RETURN jsonb_build_object('subject',c.subject,'session_id',sid,'nonce',c.nonce,
  'now',floor(extract(epoch FROM now()))::bigint);
END $$;

-- Rotates the refresh token. Returns NULL when the token or its session is gone.
CREATE FUNCTION markos_issuer.issuer_refresh(p_old text,p_new text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_issuer AS $$
DECLARE r markos_issuer.refresh_tokens;s markos_issuer.sessions;
BEGIN
 DELETE FROM markos_issuer.refresh_tokens WHERE token_hash=p_old RETURNING * INTO r;
 IF r IS NULL THEN
  RETURN NULL;
 END IF;
 SELECT * INTO s FROM markos_issuer.sessions
  WHERE session_id=r.session_id AND created_at>now()-interval '8 hours';
 IF s IS NULL THEN
  RETURN NULL;
 END IF;
 INSERT INTO markos_issuer.refresh_tokens VALUES(p_new,s.session_id,r.nonce);
 RETURN jsonb_build_object('subject',s.subject,'session_id',s.session_id,'nonce',r.nonce,
  'now',floor(extract(epoch FROM now()))::bigint);
END $$;

CREATE FUNCTION markos_issuer.issuer_session_active(p_subject text,p_session uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,markos_issuer AS $$
 SELECT EXISTS(SELECT 1 FROM markos_issuer.sessions
  WHERE session_id=p_session AND subject=p_subject AND created_at>now()-interval '8 hours');
$$;

CREATE FUNCTION markos_issuer.issuer_revoke(p_session uuid) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,markos_issuer AS $$
 WITH gone AS (DELETE FROM markos_issuer.sessions WHERE session_id=p_session RETURNING 1)
 SELECT EXISTS(SELECT 1 FROM gone);
$$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA markos_issuer FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_issuer.issuer_ready(jsonb),markos_issuer.issuer_key(),
 markos_issuer.issuer_install_key(text,text,boolean),markos_issuer.issuer_authorize(text,text,text,text),
 markos_issuer.issuer_exchange(text,text,text),markos_issuer.issuer_refresh(text,text),
 markos_issuer.issuer_session_active(text,uuid),markos_issuer.issuer_revoke(uuid) TO markos_issuer_client;
