"""Who may change the rules, disarm the agent, or reset the batch.

Every admin write used to answer anyone on the internet. The rule now: a
configured ADMIN_TOKEN is required; with none configured, loopback only. And
the kill switch is persisted, because a panic button that un-presses itself
on restart is not one.

TestClient reports its host as "testclient", which auth.py treats as loopback -
so the unconfigured case here is the laptop case. The remote case is asserted
by faking the client host.
"""

import pytest

from reclaim import killswitch
from reclaim.api import auth
from reclaim.config import settings
from reclaim.db import reset_database

ADMIN_WRITES = [
    ("POST", "/api/kill-switch?enabled=false"),
    ("POST", "/api/clock/reset"),
    ("POST", "/api/admin/guardrail/frequency_cap"),
    ("POST", "/api/admin/policy/FAILED_PAYMENT/UNKNOWN"),
    ("POST", "/api/admin/reset"),
]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_database()
    monkeypatch.setattr(settings, "admin_token", "")
    monkeypatch.setattr(settings, "autopilot_enabled", True)
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from reclaim.api.app import app

    return TestClient(app)


def _remote(client):
    """The same client, seen from an address that is not the server's own."""
    from fastapi.testclient import TestClient

    from reclaim.api.app import app

    return TestClient(app, client=("203.0.113.9", 51000))


# --- no token configured -------------------------------------------------

def test_with_no_token_configured_loopback_may_write(client):
    r = client.post("/api/kill-switch?enabled=false")
    assert r.status_code == 200
    assert r.json()["autopilot_enabled"] is False


@pytest.mark.parametrize("method,path", ADMIN_WRITES)
def test_with_no_token_configured_a_remote_caller_is_refused(client, method, path):
    r = _remote(client).request(method, path, json={})
    assert r.status_code == 403, f"{path} answered a stranger: {r.status_code}"
    assert "locked" in r.json()["detail"]


def test_a_forgotten_token_on_a_deployment_fails_closed(client):
    """The case that matters. Render's proxy is never loopback, so a deployment
    that never set ADMIN_TOKEN is locked, not open."""
    r = _remote(client).post("/api/admin/reset")
    assert r.status_code == 403


# --- token configured -----------------------------------------------------

def test_with_a_token_configured_the_header_is_required_even_from_loopback(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "s3cret")
    r = client.post("/api/kill-switch?enabled=false")
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == auth.HEADER


def test_the_right_token_is_accepted_from_anywhere(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "s3cret")
    r = _remote(client).post("/api/kill-switch?enabled=false",
                             headers={auth.HEADER: "s3cret"})
    assert r.status_code == 200


def test_the_wrong_token_is_refused(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "s3cret")
    r = client.post("/api/kill-switch?enabled=false", headers={auth.HEADER: "nope"})
    assert r.status_code == 401


# --- what stays public -------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/health", "/api/scoreboard", "/api/admin/rules", "/api/admin/changes",
])
def test_reads_stay_public_for_a_stranger(client, path):
    assert _remote(client).get(path).status_code == 200


def test_the_sandbox_stays_public_for_a_stranger(client):
    """A visitor handing the agent a record is the feature. Preview writes
    nothing, so it needs no gate."""
    r = _remote(client).get("/api/sandbox/presets")
    assert r.status_code == 200


def test_health_tells_the_caller_whether_it_is_locked(client, monkeypatch):
    assert client.get("/api/health").json()["admin_locked"] is False
    assert _remote(client).get("/api/health").json()["admin_locked"] is True
    monkeypatch.setattr(settings, "admin_token", "s3cret")
    assert client.get("/api/health").json()["admin_locked"] is True


# --- the kill switch survives a restart ----------------------------------

def test_the_kill_switch_is_persisted_not_in_memory(client):
    client.post("/api/kill-switch?enabled=false")
    # "Restart": whatever the process thinks, the database is the answer.
    settings.autopilot_enabled = True
    assert killswitch.enabled() is False
    assert client.get("/api/health").json()["autopilot_enabled"] is False


def test_the_gate_reads_the_persisted_kill_switch():
    """Flipped through the API, honoured by the guardrail on the next tick -
    without anybody passing the value through by hand."""
    from reclaim.brain.gate import run as gate_run
    from reclaim.enums import ActionType, Channel, LeakType, RecordState, RootCause
    from reclaim.models import AtRiskRecord, Diagnosis, ProposedAction
    from reclaim.timeutil import now

    killswitch.set_enabled(False)
    settings.autopilot_enabled = True  # the process disagrees; the database wins

    record = AtRiskRecord(
        id="REC_KS", leak_type=LeakType.FAILED_PAYMENT, amount=10_000,
        counterparty_id="CUST_KS", source_ref="pay_ks", detected_at=now(),
        raw_signals={}, state=RecordState.AT_RISK)
    diagnosis = Diagnosis(root_cause=RootCause.INSUFFICIENT_FUNDS, confidence=1.0,
                          reasoning="t", recoverable=True, source="deterministic")
    action = ProposedAction(
        record_id="REC_KS", action_type=ActionType.NOTIFY, channel=Channel.EMAIL,
        scheduled_for=now(), attempt_number=1,
        policy_ref="FAILED_PAYMENT.INSUFFICIENT_FUNDS", rationale="t", amount=10_000)

    report = gate_run([record], {"REC_KS": diagnosis}, [action], {})
    outcome = report.outcomes[0]
    assert outcome.result.allowed is False
    assert any(v.guardrail == "kill_switch" for v in outcome.result.violations)


def test_health_names_the_model_that_will_actually_answer(client, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "g")
    assert client.get("/api/health").json()["model"] == settings.gemini_model
    monkeypatch.setattr(settings, "gemini_api_key", "")
    assert client.get("/api/health").json()["model"] is None
