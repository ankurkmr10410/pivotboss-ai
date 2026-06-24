# Deployment Checklist

## Before any deployment
- Confirm the system is in a safe and tested state.
- Ensure risk controls and kill switches exist.
- Confirm audit logging is enabled.
- Review credentials and environment configuration.
- Test in a sandbox or paper-trading mode first.

## For live-trading readiness
- Separate paper-trading and live-trading code paths.
- Require explicit human approval for live execution.
- Add monitoring, alerting, and rollback plans.
- Review compliance and financial-risk controls.
