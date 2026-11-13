-- Explanations only: preserve approval decisions, fences and all existing authority checks.
ALTER TABLE markos_control.tool_intents ADD COLUMN cancellation_reason text;
ALTER TABLE markos_control.tool_intents ADD CHECK(cancellation_reason IS NULL OR
 (status='revoked' AND cancellation_reason IN ('requester-signed-out','sign-in-expired','access-changed',
 'settings-changed','preview-restored','request-cancelled','approval-withdrawn','test-service-check-failed','unknown')));

-- Old generic authority records can only be explained where retained evidence proves the cause.
UPDATE markos_control.tool_intents i SET cancellation_reason=CASE
 WHEN EXISTS(SELECT 1 FROM markos_control.tool_audit a WHERE a.intent_id=i.intent_id AND a.owner_id=i.owner_id AND a.domain=i.domain AND a.outcome='revoked' AND a.reason='human-decision') THEN 'approval-withdrawn'
 WHEN EXISTS(SELECT 1 FROM markos_control.tool_audit a WHERE a.intent_id=i.intent_id AND a.owner_id=i.owner_id AND a.domain=i.domain AND a.outcome='revoked' AND a.reason='grant-revision') THEN 'access-changed'
 WHEN i.epoch=markos_runtime.current_epoch() AND EXISTS(SELECT 1 FROM markos_control.chat_runs r JOIN markos_control.sessions s ON s.session_id=r.session_id AND s.principal_id=r.actor_id
 WHERE r.owner_id=i.owner_id AND r.domain=i.domain AND r.event_id=i.event_id AND s.revoked_at IS NOT NULL AND s.revoked_at<=i.terminal_at) THEN 'requester-signed-out'
 ELSE 'unknown' END WHERE status='revoked';

CREATE FUNCTION markos_control.capture_cancellation_reason() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_control AS $$DECLARE r markos_control.chat_runs;s markos_control.sessions;at_time timestamptz;BEGIN
 IF NEW.status<>'revoked' THEN NEW.cancellation_reason:=NULL;RETURN NEW;END IF;
 IF TG_OP='UPDATE' AND OLD.status='revoked' THEN RETURN NEW;END IF;
 at_time:=coalesce(NEW.terminal_at,clock_timestamp());
 SELECT * INTO r FROM markos_control.chat_runs WHERE owner_id=NEW.owner_id AND domain=NEW.domain AND event_id=NEW.event_id;
 SELECT * INTO s FROM markos_control.sessions WHERE session_id=r.session_id AND principal_id=r.actor_id;
 NEW.cancellation_reason:=CASE
 WHEN NEW.epoch IS DISTINCT FROM markos_runtime.current_epoch() THEN 'preview-restored'
 WHEN s.revoked_at IS NOT NULL AND s.revoked_at<=at_time THEN 'requester-signed-out'
 WHEN s.expires_at<=at_time OR s.provider_expires_at<=at_time OR (s.kind='browser' AND s.last_seen_at+interval '30 minutes'<=at_time) THEN 'sign-in-expired'
 WHEN r.state='cancelled' THEN 'request-cancelled'
 WHEN NOT EXISTS(SELECT 1 FROM markos_control.principals WHERE principal_id=NEW.principal_id AND NOT disabled)
 OR NOT EXISTS(SELECT 1 FROM markos_control.memberships WHERE principal_id=NEW.principal_id AND owner_id=NEW.owner_id AND domain=NEW.domain AND 'operator'=ANY(roles))
 OR NOT EXISTS(SELECT 1 FROM markos_control.tool_grants WHERE owner_id=NEW.owner_id AND domain=NEW.domain AND grant_id=NEW.grant_id AND revision=NEW.grant_revision AND enabled) THEN 'access-changed'
 WHEN NOT EXISTS(SELECT 1 FROM markos_control.agents a JOIN markos_control.configurations c USING(owner_id,domain)
 WHERE a.owner_id=NEW.owner_id AND a.domain=NEW.domain AND a.agent_id='control-test-agent' AND a.enabled AND a.revision=r.agent_revision AND c.revision=r.config_revision AND c.document->'agent'=a.manifest) THEN 'settings-changed'
 ELSE 'unknown' END;
 RETURN NEW;
END $$;
CREATE TRIGGER capture_cancellation_reason BEFORE INSERT OR UPDATE ON markos_control.tool_intents
FOR EACH ROW EXECUTE FUNCTION markos_control.capture_cancellation_reason();

-- Existing decision functions append these definitive facts in the same transaction.
CREATE FUNCTION markos_control.capture_cancellation_decision() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_control AS $$BEGIN
 IF NEW.outcome='revoked' AND NEW.reason IN ('human-decision','grant-revision') AND NEW.intent_id IS NOT NULL THEN
  UPDATE markos_control.tool_intents SET cancellation_reason=CASE NEW.reason WHEN 'human-decision' THEN 'approval-withdrawn' ELSE 'access-changed' END
  WHERE intent_id=NEW.intent_id AND owner_id=NEW.owner_id AND domain=NEW.domain AND status='revoked';
 END IF;RETURN NEW;
END $$;
CREATE TRIGGER capture_cancellation_decision AFTER INSERT ON markos_control.tool_audit
FOR EACH ROW EXECUTE FUNCTION markos_control.capture_cancellation_decision();
REVOKE ALL ON FUNCTION markos_control.capture_cancellation_reason(),markos_control.capture_cancellation_decision() FROM PUBLIC,markos_control_gateway,markos_runtime_client;

-- Delegate authorization, sweep, scope and pagination to the reviewed M1-F readers first.
ALTER FUNCTION markos_control.read_tool_records(uuid,text,uuid,text,text,bigint,integer) RENAME TO read_tool_records_m1f;
REVOKE ALL ON FUNCTION markos_control.read_tool_records_m1f(uuid,text,uuid,text,text,bigint,integer) FROM markos_control_gateway;
CREATE FUNCTION markos_control.read_tool_records(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_kind text,p_after bigint,p_limit integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE answer jsonb;BEGIN
 answer:=markos_control.read_tool_records_m1f(p_session,p_hash,p_owner,p_domain,p_kind,p_after,p_limit);
 IF p_kind='approvals' THEN
  SELECT coalesce(jsonb_agg(item.value||jsonb_build_object('cancellation_reason',i.cancellation_reason) ORDER BY item.ord),'[]'::jsonb) INTO answer
  FROM jsonb_array_elements(answer) WITH ORDINALITY item(value,ord)
  JOIN markos_control.tool_intents i ON i.intent_id=(item.value->>'intent_id')::uuid AND i.owner_id=p_owner AND i.domain=p_domain;
 END IF;RETURN answer;
END $$;
ALTER FUNCTION markos_control.read_tool_object(uuid,text,uuid,text,text,uuid) RENAME TO read_tool_object_m1f;
REVOKE ALL ON FUNCTION markos_control.read_tool_object_m1f(uuid,text,uuid,text,text,uuid) FROM markos_control_gateway;
CREATE FUNCTION markos_control.read_tool_object(p_session uuid,p_hash text,p_owner uuid,p_domain text,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_control AS $$DECLARE answer jsonb;BEGIN
 answer:=markos_control.read_tool_object_m1f(p_session,p_hash,p_owner,p_domain,p_kind,p_id);
 IF p_kind='approvals' THEN
  answer:=answer||jsonb_build_object('cancellation_reason',(SELECT cancellation_reason FROM markos_control.tool_intents WHERE intent_id=p_id AND owner_id=p_owner AND domain=p_domain));
 END IF;RETURN answer;
END $$;
REVOKE ALL ON FUNCTION markos_control.read_tool_records(uuid,text,uuid,text,text,bigint,integer),markos_control.read_tool_object(uuid,text,uuid,text,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_control.read_tool_records(uuid,text,uuid,text,text,bigint,integer),markos_control.read_tool_object(uuid,text,uuid,text,text,uuid) TO markos_control_gateway;

-- Preserve the existing fail-closed service check, while naming its cancellation cause.
ALTER FUNCTION markos_runtime.invalidate_tool_grant(uuid,bigint,uuid,uuid) RENAME TO invalidate_tool_grant_m1f;
REVOKE ALL ON FUNCTION markos_runtime.invalidate_tool_grant_m1f(uuid,bigint,uuid,uuid) FROM markos_runtime_client;
CREATE FUNCTION markos_runtime.invalidate_tool_grant(p_task uuid,p_fence bigint,p_executor uuid,p_epoch uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$DECLARE t markos_runtime.tasks;since_at timestamptz:=clock_timestamp();BEGIN
 t:=markos_runtime.tool_task(p_task,p_fence,p_executor,p_epoch);
 PERFORM markos_runtime.invalidate_tool_grant_m1f(p_task,p_fence,p_executor,p_epoch);
 UPDATE markos_control.tool_intents SET cancellation_reason='test-service-check-failed'
 WHERE owner_id=t.owner_id AND domain=t.domain AND status='revoked' AND cancellation_reason='access-changed' AND terminal_at>=since_at;
END $$;
REVOKE ALL ON FUNCTION markos_runtime.invalidate_tool_grant(uuid,bigint,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_runtime.invalidate_tool_grant(uuid,bigint,uuid,uuid) TO markos_runtime_client;
