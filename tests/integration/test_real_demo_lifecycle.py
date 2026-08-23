from __future__ import annotations

import os

import pytest

from trading_agent.demo_lifecycle import REAL_DEMO_CONFIRMATION


@pytest.mark.integration
def test_real_demo_lifecycle_requires_manual_external_command() -> None:
    assert os.environ.get("OKX_REAL_DEMO_CONFIRMATION") != REAL_DEMO_CONFIRMATION, (
        "Do not run real Demo submission from pytest. Use the audited verifier command after explicit approval."
    )
