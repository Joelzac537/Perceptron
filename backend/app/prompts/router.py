"""Versioned prompt resource for the Event Router's semantic stage.

Mirrors the loader in this package's `__init__`: the prose lives in `router.md` beside the
other prompts, and this module supplies the version string and the reader. Nothing here
interpolates runtime values — the event, the candidate loops, `reference_time` and
`timezone` all travel in the JSON envelope that `ReasoningBoundary` builds.
"""

from importlib.resources import files

ROUTER_VERSION = "router-v1"


def router_prompt() -> str:
    return files(__package__).joinpath("router.md").read_text(encoding="utf-8")
