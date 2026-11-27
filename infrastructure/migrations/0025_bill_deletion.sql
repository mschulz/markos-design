-- K1-D: bill deletion. Deleting a bill removes its record and corrections at once and keeps a
-- tombstone: the SHA-256, the Gmail pointer, the bill ID for a retry, and whether the stored
-- original is erased yet. A pending tombstone refuses a manual re-import of the same original, so
-- an erasure cannot remove a new bill's original. Mail imports stay refused for good.
CREATE TABLE markos_control.bill_tombstones(
 owner_id uuid NOT NULL,
 domain text NOT NULL,
 source_sha256 text NOT NULL CHECK(source_sha256~'^[a-f0-9]{64}$'),
 bill_id uuid NOT NULL,
 suffix text NOT NULL CHECK(suffix IN ('.txt','.pdf')),
 mail_message_id text CHECK(mail_message_id~'^[0-9a-f]{1,32}$'),
 deleted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 erased_at timestamptz,
 PRIMARY KEY(owner_id,domain,source_sha256),
 UNIQUE(owner_id,domain,bill_id)
);
CREATE INDEX bill_tombstones_pending ON markos_control.bill_tombstones(owner_id,domain) WHERE erased_at IS NULL;
ALTER TABLE markos_control.bill_tombstones ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_control.bill_tombstones FROM PUBLIC,markos_control_gateway,markos_runtime_client;

CREATE FUNCTION markos_control.bill_erasure_json(t markos_control.bill_tombstones) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('bill_id',t.bill_id,'source_sha256',t.source_sha256,'suffix',t.suffix)
$$;
REVOKE ALL ON FUNCTION markos_control.bill_erasure_json(markos_control.bill_tombstones) FROM PUBLIC;

-- Deletes the bill and returns every original in scope still to erase, this one first. Deleting
-- an already deleted bill is a retry: it returns the pending erasures without a new audit row.
CREATE FUNCTION markos_control.delete_bill(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_bill uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$
DECLARE actor uuid;b markos_control.bill_records;
BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 PERFORM pg_advisory_xact_lock(hashtextextended(p_owner::text||':'||p_domain||':bills',0));
 SELECT * INTO b FROM markos_control.bill_records WHERE owner_id=p_owner AND domain=p_domain AND bill_id=p_bill FOR UPDATE;
 IF FOUND THEN
  DELETE FROM markos_control.bill_corrections WHERE owner_id=p_owner AND domain=p_domain AND bill_id=p_bill;
  DELETE FROM markos_control.bill_records WHERE owner_id=p_owner AND domain=p_domain AND bill_id=p_bill;
  INSERT INTO markos_control.bill_tombstones(owner_id,domain,source_sha256,bill_id,suffix,mail_message_id)
  VALUES(p_owner,p_domain,b.source_sha256,p_bill,
   CASE WHEN b.report->>'extractor'='bill-text-v1' THEN '.txt' ELSE '.pdf' END,b.mail_message_id);
  INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id,revision)
  VALUES(actor,p_owner,p_domain,'bill-delete',p_bill,1);
 ELSIF NOT EXISTS(SELECT 1 FROM markos_control.bill_tombstones WHERE owner_id=p_owner AND domain=p_domain AND bill_id=p_bill) THEN
  RAISE EXCEPTION 'bill not found' USING ERRCODE='P0002';
 END IF;
 RETURN coalesce((SELECT jsonb_agg(markos_control.bill_erasure_json(t) ORDER BY t.bill_id<>p_bill,t.deleted_at)
  FROM (SELECT * FROM markos_control.bill_tombstones WHERE owner_id=p_owner AND domain=p_domain AND erased_at IS NULL
  ORDER BY bill_id<>p_bill,deleted_at LIMIT 20) t),'[]'::jsonb);
END $$;

-- The gateway calls this after the object store confirms the original is gone.
CREATE FUNCTION markos_control.bill_erased(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_source text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 UPDATE markos_control.bill_tombstones SET erased_at=clock_timestamp()
 WHERE owner_id=p_owner AND domain=p_domain AND source_sha256=p_source AND erased_at IS NULL;
END $$;

-- Deleted bills whose originals are not erased yet, newest first.
CREATE FUNCTION markos_control.bill_erasures(p_session uuid,p_hash text,p_owner uuid,p_domain text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 RETURN coalesce((SELECT jsonb_agg(jsonb_build_object('bill_id',bill_id) ORDER BY deleted_at DESC,bill_id)
  FROM markos_control.bill_tombstones WHERE owner_id=p_owner AND domain=p_domain AND erased_at IS NULL),'[]'::jsonb);
END $$;

-- A manual import of a deleted original waits for its erasure, then clears the tombstone.
CREATE OR REPLACE FUNCTION markos_control.save_bill(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_name text,p_source text,p_report jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$
DECLARE actor uuid;b markos_control.bill_records;
BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 PERFORM pg_advisory_xact_lock(hashtextextended(p_owner::text||':'||p_domain||':bills',0));
 SELECT * INTO b FROM markos_control.bill_records WHERE owner_id=p_owner AND domain=p_domain AND source_sha256=p_source;
 IF NOT FOUND THEN
  IF EXISTS(SELECT 1 FROM markos_control.bill_tombstones WHERE owner_id=p_owner AND domain=p_domain
   AND source_sha256=p_source AND erased_at IS NULL) THEN
   RAISE EXCEPTION 'bill deletion pending' USING ERRCODE='23505';END IF;
  DELETE FROM markos_control.bill_tombstones WHERE owner_id=p_owner AND domain=p_domain AND source_sha256=p_source;
  IF (SELECT count(*) FROM markos_control.bill_records WHERE owner_id=p_owner AND domain=p_domain)>=500 THEN
   RAISE EXCEPTION 'preview bill limit reached' USING ERRCODE='54000';END IF;
  INSERT INTO markos_control.bill_records(owner_id,domain,source_sha256,name,report,created_by)
  VALUES(p_owner,p_domain,p_source,p_name,p_report,actor) RETURNING * INTO b;
  INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id,revision)
  VALUES(actor,p_owner,p_domain,'bill-import',b.bill_id,1);
 END IF;
 RETURN markos_control.bill_json(b);
END $$;

-- Mail never imports a deleted bill again. 'deleted' is recorded like 'duplicate', without the hash.
ALTER TABLE markos_runtime.mail_bill_outcomes DROP CONSTRAINT mail_bill_outcomes_outcome_check,
 ADD CONSTRAINT mail_bill_outcomes_outcome_check
 CHECK(outcome IN ('imported','duplicate','unsupported','limit','rejected','deleted'));

-- The worker asks before it stores a PDF, so it does not store a deleted original again.
CREATE FUNCTION markos_runtime.mail_bill_deleted(p_fence bigint,p_source text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE b markos_runtime.mailboxes;BEGIN
 b:=markos_runtime.mail_bill_lease(p_fence);
 IF b.owner_id IS NULL THEN RETURN NULL;END IF;
 RETURN EXISTS(SELECT 1 FROM markos_control.bill_tombstones WHERE owner_id=b.owner_id AND domain=b.domain AND source_sha256=p_source);
END $$;

-- If a bill is deleted after that check, the worker may have stored its original again. The
-- tombstone goes back to pending, and the worker erases the original and reports it.
CREATE FUNCTION markos_runtime.mail_bill_erased(p_fence bigint,p_source text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE b markos_runtime.mailboxes;BEGIN
 b:=markos_runtime.mail_bill_lease(p_fence);
 IF b.owner_id IS NULL THEN RETURN NULL;END IF;
 UPDATE markos_control.bill_tombstones SET erased_at=clock_timestamp()
 WHERE owner_id=b.owner_id AND domain=b.domain AND source_sha256=p_source AND erased_at IS NULL;
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION markos_runtime.finish_mail_bill(p_fence bigint,p_message text,p_results jsonb) RETURNS jsonb
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
   ELSIF EXISTS(SELECT 1 FROM markos_control.bill_tombstones WHERE owner_id=b.owner_id AND domain=b.domain
    AND source_sha256=r->>'source_sha256') THEN
    UPDATE markos_control.bill_tombstones SET erased_at=NULL
    WHERE owner_id=b.owner_id AND domain=b.domain AND source_sha256=r->>'source_sha256';
    outcome:='deleted';
   ELSIF (SELECT count(*) FROM markos_control.bill_records WHERE owner_id=b.owner_id AND domain=b.domain)>=500 THEN outcome:='limit';
   ELSE
    INSERT INTO markos_control.bill_records(owner_id,domain,source_sha256,name,report,source,mail_message_id)
    VALUES(b.owner_id,b.domain,r->>'source_sha256',r->>'name',r->'report','mail',p_message) RETURNING bill_id INTO bill;
    -- The all-zero actor is the mail worker, not a person.
    INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id,revision)
    VALUES('00000000-0000-0000-0000-000000000000',b.owner_id,b.domain,'bill-import.mail',bill,1);
    outcome:='imported';
   END IF;
  ELSIF kind IN ('unsupported','limit','rejected','deleted') THEN outcome:=kind;
  ELSE RAISE EXCEPTION 'invalid bill result' USING ERRCODE='22023';END IF;
  INSERT INTO markos_runtime.mail_bill_outcomes(owner_id,domain,message_id,part_id,outcome,reason,source_sha256)
  VALUES(b.owner_id,b.domain,p_message,r->>'part_id',outcome,CASE WHEN outcome='rejected' THEN r->>'reason' END,
   CASE WHEN outcome IN ('imported','duplicate') THEN r->>'source_sha256' END);
  done:=done||to_jsonb(outcome);
 END LOOP;
 UPDATE markos_runtime.mail_messages SET bill_due=false WHERE owner_id=b.owner_id AND domain=b.domain AND message_id=p_message;
 RETURN done;
END $$;

REVOKE ALL ON FUNCTION markos_control.delete_bill(uuid,text,uuid,text,uuid),
 markos_control.bill_erased(uuid,text,uuid,text,text),markos_control.bill_erasures(uuid,text,uuid,text),
 markos_runtime.mail_bill_deleted(bigint,text),markos_runtime.mail_bill_erased(bigint,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.delete_bill(uuid,text,uuid,text,uuid),
 markos_control.bill_erased(uuid,text,uuid,text,text),markos_control.bill_erasures(uuid,text,uuid,text) TO markos_control_gateway;
GRANT EXECUTE ON FUNCTION markos_runtime.mail_bill_deleted(bigint,text),markos_runtime.mail_bill_erased(bigint,text)
 TO markos_runtime_client;
