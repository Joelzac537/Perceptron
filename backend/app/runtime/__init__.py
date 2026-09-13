"""The write side: applies what the read side planned.

`app/events/` answers "what should happen about this event" and performs nothing —
a boundary its own test suite enforces structurally, by asserting that no module
under `app/events/` imports `app.db`. This package is the other half: it owns the
transaction, the persistence and the agent calls that actually change a graph.
"""
