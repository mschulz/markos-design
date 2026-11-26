-- M2-B: Gmail change alerts, watch renewal and the backstop poll, as due times on the mailbox.
-- Database time is the only clock. A lease with a fence keeps one worker at a time; a crashed
-- worker's lease expires and the next claim retries the same work.
ALTER TABLE markos_runtime.mailboxes
 ADD COLUMN poll_due_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 ADD COLUMN renew_due_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 ADD COLUMN watch_expires_at timestamptz,
 ADD COLUMN alert_seq bigint NOT NULL DEFAULT 0 CHECK(alert_seq>=0),
 ADD COLUMN fence bigint NOT NULL DEFAULT 0 CHECK(fence>=0),
 ADD COLUMN lease_until timestamptz;

-- An alert only says "something changed": it makes the poll due now. Repeats change nothing more.
CREATE FUNCTION markos_runtime.note_mail_alert(p_history numeric) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;b markos_runtime.mailboxes;BEGIN
 p:=markos_runtime.mail_identity();
 IF p_history IS NULL OR p_history<=0 THEN RAISE EXCEPTION 'invalid mail alert' USING ERRCODE='22023';END IF;
 SELECT * INTO b FROM markos_runtime.mailboxes WHERE owner_id=p.owner_id AND domain=p.domain AND connected FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'no connected mailbox' USING ERRCODE='P0002';END IF;
 IF p_history<=coalesce(b.history_id,0) THEN RETURN false;END IF;
 UPDATE markos_runtime.mailboxes SET alert_seq=alert_seq+1,poll_due_at=least(poll_due_at,clock_timestamp())
 WHERE owner_id=p.owner_id AND domain=p.domain;
 RETURN true;
END $$;
-- Returns NULL when nothing is due or another worker holds the lease.
CREATE FUNCTION markos_runtime.claim_mail_work(p_seconds integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;b markos_runtime.mailboxes;tick timestamptz:=clock_timestamp();renew boolean;poll boolean;BEGIN
 p:=markos_runtime.mail_identity();
 IF p_seconds IS NULL OR p_seconds NOT BETWEEN 10 AND 300 THEN RAISE EXCEPTION 'invalid mail lease' USING ERRCODE='22023';END IF;
 SELECT * INTO b FROM markos_runtime.mailboxes WHERE owner_id=p.owner_id AND domain=p.domain AND connected FOR UPDATE;
 IF NOT FOUND OR b.lease_until>tick THEN RETURN NULL;END IF;
 renew:=b.renew_due_at<=tick OR b.watch_expires_at IS NULL OR b.watch_expires_at<=tick+interval '1 day';
 poll:=b.poll_due_at<=tick;
 IF NOT (renew OR poll) THEN RETURN NULL;END IF;
 UPDATE markos_runtime.mailboxes SET fence=fence+1,lease_until=tick+make_interval(secs=>p_seconds)
 WHERE owner_id=p.owner_id AND domain=p.domain RETURNING * INTO b;
 RETURN jsonb_build_object('fence',b.fence,'renew',renew,'sync',poll,'alert_seq',b.alert_seq,'labels',to_jsonb(b.labels));
END $$;
-- Records finished work under the claim's fence. A worker whose lease was taken over records nothing.
CREATE FUNCTION markos_runtime.finish_mail_work(p_fence bigint,p_alert_seq bigint,p_synced boolean,p_watch_expires timestamptz)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,markos_runtime AS $$
DECLARE p markos_runtime.service_identities;b markos_runtime.mailboxes;tick timestamptz:=clock_timestamp();BEGIN
 p:=markos_runtime.mail_identity();
 IF p_synced IS NULL OR (p_watch_expires IS NOT NULL AND p_watch_expires<=tick) THEN
  RAISE EXCEPTION 'invalid mail work' USING ERRCODE='22023';END IF;
 SELECT * INTO b FROM markos_runtime.mailboxes WHERE owner_id=p.owner_id AND domain=p.domain FOR UPDATE;
 IF NOT FOUND OR b.fence IS DISTINCT FROM p_fence OR b.lease_until IS NULL THEN RETURN false;END IF;
 UPDATE markos_runtime.mailboxes SET lease_until=NULL,
  poll_due_at=CASE WHEN NOT p_synced THEN poll_due_at WHEN alert_seq>p_alert_seq THEN tick ELSE tick+interval '15 minutes' END,
  watch_expires_at=coalesce(p_watch_expires,watch_expires_at),
  renew_due_at=CASE WHEN p_watch_expires IS NULL THEN renew_due_at ELSE least(tick+interval '1 day',p_watch_expires-interval '1 day') END
 WHERE owner_id=p.owner_id AND domain=p.domain;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION markos_runtime.note_mail_alert(numeric),markos_runtime.claim_mail_work(integer),
 markos_runtime.finish_mail_work(bigint,bigint,boolean,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION markos_runtime.note_mail_alert(numeric),markos_runtime.claim_mail_work(integer),
 markos_runtime.finish_mail_work(bigint,bigint,boolean,timestamptz) TO markos_runtime_client;
