# MarkOS infrastructure boundary

Reviewed infrastructure definitions and operational documentation will live
here, including local development dependencies, broker ACLs, database migrations
and backup/restore procedures as approved milestones require.

Accepted [[ADR-002 Cloud-first Deployment]] places always-on services on a
DigitalOcean VM using Docker Compose, with Supabase PostgreSQL for durable state.
The MacBook is development/admin only. See [[MarkOS Cloud Deployment Architecture]]
for persistent schedules/missed-job recovery, restart/health policies, backups,
independent monitoring and recovery. Source-object provider and detailed sizing,
networking, plan/cost and operational targets still require review. This increment
contains no provisioning or deployment commands and authorizes no deployment.
