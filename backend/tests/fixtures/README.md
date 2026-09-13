# Expected contract fixtures

`*_goal.json` contains a CompileGoalRequest with a normalized source event.
`*_compiled_graph.json` is a hand-authored expected graph for that request.
These are offline contract examples, not recorded LLM responses.

Each graph proposes a Calendar checkpoint and contains the complete dependency
chain and evidence contracts. Future submission/follow-up actions are deferred
until their prerequisite evidence is available. Dates, reference IDs, addresses,
and actors are synthetic demo data. Assumed checkpoint times are explicit.

The renewal graph intentionally leaves policy identity retrieval to a future
step; a live compiler/verifier must not infer the insured person from these
minimal fixtures. See A3/A4 in the implementation plan.
