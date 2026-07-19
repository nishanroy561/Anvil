"""Plugin registries — Phase 2/5/8 build these out. Spec: plan/plugins.md.

Three registries (graders, alert channels, pricing resolvers) populated at
startup: built-ins first, then external packages via
importlib.metadata.entry_points(group="anvil.graders" | "anvil.alert_channels"
| "anvil.pricing"). First registration wins; losers rejected with a loud log.
Cloud mode (DEPLOYMENT_MODE=cloud) loads built-ins only (ADR-0012).
Every plugin call is wrapped: try/except + timeout (graders 30 s,
channels 10 s, pricing 1 s)."""
