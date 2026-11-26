-- K1-C2: every bill in a scope with its report and corrections, for the combined history.
-- Read only. At most 500 bills per scope (0017), so one response holds them all.
CREATE FUNCTION markos_control.bill_history(p_session uuid,p_hash text,p_owner uuid,p_domain text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$BEGIN
 PERFORM markos_control.authorize(p_session,p_hash,p_owner,p_domain,'inspect');
 RETURN coalesce((SELECT jsonb_agg(markos_control.bill_json(b) ORDER BY b.created_at,b.bill_id)
  FROM markos_control.bill_records b WHERE b.owner_id=p_owner AND b.domain=p_domain),'[]'::jsonb);
END $$;
REVOKE ALL ON FUNCTION markos_control.bill_history(uuid,text,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.bill_history(uuid,text,uuid,text) TO markos_control_gateway;
