"""Phase 8 — NOT IMPLEMENTED YET. Spec: plan/evaluation.md §6.

Every 60 s: evaluate enabled alert_rules over their trailing window; fire
alert_events on breach (no re-fire while unresolved); resolve when back
under; notify via the alert-channel registry (plan/plugins.md) — built-in:
projects.alert_webhook_url. Prometheus on port 9103."""
