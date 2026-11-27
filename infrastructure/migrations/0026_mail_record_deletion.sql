-- M7-F: mail record deletion. Deleting a mail record removes its labels, attachment references and
-- bill outcomes. A tombstone keeps only the Gmail message ID, so a resync never stores it again.
-- A bill imported from the message stays, with its own SHA-256 and Gmail pointer.
CREATE TABLE markos_runtime.mail_tombstones(
 owner_id uuid NOT NULL,
 domain text NOT NULL,
 message_id text NOT NULL CHECK(message_id~'^[0-9a-f]{1,32}$'),
 deleted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,message_id)
);
ALTER TABLE markos_runtime.mail_tombstones ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_runtime.mail_tombstones FROM PUBLIC,markos_runtime_client,markos_control_gateway;

-- Same as 0019, but skips tombstoned message IDs.
CREATE OR REPLACE FUNCTION markos_runtime.mail_insert(p_owner uuid,p_domain text,p_labels text[],p_messages jsonb) RETURNS integer
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
 FROM jsonb_array_elements(p_messages) m
 WHERE NOT EXISTS(SELECT 1 FROM markos_runtime.mail_tombstones t
  WHERE t.owner_id=p_owner AND t.domain=p_domain AND t.message_id=m->>'message_id')
 ON CONFLICT DO NOTHING;
 GET DIAGNOSTICS n=ROW_COUNT;
 RETURN n;
END $$;

-- The newest 100 records, without attachment IDs.
CREATE FUNCTION markos_control.mail_records(p_session uuid,p_hash text,p_owner uuid,p_domain text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 RETURN coalesce((SELECT jsonb_agg(jsonb_build_object(
  'message_id',m.message_id,'thread_id',m.thread_id,'received_at',m.received_at,'labels',to_jsonb(m.label_ids),
  'attachments',jsonb_array_length(m.attachments),'status',m.status,
  'bill_outcomes',(SELECT coalesce(jsonb_agg(o.outcome ORDER BY o.part_id),'[]'::jsonb) FROM markos_runtime.mail_bill_outcomes o
   WHERE o.owner_id=m.owner_id AND o.domain=m.domain AND o.message_id=m.message_id))
  ORDER BY m.received_at DESC,m.message_id)
  FROM (SELECT * FROM markos_runtime.mail_messages WHERE owner_id=p_owner AND domain=p_domain
   ORDER BY received_at DESC,message_id LIMIT 100) m),'[]'::jsonb);
END $$;

CREATE FUNCTION markos_control.delete_mail_record(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_message text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE actor uuid;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 DELETE FROM markos_runtime.mail_bill_outcomes WHERE owner_id=p_owner AND domain=p_domain AND message_id=p_message;
 DELETE FROM markos_runtime.mail_messages WHERE owner_id=p_owner AND domain=p_domain AND message_id=p_message;
 IF NOT FOUND THEN RAISE EXCEPTION 'mail record not found' USING ERRCODE='P0002';END IF;
 INSERT INTO markos_runtime.mail_tombstones(owner_id,domain,message_id) VALUES(p_owner,p_domain,p_message);
 -- The audit row names the record by a hash, not the Gmail message ID.
 INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id,revision)
 VALUES(actor,p_owner,p_domain,'mail-record-delete',md5(p_owner::text||':'||p_message)::uuid,1);
 RETURN jsonb_build_object('message_id',p_message,'deleted',true);
END $$;

REVOKE ALL ON FUNCTION markos_control.mail_records(uuid,text,uuid,text),
 markos_control.delete_mail_record(uuid,text,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.mail_records(uuid,text,uuid,text),
 markos_control.delete_mail_record(uuid,text,uuid,text,text) TO markos_control_gateway;
