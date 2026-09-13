"""Versioned trusted prompt resources, included in the backend package."""

from importlib.resources import files

BOUNDARY_VERSION = "boundary-v1"


def boundary_prompt() -> str:
    return files(__package__).joinpath("boundary.md").read_text(encoding="utf-8")
