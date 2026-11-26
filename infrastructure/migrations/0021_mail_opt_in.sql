-- M2-C1: owner opt-in. Only an administrator connects or disconnects the mailbox and sets its
-- label allowlist. The gateway stores the Gmail refresh token sealed with the mail worker's
-- public key, so only the worker can open it. Disconnect stops new work at once; the worker then
-- stops the watch, revokes the token at Google and deletes it. After 1 day it is deleted anyway.
DROP FUNCTION markos_runtime.open_mailbox(text,text[]);
-- A mailbox opened by the synthetic M2-A path has no token, so it cannot stay connected.
UPDATE markos_runtime.mailboxes SET connected=false,lease_until=NULL WHERE connected;

ALTER TABLE markos_runtime.mailboxes ADD COLUMN disconnect_deadline timestamptz,
 ADD CONSTRAINT mailbox_disconnect_state CHECK(NOT (connected AND disconnect_deadline IS NOT NULL));
CREATE TABLE markos_runtime.mail_tokens(
 owner_id uuid NOT NULL,domain text NOT NULL,
 sealed bytea NOT NULL CHECK(octet_length(sealed) BETWEEN 62 AND 4096 AND get_byte(sealed,0)=1),
 key_id text NOT NULL CHECK(key_id~'^[a-f0-9]{32}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain),
 FOREIGN KEY(owner_id,domain) REFERENCES markos_runtime.mailboxes
);
ALTER TABLE markos_runtime.mail_tokens ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_runtime.mail_tokens FROM PUBLIC,markos_runtime_client,markos_control_gateway;

-- Every owner change, with the labels it set. The audit table records who and when.
CREATE TABLE markos_control.mail_history(
 owner_id uuid NOT NULL,domain text NOT NULL,
 change_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 action text NOT NULL CHECK(action IN ('connect','labels','disconnect')),
 labels text[] CHECK((action='disconnect')=(labels IS NULL)),
 actor_id uuid NOT NULL REFERENCES markos_control.principals,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE markos_control.mail_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_control.mail_history FROM PUBLIC,markos_control_gateway,markos_runtime_client;

CREATE FUNCTION markos_control.mail_change(p_actor uuid,p_owner uuid,p_domain text,p_action text,p_labels text[])
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$
 INSERT INTO markos_control.mail_history(owner_id,domain,action,labels,actor_id) VALUES(p_owner,p_domain,p_action,p_labels,p_actor);
 INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id) VALUES(p_actor,p_owner,p_domain,'mail.'||p_action,p_owner);
$$;
CREATE FUNCTION markos_control.mail_view(p_owner uuid,p_domain text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
 SELECT coalesce((SELECT jsonb_build_object(
  'state',CASE WHEN connected THEN 'connected' WHEN disconnect_deadline IS NOT NULL THEN 'disconnecting' ELSE 'disconnected' END,
  'labels',CASE WHEN connected THEN to_jsonb(labels) END,'watch_expires_at',CASE WHEN connected THEN watch_expires_at END)
  FROM markos_runtime.mailboxes WHERE owner_id=p_owner AND domain=p_domain),
 jsonb_build_object('state','none','labels',NULL,'watch_expires_at',NULL))
$$;

-- One address per owner and domain. Reconnecting keeps the cursor; a gap resyncs as in M2-A.
CREATE FUNCTION markos_control.connect_mailbox(p_session uuid,p_hash text,p_owner uuid,p_domain text,
 p_address_hash text,p_labels text[],p_sealed bytea,p_key_id text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE actor uuid;b markos_runtime.mailboxes;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'configure');
 INSERT INTO markos_runtime.mailboxes(owner_id,domain,address_hash,labels) VALUES(p_owner,p_domain,p_address_hash,p_labels)
 ON CONFLICT DO NOTHING;
 SELECT * INTO STRICT b FROM markos_runtime.mailboxes WHERE owner_id=p_owner AND domain=p_domain FOR UPDATE;
 IF b.address_hash<>p_address_hash THEN RAISE EXCEPTION 'another mailbox is connected' USING ERRCODE='23505';END IF;
 IF b.disconnect_deadline IS NOT NULL THEN RAISE EXCEPTION 'mailbox disconnect in progress' USING ERRCODE='40001';END IF;
 UPDATE markos_runtime.mailboxes SET labels=p_labels,connected=true,poll_due_at=clock_timestamp(),renew_due_at=clock_timestamp(),
  watch_expires_at=NULL,fence=fence+1,lease_until=NULL,updated_at=clock_timestamp()
 WHERE owner_id=p_owner AND domain=p_domain;
 INSERT INTO markos_runtime.mail_tokens(owner_id,domain,sealed,key_id) VALUES(p_owner,p_domain,p_sealed,p_key_id)
 ON CONFLICT(owner_id,domain) DO UPDATE SET sealed=EXCLUDED.sealed,key_id=EXCLUDED.key_id,created_at=clock_timestamp();
 PERFORM markos_control.mail_change(actor,p_owner,p_domain,'connect',p_labels);
 RETURN markos_control.mail_view(p_owner,p_domain);
END $$;
-- A new allowlist needs a new watch, so the renewal becomes due now.
CREATE FUNCTION markos_control.set_mail_labels(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_labels text[]) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE actor uuid;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'configure');
 UPDATE markos_runtime.mailboxes SET labels=p_labels,renew_due_at=clock_timestamp(),updated_at=clock_timestamp()
 WHERE owner_id=p_owner AND domain=p_domain AND connected;
 IF NOT FOUND THEN RAISE EXCEPTION 'no connected mailbox' USING ERRCODE='P0002';END IF;
 PERFORM markos_control.mail_change(actor,p_owner,p_domain,'labels',p_labels);
 RETURN markos_control.mail_view(p_owner,p_domain);
END $$;
-- Stops new work at once. A new fence makes any running worker's finish a no-op.
CREATE FUNCTION markos_control.disconnect_mailbox(p_session uuid,p_hash text,p_owner uuid,p_domain text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE actor uuid;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'configure');
 UPDATE markos_runtime.mailboxes SET connected=false,disconnect_deadline=clock_timestamp()+interval '1 day',
  fence=fence+1,lease_until=NULL,watch_expires_at=NULL,updated_at=clock_timestamp()
 WHERE owner_id=p_owner AND domain=p_domain AND connected;
 IF FOUND THEN PERFORM markos_control.mail_change(actor,p_owner,p_domain,'disconnect',NULL);END IF;
 RETURN markos_control.mail_view(p_owner,p_domain);
END $$;
CREATE FUNCTION markos_control.read_mailbox(p_session uuid,p_hash text,p_owner uuid,p_domain text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 RETURN markos_control.mail_view(p_owner,p_domain);
END $$;

-- The worker's claim now carries the sealed token, and a pending disconnect is work too. A
-- disconnect past its deadline is finished here without Google: the token is deleted.
CREATE OR REPLACE FUNCTION markos_runtime.claim_mail_work(p_seconds integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;b markos_runtime.mailboxes;t markos_runtime.mail_tokens;
 tick timestamptz:=clock_timestamp();renew boolean;poll boolean;held boolean;BEGIN
 p:=markos_runtime.mail_identity();
 IF p_seconds IS NULL OR p_seconds NOT BETWEEN 10 AND 300 THEN RAISE EXCEPTION 'invalid mail lease' USING ERRCODE='22023';END IF;
 SELECT * INTO b FROM markos_runtime.mailboxes WHERE owner_id=p.owner_id AND domain=p.domain FOR UPDATE;
 IF NOT FOUND OR b.lease_until>tick OR NOT (b.connected OR b.disconnect_deadline IS NOT NULL) THEN RETURN NULL;END IF;
 SELECT * INTO t FROM markos_runtime.mail_tokens WHERE owner_id=p.owner_id AND domain=p.domain;
 held:=FOUND;
 IF NOT b.connected AND (b.disconnect_deadline<=tick OR NOT held) THEN
  DELETE FROM markos_runtime.mail_tokens WHERE owner_id=p.owner_id AND domain=p.domain;
  UPDATE markos_runtime.mailboxes SET disconnect_deadline=NULL,lease_until=NULL WHERE owner_id=p.owner_id AND domain=p.domain;
  RETURN NULL;
 END IF;
 IF NOT held THEN RETURN NULL;END IF;
 renew:=b.connected AND (b.renew_due_at<=tick OR b.watch_expires_at IS NULL OR b.watch_expires_at<=tick+interval '1 day');
 poll:=b.connected AND b.poll_due_at<=tick;
 IF b.connected AND NOT (renew OR poll) THEN RETURN NULL;END IF;
 UPDATE markos_runtime.mailboxes SET fence=fence+1,lease_until=tick+make_interval(secs=>p_seconds)
 WHERE owner_id=p.owner_id AND domain=p.domain RETURNING * INTO b;
 RETURN jsonb_build_object('fence',b.fence,'disconnect',NOT b.connected,'renew',renew,'sync',poll,'alert_seq',b.alert_seq,
  'labels',to_jsonb(b.labels),'owner_id',b.owner_id,'domain',b.domain,'key_id',t.key_id,'sealed',encode(t.sealed,'base64'));
END $$;
-- Called after Google has stopped the watch and revoked the token. Ignored under an old fence.
CREATE FUNCTION markos_runtime.finish_mail_disconnect(p_fence bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;b markos_runtime.mailboxes;BEGIN
 p:=markos_runtime.mail_identity();
 SELECT * INTO b FROM markos_runtime.mailboxes WHERE owner_id=p.owner_id AND domain=p.domain FOR UPDATE;
 IF NOT FOUND OR b.connected OR b.disconnect_deadline IS NULL OR b.fence IS DISTINCT FROM p_fence OR b.lease_until IS NULL THEN
  RETURN false;END IF;
 DELETE FROM markos_runtime.mail_tokens WHERE owner_id=p.owner_id AND domain=p.domain;
 UPDATE markos_runtime.mailboxes SET disconnect_deadline=NULL,lease_until=NULL WHERE owner_id=p.owner_id AND domain=p.domain;
 RETURN true;
END $$;

REVOKE ALL ON FUNCTION markos_control.mail_change(uuid,uuid,text,text,text[]),markos_control.mail_view(uuid,text),
 markos_control.connect_mailbox(uuid,text,uuid,text,text,text[],bytea,text),markos_control.set_mail_labels(uuid,text,uuid,text,text[]),
 markos_control.disconnect_mailbox(uuid,text,uuid,text),markos_control.read_mailbox(uuid,text,uuid,text),
 markos_runtime.finish_mail_disconnect(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.connect_mailbox(uuid,text,uuid,text,text,text[],bytea,text),
 markos_control.set_mail_labels(uuid,text,uuid,text,text[]),markos_control.disconnect_mailbox(uuid,text,uuid,text),
 markos_control.read_mailbox(uuid,text,uuid,text) TO markos_control_gateway;
GRANT EXECUTE ON FUNCTION markos_runtime.finish_mail_disconnect(bigint) TO markos_runtime_client;
