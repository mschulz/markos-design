-- Human-initiated bill-text slice. Originals stay in a private object adapter;
-- PostgreSQL owns source references, extracted figures and their provenance.
CREATE TABLE markos_control.bill_records(
 owner_id uuid NOT NULL CHECK(owner_id<>'00000000-0000-0000-0000-000000000000'),
 domain text NOT NULL CHECK(domain IN ('personal','research','work')),
 bill_id uuid NOT NULL DEFAULT gen_random_uuid(),
 source_sha256 text NOT NULL CHECK(source_sha256~'^[a-f0-9]{64}$'),
 name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 120),
 report jsonb NOT NULL CHECK((report->>'extractor'='bill-text-v1'
  AND report->>'source_sha256'=source_sha256 AND jsonb_typeof(report->'facts')='array' AND jsonb_array_length(report->'facts')=6) IS TRUE),
 created_by uuid NOT NULL REFERENCES markos_control.principals,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(owner_id,domain,bill_id),UNIQUE(owner_id,domain,source_sha256)
);
ALTER TABLE markos_control.bill_records ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON markos_control.bill_records FROM PUBLIC,markos_control_gateway,markos_runtime_client;

CREATE FUNCTION markos_control.bill_access(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_write boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$BEGIN
 IF p_write IS NULL THEN RAISE EXCEPTION 'bill operation required' USING ERRCODE='42501';END IF;
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,CASE WHEN p_write THEN 'chat' ELSE 'inspect' END);
END $$;
CREATE FUNCTION markos_control.save_bill(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_name text,p_source text,p_report jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$
DECLARE actor uuid;b markos_control.bill_records;
BEGIN
 actor:=markos_control.authorize(p_session,p_hash,p_owner,p_domain,'chat');
 PERFORM pg_advisory_xact_lock(hashtextextended(p_owner::text||':'||p_domain||':bills',0));
 SELECT * INTO b FROM markos_control.bill_records WHERE owner_id=p_owner AND domain=p_domain AND source_sha256=p_source;
 IF NOT FOUND THEN
  IF (SELECT count(*) FROM markos_control.bill_records WHERE owner_id=p_owner AND domain=p_domain)>=100 THEN
   RAISE EXCEPTION 'preview bill limit reached' USING ERRCODE='54000';END IF;
  INSERT INTO markos_control.bill_records(owner_id,domain,source_sha256,name,report,created_by)
  VALUES(p_owner,p_domain,p_source,p_name,p_report,actor) RETURNING * INTO b;
  INSERT INTO markos_control.audit(actor_id,owner_id,domain,operation,object_id,revision)
  VALUES(actor,p_owner,p_domain,'bill-import',b.bill_id,1);
 END IF;
 RETURN jsonb_build_object('schema_version',1,'bill_id',b.bill_id,'name',b.name,'source_sha256',b.source_sha256,'report',b.report);
END $$;
CREATE FUNCTION markos_control.read_bill(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_bill uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE b markos_control.bill_records;BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 SELECT * INTO b FROM markos_control.bill_records WHERE owner_id=p_owner AND domain=p_domain AND bill_id=p_bill;
 IF NOT FOUND THEN RAISE EXCEPTION 'bill not found' USING ERRCODE='P0002';END IF;
 RETURN jsonb_build_object('schema_version',1,'bill_id',b.bill_id,'name',b.name,'source_sha256',b.source_sha256,'report',b.report);
END $$;
CREATE FUNCTION markos_control.list_bills(p_session uuid,p_hash text,p_owner uuid,p_domain text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 RETURN coalesce((SELECT jsonb_agg(jsonb_build_object('bill_id',bill_id,'name',name,'source_sha256',source_sha256) ORDER BY created_at DESC,bill_id)
 FROM markos_control.bill_records WHERE owner_id=p_owner AND domain=p_domain),'[]'::jsonb);
END $$;
REVOKE ALL ON FUNCTION markos_control.bill_access(uuid,text,uuid,text,boolean),markos_control.save_bill(uuid,text,uuid,text,text,text,jsonb),
 markos_control.read_bill(uuid,text,uuid,text,uuid),markos_control.list_bills(uuid,text,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.bill_access(uuid,text,uuid,text,boolean),markos_control.save_bill(uuid,text,uuid,text,text,text,jsonb),
 markos_control.read_bill(uuid,text,uuid,text,uuid),markos_control.list_bills(uuid,text,uuid,text) TO markos_control_gateway;
