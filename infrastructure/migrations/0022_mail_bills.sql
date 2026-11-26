-- M2-D: bills from one Gmail label. An administrator picks the bill label, which must be on the
-- allowlist. The mail worker reads PDF attachments of stored messages in that label, back to the
-- first import, and saves known supplier bills through the K1 PDF import. A mail bill names no
-- person: its source is 'mail' and its Gmail message. Each PDF gets one recorded outcome.
ALTER TABLE markos_runtime.mailboxes ADD COLUMN bill_label text,
 ADD CONSTRAINT mailbox_bill_label CHECK(bill_label IS NULL OR bill_label=ANY(labels));
-- A new allowlist without the bill label also clears the bill label.
CREATE FUNCTION markos_runtime.mail_keep_bill_label() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog AS $$BEGIN
 IF NEW.bill_label IS NOT NULL AND NOT NEW.bill_label=ANY(NEW.labels) THEN NEW.bill_label:=NULL;END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER mailbox_bill_label BEFORE UPDATE OF labels ON markos_runtime.mailboxes
 FOR EACH ROW EXECUTE FUNCTION markos_runtime.mail_keep_bill_label();

CREATE FUNCTION markos_runtime.mail_bill_candidate(p_label text,p_labels text[],p_attachments jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT coalesce(p_label=ANY(p_labels),false)
  AND EXISTS(SELECT 1 FROM jsonb_array_elements(p_attachments) a WHERE a->>'mime_type'='application/pdf')
$$;
ALTER TABLE markos_runtime.mail_messages ADD COLUMN bill_due boolean NOT NULL DEFAULT false;
CREATE INDEX mail_messages_bill_due ON markos_runtime.mail_messages(owner_id,domain,received_at) WHERE bill_due;
CREATE FUNCTION markos_runtime.mail_mark_bill() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,markos_runtime AS $$BEGIN
 NEW.bill_due:=markos_runtime.mail_bill_candidate((SELECT bill_label FROM markos_runtime.mailboxes
  WHERE owner_id=NEW.owner_id AND domain=NEW.domain),NEW.label_ids,NEW.attachments);
 RETURN NEW;
END $$;
CREATE TRIGGER mail_message_bill BEFORE INSERT ON markos_runtime.mail_messages
 FOR EACH ROW EXECUTE FUNCTION markos_runtime.mail_mark_bill();

CREATE TABLE markos_runtime.mail_bill_outcomes(
 owner_id uuid NOT NULL,domain text NOT NULL,message_id text NOT NULL,
 part_id text NOT NULL CHECK(part_id~'^[0-9]{1,8}(\.[0-9]{1,8}){0,7}$'),
 outcome text NOT NULL CHECK(outcome IN ('imported','duplicate','unsupported','limit','rejected')),
 reason text CHECK(reason~'^[a-z-]{1,32}$'),
 source_sha256 text CHECK(source_sha256~'^[a-f0-9]{64}$'),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,message_id,part_id),
 FOREIGN KEY(owner_id,domain,message_id) REFERENCES markos_runtime.mail_messages,
 CHECK((outcome IN ('imported','duplicate'))=(source_sha256 IS NOT NULL)),
 CHECK((outcome='rejected')=(reason IS NOT NULL))
);
ALTER TABLE markos_runtime.mail_bill_outcomes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_runtime.mail_bill_outcomes FROM PUBLIC,markos_runtime_client,markos_control_gateway;

-- A bill is imported by a person or from mail. A mail bill names its message, not a person.
ALTER TABLE markos_control.bill_records ALTER COLUMN created_by DROP NOT NULL,
 ADD COLUMN source text NOT NULL DEFAULT 'person' CHECK(source IN ('person','mail')),
 ADD COLUMN mail_message_id text CHECK(mail_message_id~'^[0-9a-f]{1,32}$'),
 ADD CONSTRAINT bill_record_source CHECK(CASE source WHEN 'person' THEN created_by IS NOT NULL AND mail_message_id IS NULL
  ELSE created_by IS NULL AND mail_message_id IS NOT NULL END);

ALTER TABLE markos_control.mail_history DROP CONSTRAINT mail_history_action_check,
 ADD CONSTRAINT mail_history_action_check CHECK(action IN ('connect','labels','disconnect','bill-label'));

CREATE OR REPLACE FUNCTION markos_control.mail_view(p_owner uuid,p_domain text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
 SELECT coalesce((SELECT jsonb_build_object(
  'state',CASE WHEN connected THEN 'connected' WHEN disconnect_deadline IS NOT NULL THEN 'disconnecting' ELSE 'disconnected' END,
  'labels',CASE WHEN connected THEN to_jsonb(labels) END,'watch_expires_at',CASE WHEN connected THEN watch_expires_at END,
  'bill_label',CASE WHEN connected THEN bill_label END)
  FROM markos_runtime.mailboxes WHERE owner_id=p_owner AND domain=p_domain),
 jsonb_build_object('state','none','labels',NULL,'watch_expires_at',NULL,'bill_label',NULL))
 ||jsonb_build_object('bills',jsonb_build_object(
  'waiting',(SELECT count(*) FROM markos_runtime.mail_messages WHERE owner_id=p_owner AND domain=p_domain AND bill_due),
  'imported',(SELECT count(*) FILTER(WHERE outcome='imported') FROM markos_runtime.mail_bill_outcomes o WHERE owner_id=p_owner AND domain=p_domain),
  'duplicate',(SELECT count(*) FILTER(WHERE outcome='duplicate') FROM markos_runtime.mail_bill_outcomes o WHERE owner_id=p_owner AND domain=p_domain),
  'review',(SELECT count(*) FILTER(WHERE outcome IN ('unsupported','limit')) FROM markos_runtime.mail_bill_outcomes o WHERE owner_id=p_owner AND domain=p_domain),
  'rejected',(SELECT count(*) FILTER(WHERE outcome='rejected') FROM markos_runtime.mail_bill_outcomes o WHERE owner_id=p_owner AND domain=p_domain)))
$$;

-- Picking a label queues its stored PDF messages that have no outcome yet. Clearing it stops the queue.
CREATE FUNCTION markos_control.set_mail_bill_label(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_label text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE actor uuid;b markos_runtime.mailboxes;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'configure');
 SELECT * INTO b FROM markos_runtime.mailboxes WHERE owner_id=p_owner AND domain=p_domain AND connected FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'no connected mailbox' USING ERRCODE='P0002';END IF;
 IF p_label IS NOT NULL AND NOT p_label=ANY(b.labels) THEN RAISE EXCEPTION 'bill label not allowed' USING ERRCODE='40001';END IF;
 UPDATE markos_runtime.mailboxes SET bill_label=p_label,updated_at=clock_timestamp() WHERE owner_id=p_owner AND domain=p_domain;
 UPDATE markos_runtime.mail_messages m SET bill_due=markos_runtime.mail_bill_candidate(p_label,m.label_ids,m.attachments)
  AND NOT EXISTS(SELECT 1 FROM markos_runtime.mail_bill_outcomes o
   WHERE o.owner_id=m.owner_id AND o.domain=m.domain AND o.message_id=m.message_id)
 WHERE m.owner_id=p_owner AND m.domain=p_domain;
 PERFORM markos_control.mail_change(actor,p_owner,p_domain,'bill-label',CASE WHEN p_label IS NULL THEN '{}'::text[] ELSE ARRAY[p_label] END);
 RETURN markos_control.mail_view(p_owner,p_domain);
END $$;

-- Bill work is due only for a worker that has a bill folder.
DROP FUNCTION markos_runtime.claim_mail_work(integer);
CREATE FUNCTION markos_runtime.claim_mail_work(p_seconds integer,p_bills boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;b markos_runtime.mailboxes;t markos_runtime.mail_tokens;
 tick timestamptz:=clock_timestamp();renew boolean;poll boolean;bills boolean;held boolean;BEGIN
 p:=markos_runtime.mail_identity();
 IF p_seconds IS NULL OR p_seconds NOT BETWEEN 10 AND 300 OR p_bills IS NULL THEN
  RAISE EXCEPTION 'invalid mail lease' USING ERRCODE='22023';END IF;
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
 bills:=b.connected AND p_bills AND b.bill_label IS NOT NULL AND EXISTS(SELECT 1 FROM markos_runtime.mail_messages
  WHERE owner_id=p.owner_id AND domain=p.domain AND bill_due);
 IF b.connected AND NOT (renew OR poll OR bills) THEN RETURN NULL;END IF;
 UPDATE markos_runtime.mailboxes SET fence=fence+1,lease_until=tick+make_interval(secs=>p_seconds)
 WHERE owner_id=p.owner_id AND domain=p.domain RETURNING * INTO b;
 RETURN jsonb_build_object('fence',b.fence,'disconnect',NOT b.connected,'renew',renew,'sync',poll,'bills',bills,
  'alert_seq',b.alert_seq,'labels',to_jsonb(b.labels),'owner_id',b.owner_id,'domain',b.domain,
  'key_id',t.key_id,'sealed',encode(t.sealed,'base64'));
END $$;

CREATE FUNCTION markos_runtime.mail_bill_lease(p_fence bigint) RETURNS markos_runtime.mailboxes
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;b markos_runtime.mailboxes;BEGIN
 p:=markos_runtime.mail_identity();
 SELECT * INTO b FROM markos_runtime.mailboxes WHERE owner_id=p.owner_id AND domain=p.domain FOR UPDATE;
 IF NOT FOUND OR NOT b.connected OR b.bill_label IS NULL OR b.fence IS DISTINCT FROM p_fence
  OR NOT b.lease_until>clock_timestamp() THEN RETURN NULL;END IF;
 RETURN b;
END $$;
-- The oldest queued bill messages, under the current lease only. NULL means the lease is gone.
CREATE FUNCTION markos_runtime.mail_bills_due(p_fence bigint,p_limit integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime SET TimeZone='UTC' AS $$
DECLARE b markos_runtime.mailboxes;BEGIN
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 20 THEN RAISE EXCEPTION 'invalid bill batch' USING ERRCODE='22023';END IF;
 b:=markos_runtime.mail_bill_lease(p_fence);
 IF b.owner_id IS NULL THEN RETURN NULL;END IF;
 RETURN coalesce((SELECT jsonb_agg(jsonb_build_object('message_id',message_id,'received_at',received_at,
  'attachments',attachments) ORDER BY received_at,message_id) FROM (SELECT * FROM markos_runtime.mail_messages
  WHERE owner_id=b.owner_id AND domain=b.domain AND bill_due ORDER BY received_at,message_id LIMIT p_limit) m),'[]'::jsonb);
END $$;
-- One result for each PDF attachment of the message. A result with a report is saved as a bill
-- unless its SHA-256 is already a bill, or the bill limit is reached. NULL means the lease is gone.
CREATE FUNCTION markos_runtime.finish_mail_bill(p_fence bigint,p_message text,p_results jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE b markos_runtime.mailboxes;m markos_runtime.mail_messages;r jsonb;kind text;outcome text;bill uuid;
 done jsonb:='[]'::jsonb;BEGIN
 b:=markos_runtime.mail_bill_lease(p_fence);
 IF b.owner_id IS NULL THEN RETURN NULL;END IF;
 SELECT * INTO m FROM markos_runtime.mail_messages WHERE owner_id=b.owner_id AND domain=b.domain AND message_id=p_message FOR UPDATE;
 IF NOT FOUND OR NOT m.bill_due THEN RETURN NULL;END IF;
 IF jsonb_typeof(p_results) IS DISTINCT FROM 'array'
  OR ARRAY(SELECT x->>'part_id' FROM jsonb_array_elements(p_results) x ORDER BY 1) IS DISTINCT FROM
   ARRAY(SELECT a->>'part_id' FROM jsonb_array_elements(m.attachments) a WHERE a->>'mime_type'='application/pdf' ORDER BY 1) THEN
  RAISE EXCEPTION 'one result for each PDF attachment required' USING ERRCODE='22023';END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(b.owner_id::text||':'||b.domain||':bills',0));
 FOR r IN SELECT * FROM jsonb_array_elements(p_results) LOOP
  kind:=r->>'kind';
  IF kind='save' THEN
   SELECT bill_id INTO bill FROM markos_control.bill_records WHERE owner_id=b.owner_id AND domain=b.domain AND source_sha256=r->>'source_sha256';
   IF FOUND THEN outcome:='duplicate';
   ELSIF (SELECT count(*) FROM markos_control.bill_records WHERE owner_id=b.owner_id AND domain=b.domain)>=500 THEN outcome:='limit';
   ELSE
    INSERT INTO markos_control.bill_records(owner_id,domain,source_sha256,name,report,source,mail_message_id)
    VALUES(b.owner_id,b.domain,r->>'source_sha256',r->>'name',r->'report','mail',p_message) RETURNING bill_id INTO bill;
    -- The all-zero actor is the mail worker, not a person.
    INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id,revision)
    VALUES('00000000-0000-0000-0000-000000000000',b.owner_id,b.domain,'bill-import.mail',bill,1);
    outcome:='imported';
   END IF;
  ELSIF kind IN ('unsupported','limit','rejected') THEN outcome:=kind;
  ELSE RAISE EXCEPTION 'invalid bill result' USING ERRCODE='22023';END IF;
  INSERT INTO markos_runtime.mail_bill_outcomes(owner_id,domain,message_id,part_id,outcome,reason,source_sha256)
  VALUES(b.owner_id,b.domain,p_message,r->>'part_id',outcome,CASE WHEN outcome='rejected' THEN r->>'reason' END,
   CASE WHEN outcome IN ('imported','duplicate') THEN r->>'source_sha256' END);
  done:=done||to_jsonb(outcome);
 END LOOP;
 UPDATE markos_runtime.mail_messages SET bill_due=false WHERE owner_id=b.owner_id AND domain=b.domain AND message_id=p_message;
 RETURN done;
END $$;

REVOKE ALL ON FUNCTION markos_runtime.mail_keep_bill_label(),markos_runtime.mail_mark_bill(),
 markos_runtime.mail_bill_candidate(text,text[],jsonb),markos_control.set_mail_bill_label(uuid,text,uuid,text,text),
 markos_runtime.claim_mail_work(integer,boolean),markos_runtime.mail_bill_lease(bigint),
 markos_runtime.mail_bills_due(bigint,integer),markos_runtime.finish_mail_bill(bigint,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.set_mail_bill_label(uuid,text,uuid,text,text) TO markos_control_gateway;
GRANT EXECUTE ON FUNCTION markos_runtime.claim_mail_work(integer,boolean),markos_runtime.mail_bills_due(bigint,integer),
 markos_runtime.finish_mail_bill(bigint,text,jsonb) TO markos_runtime_client;
