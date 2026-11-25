-- Keep recorded reasons immutable across concurrent service-check failures.
-- Preserve 0012 for databases where it has already been explicitly applied.
CREATE OR REPLACE FUNCTION markos_runtime.invalidate_tool_grant(
 p_task uuid,p_fence bigint,p_executor uuid,p_epoch uuid
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,markos_runtime AS $$
DECLARE t markos_runtime.tasks;affected uuid[];
BEGIN
 -- Validate the executor and acquire the existing serialization fence first.
 t:=markos_runtime.tool_task(p_task,p_fence,p_executor,p_epoch);
 -- Match save_tool_grant's grant-before-intent lock order. A grant revision
 -- cannot race the snapshot and be mistaken for a failed service check.
 PERFORM 1 FROM markos_control.tool_grants
 WHERE owner_id=t.owner_id AND domain=t.domain FOR UPDATE;
 SELECT array_agg(i.intent_id) INTO affected FROM (
  SELECT i.intent_id FROM markos_control.tool_intents i
  JOIN markos_control.tool_grants g ON g.owner_id=i.owner_id AND g.domain=i.domain
   AND g.grant_id=i.grant_id AND g.revision=i.grant_revision AND g.enabled
  WHERE i.owner_id=t.owner_id AND i.domain=t.domain
   AND i.status IN ('awaiting','approved') AND i.expires_at>clock_timestamp()
   AND markos_control.chat_authority(i.owner_id,i.domain,i.event_id)
  FOR UPDATE OF i
 ) i;
 -- Retain the reviewed fail-closed invalidation and authority sweep.
 PERFORM markos_runtime.invalidate_tool_grant_m1f(p_task,p_fence,p_executor,p_epoch);
 UPDATE markos_control.tool_intents SET cancellation_reason='test-service-check-failed'
 WHERE owner_id=t.owner_id AND domain=t.domain AND intent_id=ANY(affected)
  AND status='revoked' AND cancellation_reason='access-changed';
END $$;
REVOKE ALL ON FUNCTION markos_runtime.invalidate_tool_grant(uuid,bigint,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_runtime.invalidate_tool_grant(uuid,bigint,uuid,uuid) TO markos_runtime_client;
