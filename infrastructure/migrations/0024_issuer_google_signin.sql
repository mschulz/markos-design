-- M7-B2: a Google sign-in waiting for Google's callback. The issuer stores the Google state
-- and nonce as HMAC lookups and seals the PKCE verifier before it arrives. Each row binds
-- the MarkOS PKCE challenge and nonce the gateway sent, so the MarkOS code the issuer mints
-- after Google answers works only for that gateway flow. A row is used once and lasts 10
-- minutes of database time.
CREATE TABLE markos_issuer.google_logins(
 state_hash text PRIMARY KEY CHECK(state_hash~'^[a-f0-9]{64}$'),
 nonce_hash text NOT NULL CHECK(nonce_hash~'^[a-f0-9]{64}$'),
 verifier_cipher text NOT NULL CHECK(length(verifier_cipher) BETWEEN 1 AND 1024),
 challenge text NOT NULL CHECK(challenge~'^[A-Za-z0-9_-]{43}$'),
 nonce text NOT NULL CHECK(nonce~'^[A-Za-z0-9_-]{43}$'),
 expires_at timestamptz NOT NULL
);
ALTER TABLE markos_issuer.google_logins ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_issuer.google_logins FROM PUBLIC,markos_issuer_client;

CREATE FUNCTION markos_issuer.issuer_google_begin(p_state text,p_nonce_hash text,p_verifier text,
 p_challenge text,p_nonce text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_issuer AS $$
BEGIN
 DELETE FROM markos_issuer.google_logins WHERE expires_at<=now();
 IF (SELECT count(*) FROM markos_issuer.google_logins)>=100 THEN
  RAISE EXCEPTION 'issuer google sign-ins full' USING ERRCODE='54000';
 END IF;
 INSERT INTO markos_issuer.google_logins
  VALUES(p_state,p_nonce_hash,p_verifier,p_challenge,p_nonce,now()+interval '10 minutes');
END $$;

-- Consumes the row. Returns NULL when it is unknown or expired.
CREATE FUNCTION markos_issuer.issuer_google_take(p_state text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_issuer AS $$
DECLARE g markos_issuer.google_logins;
BEGIN
 DELETE FROM markos_issuer.google_logins WHERE state_hash=p_state RETURNING * INTO g;
 IF g IS NULL OR g.expires_at<=now() THEN
  RETURN NULL;
 END IF;
 RETURN jsonb_build_object('nonce_hash',g.nonce_hash,'verifier_cipher',g.verifier_cipher,
  'challenge',g.challenge,'nonce',g.nonce);
END $$;

REVOKE ALL ON FUNCTION markos_issuer.issuer_google_begin(text,text,text,text,text),
 markos_issuer.issuer_google_take(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_issuer.issuer_google_begin(text,text,text,text,text),
 markos_issuer.issuer_google_take(text) TO markos_issuer_client;
