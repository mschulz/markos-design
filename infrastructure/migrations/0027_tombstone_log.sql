-- M7-G: tombstone log. The gateway also writes each tombstone to object storage, outside the
-- database. After a restore from an older Supabase backup, reapply_tombstones() replays that log,
-- so deletions made after the backup stay deleted.

-- Adds the Gmail pointer, so a logged bill tombstone also blocks a mail re-import.
CREATE OR REPLACE FUNCTION markos_control.bill_erasure_json(t markos_control.bill_tombstones) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('bill_id',t.bill_id,'source_sha256',t.source_sha256,'suffix',t.suffix,
  'mail_message_id',t.mail_message_id)
$$;

-- Same as 0026, but a repeat succeeds, so the gateway can retry a failed log write.
CREATE OR REPLACE FUNCTION markos_control.delete_mail_record(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_message text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE actor uuid;BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 IF EXISTS(SELECT 1 FROM markos_runtime.mail_tombstones WHERE owner_id=p_owner AND domain=p_domain AND message_id=p_message) THEN
  RETURN jsonb_build_object('message_id',p_message,'deleted',true);END IF;
 DELETE FROM markos_runtime.mail_bill_outcomes WHERE owner_id=p_owner AND domain=p_domain AND message_id=p_message;
 DELETE FROM markos_runtime.mail_messages WHERE owner_id=p_owner AND domain=p_domain AND message_id=p_message;
 IF NOT FOUND THEN RAISE EXCEPTION 'mail record not found' USING ERRCODE='P0002';END IF;
 INSERT INTO markos_runtime.mail_tombstones(owner_id,domain,message_id) VALUES(p_owner,p_domain,p_message);
 INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id,revision)
 VALUES(actor,p_owner,p_domain,'mail-record-delete',md5(p_owner::text||':'||p_message)::uuid,1);
 RETURN jsonb_build_object('message_id',p_message,'deleted',true);
END $$;

-- Replays logged tombstones after a restore. The caller has already erased each bill original.
-- Only the database owner (the migration role) can run it. Returns how many entries it applied.
CREATE FUNCTION markos_control.reapply_tombstones(p_items jsonb) RETURNS integer
LANGUAGE plpgsql SET search_path=pg_catalog,markos_control,markos_runtime AS $$
DECLARE x jsonb;o uuid;d text;n integer:=0;BEGIN
 FOR x IN SELECT * FROM jsonb_array_elements(p_items) LOOP
  o:=(x->>'owner_id')::uuid;d:=x->>'domain';
  IF x->>'kind'='bill' THEN
   DELETE FROM markos_control.bill_corrections c USING markos_control.bill_records b
    WHERE c.owner_id=o AND c.domain=d AND b.owner_id=o AND b.domain=d AND c.bill_id=b.bill_id
    AND b.source_sha256=x->>'source_sha256';
   DELETE FROM markos_control.bill_records WHERE owner_id=o AND domain=d AND source_sha256=x->>'source_sha256';
   INSERT INTO markos_control.bill_tombstones(owner_id,domain,source_sha256,bill_id,suffix,mail_message_id,erased_at)
   VALUES(o,d,x->>'source_sha256',(x->>'bill_id')::uuid,x->>'suffix',x->>'mail_message_id',clock_timestamp())
   ON CONFLICT DO NOTHING;
   UPDATE markos_control.bill_tombstones SET erased_at=coalesce(erased_at,clock_timestamp())
    WHERE owner_id=o AND domain=d AND source_sha256=x->>'source_sha256';
  ELSE
   DELETE FROM markos_runtime.mail_bill_outcomes WHERE owner_id=o AND domain=d AND message_id=x->>'message_id';
   DELETE FROM markos_runtime.mail_messages WHERE owner_id=o AND domain=d AND message_id=x->>'message_id';
   INSERT INTO markos_runtime.mail_tombstones(owner_id,domain,message_id) VALUES(o,d,x->>'message_id')
   ON CONFLICT DO NOTHING;
  END IF;
  n:=n+1;
 END LOOP;
 RETURN n;
END $$;
REVOKE ALL ON FUNCTION markos_control.reapply_tombstones(jsonb) FROM PUBLIC;
