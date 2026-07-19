"""Phase 8 — NOT IMPLEMENTED YET. Spec: plan/evaluation.md §6.

Every 30 s: find monitors due per schedule_cron (croniter, UTC); Bernoulli-
sample recent unscored traces; grade via the Phase-5 grader machinery
(re-score mode); write monitor_scores. Prometheus on port 9103
(last-run staleness gauge)."""
