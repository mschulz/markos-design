-- M2-A: Gmail mailbox state and message records. Synthetic only: no OAuth, alerts or events.
-- Mail bodies and attachment names are never stored. Access is only through the functions below.
ALTER TABLE markos_runtime.service_identities ADD COLUMN can_ingest_mail boolean NOT NULL DEFAULT false;

CREATE FUNCTION markos_runtime.mail_labels_valid(p text[]) RETURNS boolean LANGUAGE sql IMMUTABLE
SET search_path=pg_catalog AS $$
 SELECT cardinality(p) BETWEEN 1 AND 20
 AND (SELECT count(DISTINCT l)=cardinality(p) AND coalesce(bool_and(l~'^[A-Za-z0-9_-]{1,64}$'),false) FROM unnest(p) l)
$$;
CREATE FUNCTION markos_runtime.mail_attachments_valid(a jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE
SET search_path=pg_catalog AS $$
 SELECT CASE WHEN jsonb_typeof(a)<>'array' OR jsonb_array_length(a)>50 THEN false ELSE NOT EXISTS(
  SELECT 1 FROM jsonb_array_elements(a) x WHERE NOT coalesce(jsonb_typeof(x)='object'
   AND (SELECT count(*) FROM jsonb_object_keys(x))=4
   AND x->>'part_id'~'^[0-9]{1,8}(\.[0-9]{1,8}){0,7}$'
   AND x->>'attachment_id'~'^[A-Za-z0-9_-]+$' AND length(x->>'attachment_id')<=1024
   AND x->>'mime_type'~'^[a-z0-9.+-]{1,63}/[a-z0-9.+-]{1,63}$'
   AND jsonb_typeof(x->'size')='number' AND x->>'size'~'^[0-9]{1,9}$',false)) END
$$;

CREATE TABLE markos_runtime.mailboxes(
 owner_id uuid NOT NULL CHECK(owner_id<>'00000000-0000-0000-0000-000000000000'),
 domain text NOT NULL CHECK(domain IN ('personal','research','work')),
 address_hash text NOT NULL CHECK(address_hash~'^[a-f0-9]{64}$'),
 labels text[] NOT NULL CHECK(markos_runtime.mail_labels_valid(labels)),
 history_id numeric(20,0) CHECK(history_id>0),
 connected boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain)
);
CREATE TABLE markos_runtime.mail_messages(
 owner_id uuid NOT NULL,domain text NOT NULL,
 message_id text NOT NULL CHECK(message_id~'^[0-9a-f]{1,32}$'),
 thread_id text NOT NULL CHECK(thread_id~'^[0-9a-f]{1,32}$'),
 history_id numeric(20,0) NOT NULL CHECK(history_id>0),
 received_at timestamptz NOT NULL,
 label_ids text[] NOT NULL CHECK(markos_runtime.mail_labels_valid(label_ids)),
 attachments jsonb NOT NULL CHECK(markos_runtime.mail_attachments_valid(attachments)),
 status text NOT NULL DEFAULT 'ingested' CHECK(status IN ('ingested')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,message_id),
 FOREIGN KEY(owner_id,domain) REFERENCES markos_runtime.mailboxes
);
-- One row per resync is the resync notice.
CREATE TABLE markos_runtime.mail_resyncs(
 owner_id uuid NOT NULL,domain text NOT NULL,
 resync_id uuid NOT NULL DEFAULT gen_random_uuid(),
 reason text NOT NULL CHECK(reason IN ('first-import','history-lost')),
 history_from numeric(20,0),history_to numeric(20,0) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,resync_id),
 FOREIGN KEY(owner_id,domain) REFERENCES markos_runtime.mailboxes,
 CHECK((reason='first-import')=(history_from IS NULL))
);
ALTER TABLE markos_runtime.mailboxes ENABLE ROW LEVEL SECURITY;
ALTER TABLE markos_runtime.mail_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE markos_runtime.mail_resyncs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_runtime.mailboxes,markos_runtime.mail_messages,markos_runtime.mail_resyncs
 FROM PUBLIC,markos_runtime_client,markos_control_gateway;

CREATE FUNCTION markos_runtime.mail_identity() RETURNS markos_runtime.service_identities
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;BEGIN
 SELECT * INTO p FROM markos_runtime.service_identities WHERE login_role=session_user AND can_ingest_mail;
 IF NOT FOUND THEN RAISE EXCEPTION 'mail ingestion not authorized' USING ERRCODE='42501';END IF;
 RETURN p;
END $$;
CREATE FUNCTION markos_runtime.mail_state(p_owner uuid,p_domain text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
 SELECT jsonb_build_object('labels',to_jsonb(labels),'history_id',history_id::text,'connected',connected)
 FROM markos_runtime.mailboxes WHERE owner_id=p_owner AND domain=p_domain
$$;
-- Inserts once per message ID. Every message must carry a label on the mailbox allowlist.
CREATE FUNCTION markos_runtime.mail_insert(p_owner uuid,p_domain text,p_labels text[],p_messages jsonb) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime SET TimeZone='UTC' AS $$
DECLARE n integer;BEGIN
 IF jsonb_typeof(p_messages) IS DISTINCT FROM 'array' OR jsonb_array_length(p_messages)>100 THEN
  RAISE EXCEPTION 'invalid mail batch' USING ERRCODE='22023';END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_messages) m
 WHERE jsonb_typeof(m)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(m))<>6
 OR NOT ARRAY(SELECT jsonb_array_elements_text(m->'label_ids')) && p_labels) THEN
  RAISE EXCEPTION 'mail label not allowed' USING ERRCODE='42501';END IF;
 INSERT INTO markos_runtime.mail_messages(owner_id,domain,message_id,thread_id,history_id,received_at,label_ids,attachments)
 SELECT p_owner,p_domain,m->>'message_id',m->>'thread_id',(m->>'history_id')::numeric(20,0),(m->>'received_at')::timestamptz,
 ARRAY(SELECT jsonb_array_elements_text(m->'label_ids')),m->'attachments'
 FROM jsonb_array_elements(p_messages) m ON CONFLICT DO NOTHING;
 GET DIAGNOSTICS n=ROW_COUNT;
 RETURN n;
END $$;

-- Synthetic opt-in until M2-C moves connecting to the OAuth flow. One mailbox per owner and domain.
CREATE FUNCTION markos_runtime.open_mailbox(p_address_hash text,p_labels text[]) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;b markos_runtime.mailboxes;BEGIN
 p:=markos_runtime.mail_identity();
 INSERT INTO markos_runtime.mailboxes(owner_id,domain,address_hash,labels) VALUES(p.owner_id,p.domain,p_address_hash,p_labels)
 ON CONFLICT DO NOTHING;
 SELECT * INTO STRICT b FROM markos_runtime.mailboxes WHERE owner_id=p.owner_id AND domain=p.domain FOR UPDATE;
 IF b.address_hash<>p_address_hash THEN RAISE EXCEPTION 'another mailbox is connected' USING ERRCODE='23505';END IF;
 UPDATE markos_runtime.mailboxes SET labels=p_labels,connected=true,updated_at=clock_timestamp()
 WHERE owner_id=p.owner_id AND domain=p.domain;
 RETURN markos_runtime.mail_state(p.owner_id,p.domain);
END $$;
CREATE FUNCTION markos_runtime.mailbox_state() RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;BEGIN
 p:=markos_runtime.mail_identity();
 RETURN markos_runtime.mail_state(p.owner_id,p.domain);
END $$;
-- Stores one page of a resync without moving the cursor.
CREATE FUNCTION markos_runtime.store_mail(p_messages jsonb) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;b markos_runtime.mailboxes;BEGIN
 p:=markos_runtime.mail_identity();
 SELECT * INTO b FROM markos_runtime.mailboxes WHERE owner_id=p.owner_id AND domain=p.domain AND connected FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'no connected mailbox' USING ERRCODE='P0002';END IF;
 RETURN markos_runtime.mail_insert(p.owner_id,p.domain,b.labels,p_messages);
END $$;
-- Stores messages and moves the cursor in one transaction. Returns NULL, with no writes, when the
-- cursor is no longer p_from. The cursor never moves backward. p_reason marks a resync.
CREATE FUNCTION markos_runtime.advance_mail(p_from numeric,p_to numeric,p_messages jsonb,p_reason text) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;b markos_runtime.mailboxes;n integer;BEGIN
 p:=markos_runtime.mail_identity();
 SELECT * INTO b FROM markos_runtime.mailboxes WHERE owner_id=p.owner_id AND domain=p.domain AND connected FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'no connected mailbox' USING ERRCODE='P0002';END IF;
 IF b.history_id IS DISTINCT FROM p_from THEN RETURN NULL;END IF;
 IF p_to IS NULL OR p_to<coalesce(p_from,1) OR (p_from IS NULL AND p_reason IS DISTINCT FROM 'first-import') THEN
  RAISE EXCEPTION 'invalid mail cursor' USING ERRCODE='22023';END IF;
 n:=markos_runtime.mail_insert(p.owner_id,p.domain,b.labels,p_messages);
 IF p_reason IS NOT NULL THEN
  INSERT INTO markos_runtime.mail_resyncs(owner_id,domain,reason,history_from,history_to) VALUES(p.owner_id,p.domain,p_reason,p_from,p_to);
 END IF;
 UPDATE markos_runtime.mailboxes SET history_id=p_to,updated_at=clock_timestamp() WHERE owner_id=p.owner_id AND domain=p.domain;
 RETURN n;
END $$;
REVOKE ALL ON FUNCTION markos_runtime.mail_labels_valid(text[]),markos_runtime.mail_attachments_valid(jsonb),
 markos_runtime.mail_identity(),markos_runtime.mail_state(uuid,text),markos_runtime.mail_insert(uuid,text,text[],jsonb),
 markos_runtime.open_mailbox(text,text[]),markos_runtime.mailbox_state(),markos_runtime.store_mail(jsonb),
 markos_runtime.advance_mail(numeric,numeric,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_runtime.open_mailbox(text,text[]),markos_runtime.mailbox_state(),
 markos_runtime.store_mail(jsonb),markos_runtime.advance_mail(numeric,numeric,jsonb,text) TO markos_runtime_client;
