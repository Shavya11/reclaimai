"""`cli trace` - the brief's worked example, as a command.

One record, detection to money, read from storage. The important assertion
is the last one: the command must say, per event, whether Razorpay sent it or
the simulator did, and must never call a modelled outcome confirmed.
"""

import json

import pytest

from reclaim import clock
from reclaim.cli import main
from reclaim.db import SessionLocal, WebhookEventRow, reset_database
from reclaim.runner import run_batch
from reclaim.measure.trace import trace


@pytest.fixture(scope="module")
def _batch():
    reset_database()
    clock.reset()
    run_batch(dry_run=True)
    with SessionLocal() as session:
        recovered = (session.query(WebhookEventRow)
                     .filter(WebhookEventRow.record_id.isnot(None))
                     .first())
    return recovered.record_id


def test_an_unknown_record_is_none(_batch):
    assert trace("REC_NOPE") is None


def test_the_trail_covers_every_stage_the_pipeline_claims(_batch):
    data = trace(_batch)
    stages = {row["stage"] for row in data["trail"]}
    assert {"DIAGNOSE", "DECIDE", "GUARDRAIL", "EXECUTE", "OUTCOME"} <= stages


def test_the_walk_is_joined_not_guessed(_batch):
    """The webhook's ref must be the intervention's ref must be the ref in the
    EXECUTE audit row. Three tables, one key."""
    data = trace(_batch)
    refs_exec = {r["payload"].get("razorpay_ref") for r in data["trail"]
                 if r["stage"] == "EXECUTE"}
    refs_int = {i["razorpay_ref"] for i in data["interventions"]}
    refs_hook = {w["razorpay_ref"] for w in data["webhooks"]}
    assert refs_hook and refs_hook <= refs_int <= refs_exec | {None}


def test_a_modelled_outcome_is_never_called_confirmed(_batch):
    data = trace(_batch)
    assert all(w["simulated"] for w in data["webhooks"]), "DRY_RUN produced a real event?"
    assert data["money"]["confirmed_by_razorpay"] is False
    assert data["money"]["real_events"] == 0
    assert data["money"]["modelled_events"] == len(data["webhooks"])


def test_recovered_money_matches_the_interventions(_batch):
    data = trace(_batch)
    from_interventions = sum(i["recovered_amount"] for i in data["interventions"]
                             if i["result"] == "RECOVERED")
    assert data["money"]["recovered_paise"] == from_interventions


def test_cli_trace_json_is_the_same_walk(_batch, capsys):
    assert main(["trace", _batch, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["record"]["id"] == _batch
    assert out == trace(_batch)


def test_cli_trace_on_a_missing_record_fails_loudly(_batch, capsys):
    assert main(["trace", "REC_NOPE"]) == 1
    assert "no record" in capsys.readouterr().out
