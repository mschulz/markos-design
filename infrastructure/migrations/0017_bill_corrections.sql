-- K1-C1: owner corrections, kept apart from the immutable extracted report, and a 500-bill scope limit.
CREATE TABLE markos_control.bill_corrections(
 owner_id uuid NOT NULL,
 domain text NOT NULL,
 bill_id uuid NOT NULL,
 correction_id bigint GENERATED ALWAYS AS IDENTITY,
 fact_key text NOT NULL CHECK(fact_key~'^[a-z_]{1,32}$'),
 value text CHECK(value IS NULL OR length(value) BETWEEN 1 AND 40),
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 200),
 created_by uuid NOT NULL REFERENCES markos_control.principals,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,bill_id,correction_id),
 FOREIGN KEY(owner_id,domain,bill_id) REFERENCES markos_control.bill_records
);
ALTER TABLE markos_control.bill_corrections ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_control.bill_corrections FROM PUBLIC,markos_control_gateway,markos_runtime_client;

-- One wire shape for a bill: the extracted report plus every correction, oldest first.
CREATE FUNCTION markos_control.bill_json(b markos_control.bill_records) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog,markos_control AS $$
 SELECT jsonb_build_object('schema_version',1,'bill_id',b.bill_id,'name',b.name,'source_sha256',b.source_sha256,
  'report',b.report,'corrections',coalesce((SELECT jsonb_agg(jsonb_build_object('schema_version',1,
  'correction_id',c.correction_id,'key',c.fact_key,'value',c.value,'reason',c.reason,'created_at',c.created_at)
  ORDER BY c.correction_id) FROM markos_control.bill_corrections c
  WHERE c.owner_id=b.owner_id AND c.domain=b.domain AND c.bill_id=b.bill_id),'[]'::jsonb))
$$;
REVOKE ALL ON FUNCTION markos_control.bill_json(markos_control.bill_records) FROM PUBLIC;

CREATE OR REPLACE FUNCTION markos_control.save_bill(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_name text,p_source text,p_report jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$
DECLARE actor uuid;b markos_control.bill_records;
BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 PERFORM pg_advisory_xact_lock(hashtextextended(p_owner::text||':'||p_domain||':bills',0));
 SELECT * INTO b FROM markos_control.bill_records WHERE owner_id=p_owner AND domain=p_domain AND source_sha256=p_source;
 IF NOT FOUND THEN
  IF (SELECT count(*) FROM markos_control.bill_records WHERE owner_id=p_owner AND domain=p_domain)>=500 THEN
   RAISE EXCEPTION 'preview bill limit reached' USING ERRCODE='54000';END IF;
  INSERT INTO markos_control.bill_records(owner_id,domain,source_sha256,name,report,created_by)
  VALUES(p_owner,p_domain,p_source,p_name,p_report,actor) RETURNING * INTO b;
  INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id,revision)
  VALUES(actor,p_owner,p_domain,'bill-import',b.bill_id,1);
 END IF;
 RETURN markos_control.bill_json(b);
END $$;

CREATE OR REPLACE FUNCTION markos_control.read_bill(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_bill uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE b markos_control.bill_records;BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 SELECT * INTO b FROM markos_control.bill_records WHERE owner_id=p_owner AND domain=p_domain AND bill_id=p_bill;
 IF NOT FOUND THEN RAISE EXCEPTION 'bill not found' USING ERRCODE='P0002';END IF;
 RETURN markos_control.bill_json(b);
END $$;

-- The gateway validates key and value against the bill's profile before calling this.
CREATE FUNCTION markos_control.correct_bill(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_bill uuid,
 p_key text,p_value text,p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$
DECLARE actor uuid;b markos_control.bill_records;n integer;
BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 SELECT * INTO b FROM markos_control.bill_records WHERE owner_id=p_owner AND domain=p_domain AND bill_id=p_bill FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'bill not found' USING ERRCODE='P0002';END IF;
 SELECT count(*) INTO n FROM markos_control.bill_corrections WHERE owner_id=p_owner AND domain=p_domain AND bill_id=p_bill;
 IF n>=100 THEN RAISE EXCEPTION 'bill correction limit reached' USING ERRCODE='54000';END IF;
 INSERT INTO markos_control.bill_corrections(owner_id,domain,bill_id,fact_key,value,reason,created_by)
 VALUES(p_owner,p_domain,p_bill,p_key,p_value,p_reason,actor);
 INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id,revision)
 VALUES(actor,p_owner,p_domain,'bill-correction',p_bill,n+1);
 RETURN markos_control.bill_json(b);
END $$;
REVOKE ALL ON FUNCTION markos_control.correct_bill(uuid,text,uuid,text,uuid,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.correct_bill(uuid,text,uuid,text,uuid,text,text,text) TO markos_control_gateway;
