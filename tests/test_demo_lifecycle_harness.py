from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from data.models import AccountSnapshot, Instrument, MarketSnapshot
from execution.base_backend import BackendStatus
from execution.errors import PreSubmitRejectedError, SubmissionUncertainError
from trading_agent.demo_lifecycle import REAL_DEMO_CONFIRMATION, DemoLifecycleVerifier, submission_gate


@dataclass(frozen=True)
class StubPlan:
    plan_id: str = "plan-lifecycle"
    decision: str = "BUY"
    risk_approved: bool = True

    def as_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "decision": self.decision,
            "risk_approved": self.risk_approved,
        }


class StubBackend:
    def __init__(self, *, demo: bool = True, attached_tp_sl: bool = True) -> None:
        self.demo = demo
        self.attached_tp_sl = attached_tp_sl
        self.place_calls = 0

    def status(self) -> BackendStatus:
        return BackendStatus("stub", "CONNECTED", True, self.demo)

    def capabilities(self) -> dict:
        return {"attached_tp_sl": self.attached_tp_sl}


class StubOrchestrator:
    def __init__(
        self,
        market: MarketSnapshot,
        account: AccountSnapshot,
        backend: StubBackend,
        submit_error: Exception | None = None,
    ) -> None:
        self.config = SimpleNamespace(environment="demo")
        self.adapter = SimpleNamespace(backend=backend)
        self.market_data = SimpleNamespace(get_snapshot=lambda _symbol: market)
        self.account_data = SimpleNamespace(get_snapshot=lambda _symbol: account)
        self.order_manager = SimpleNamespace(
            reconcile_plan=lambda plan_id: {"plan_id": plan_id, "state": "OPEN"},
            reconcile_managed_positions=lambda: [],
        )
        self.approvals: list[tuple[str, str, float | None]] = []
        self.submit_error = submit_error

    def get_health(self) -> dict:
        return {"trading_eligibility": {"blocking_reasons": []}}

    def analyze(self, _symbol: str) -> StubPlan:
        return StubPlan()

    def approve_plan(
        self,
        plan_id: str,
        confirmation: str = "",
        *,
        position_size_cap: float | None = None,
    ) -> dict:
        self.approvals.append((plan_id, confirmation, position_size_cap))
        if confirmation:
            self.adapter.backend.place_calls += 1
            if self.submit_error is not None:
                raise self.submit_error
            return {"status": "SUBMITTED", "plan_id": plan_id}
        return {
            "status": "READY_FOR_EXACT_APPROVAL",
            "current_executable_price": 100.0,
            "position_size": 0.001,
        }


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"submit_flag": False}, "REAL_DEMO_SUBMIT_FLAG_REQUIRED"),
        ({"confirmation": "yes"}, "EXACT_REAL_DEMO_CONFIRMATION_REQUIRED"),
        ({"environment": "live"}, "DEMO_ENVIRONMENT_REQUIRED"),
        ({"backend_demo": False}, "VERIFIED_DEMO_BACKEND_REQUIRED"),
        ({"attached_tp_sl": False}, "TP_SL_BACKEND_NOT_SUPPORTED"),
        ({"precheck_ready": False}, "PRECHECK_NOT_READY"),
    ],
)
def test_real_demo_submission_gate_fails_closed(overrides, reason) -> None:
    values = {
        "submit_flag": True,
        "confirmation": REAL_DEMO_CONFIRMATION,
        "environment": "demo",
        "backend_available": True,
        "backend_demo": True,
        "attached_tp_sl": True,
        "precheck_ready": True,
    }
    values.update(overrides)
    assert submission_gate(**values) == (False, reason)


def test_real_demo_submission_requires_every_gate() -> None:
    assert submission_gate(
        submit_flag=True,
        confirmation=REAL_DEMO_CONFIRMATION,
        environment="demo",
        backend_available=True,
        backend_demo=True,
        attached_tp_sl=True,
        precheck_ready=True,
    ) == (True, "PASS")


def test_lifecycle_precheck_is_read_only_and_exports_sanitized_preview(
    tmp_path, market, account,
) -> None:
    backend = StubBackend()
    verifier = DemoLifecycleVerifier(StubOrchestrator(market, account, backend), tmp_path)

    result = verifier.precheck("BTC-USDT")

    assert result["mode"] == "PRECHECK_READ_ONLY"
    assert result["place_order_called"] is False
    assert backend.place_calls == 0
    assert result["timeline"][0]["state"] == "PLAN_CREATED"
    assert result["timeline"][1]["state"] == "PREVIEW_REVALIDATED"
    assert (tmp_path / result["audit_export"].split("/")[-1]).exists()


def test_lifecycle_minimum_size_is_lot_aligned_and_only_caps_risk_size(
    tmp_path, market, account,
) -> None:
    instrument = Instrument("BTC-USDT", "BTC", "USDT", 0.00005, 0.00002, 0.1)
    adjusted_market = MarketSnapshot(
        market.symbol,
        market.timestamp_ms,
        market.price,
        market.bid,
        market.ask,
        market.volume_24h,
        market.candles,
        instrument,
    )
    backend = StubBackend()
    orchestrator = StubOrchestrator(adjusted_market, account, backend)
    verifier = DemoLifecycleVerifier(orchestrator, tmp_path)
    precheck = verifier.precheck("BTC-USDT")

    assert precheck["minimum_executable_quantity"] == pytest.approx(0.00006)
    result = verifier.submit(
        precheck,
        submit_flag=True,
        confirmation=REAL_DEMO_CONFIRMATION,
    )

    assert result["mode"] == "REAL_DEMO_SUBMISSION_EXPLICITLY_APPROVED"
    assert orchestrator.approvals[-1] == (
        "plan-lifecycle",
        "CONFIRM DEMO ORDER",
        pytest.approx(0.00006),
    )
    assert backend.place_calls == 1
    assert result["status"] == "SUBMIT_SUCCEEDED"
    assert result["timeline"][-1]["state"] == "SUBMIT_SUCCEEDED"


def test_lifecycle_explicit_rejection_exports_sanitized_terminal_audit(
    tmp_path, market, account,
) -> None:
    backend = StubBackend()
    orchestrator = StubOrchestrator(
        market,
        account,
        backend,
        PreSubmitRejectedError(
            "EXCHANGE_EXPLICIT_REJECTION",
            diagnostics={
                "tool": "spot_place_order",
                "type": "OkxApiError",
                "code": "401",
                "message": "apiKey=test-never-write-this",
                "endpoint": "POST /api/v5/trade/order",
                "trace_id": "trace-test",
                "server_version": "1.4.4",
                "signature": "test-never-write-signature",
            },
        ),
    )
    verifier = DemoLifecycleVerifier(orchestrator, tmp_path)
    precheck = verifier.precheck("BTC-USDT")

    with pytest.raises(PreSubmitRejectedError) as raised:
        verifier.submit(precheck, submit_flag=True, confirmation=REAL_DEMO_CONFIRMATION)

    audit = raised.value.failure_audit
    persisted = json.loads((tmp_path / audit["audit_export"].split("/")[-1]).read_text())
    assert persisted["status"] == "SUBMIT_REJECTED"
    assert persisted["reason"] == "EXCHANGE_EXPLICIT_REJECTION"
    assert persisted["diagnostics"]["code"] == "401"
    assert persisted["diagnostics"]["endpoint"] == "POST /api/v5/trade/order"
    assert persisted["diagnostics"]["trace_id"] == "trace-test"
    assert [event["state"] for event in persisted["timeline"]][-2:] == [
        "SUBMIT_ATTEMPTED", "SUBMIT_REJECTED",
    ]
    serialized = json.dumps(persisted)
    assert "test-never-write-this" not in serialized
    assert "test-never-write-signature" not in serialized
    assert backend.place_calls == 1


def test_lifecycle_uncertain_submission_exports_submission_unknown_audit(
    tmp_path, market, account,
) -> None:
    backend = StubBackend()
    orchestrator = StubOrchestrator(
        market,
        account,
        backend,
        SubmissionUncertainError("MCP_TIMEOUT"),
    )
    verifier = DemoLifecycleVerifier(orchestrator, tmp_path)
    precheck = verifier.precheck("BTC-USDT")

    with pytest.raises(SubmissionUncertainError) as raised:
        verifier.submit(precheck, submit_flag=True, confirmation=REAL_DEMO_CONFIRMATION)

    persisted = json.loads((tmp_path / raised.value.failure_audit["audit_export"].split("/")[-1]).read_text())
    assert persisted["status"] == "SUBMISSION_UNKNOWN"
    assert persisted["timeline"][-1]["state"] == "SUBMISSION_UNKNOWN"
    assert backend.place_calls == 1


def test_lifecycle_resume_only_reconciles_and_never_resubmits(tmp_path, market, account) -> None:
    backend = StubBackend()
    verifier = DemoLifecycleVerifier(StubOrchestrator(market, account, backend), tmp_path)

    result = verifier.resume("plan-lifecycle")

    assert result["mode"] == "RESUME_RECONCILIATION_READ_ONLY"
    assert result["place_order_called"] is False
    assert backend.place_calls == 0
