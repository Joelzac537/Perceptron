import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def refund_data():
    return json.loads((FIXTURES / "refund_compiled_graph.json").read_text(encoding="utf-8"))
