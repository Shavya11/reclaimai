"""Command line entry point.

Every command takes --json so an automated reviewer can assert on structured
output instead of parsing a table.
"""

import argparse
import json
import sys
from collections import Counter

from . import console
from .config import settings
from .db import init_db
from .detectors import REGISTRY, detect_all
from .money import format_inr, format_inr_short
from .repository import save_batch
from .synthetic import generate
from .verify import FAIL, PASS, PENDING, run_all

GREEN, RED, YELLOW, DIM, BOLD, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)


def cmd_seed(args) -> int:
    init_db()
    batch = generate(seed=args.seed, n=args.count)
    save_batch(batch.records, batch.customers)
    if args.json:
        print(json.dumps({"seed": args.seed, "records": len(batch.records),
                          "customers": len(batch.customers),
                          "total_at_risk_paise": batch.total_at_risk}))
        return 0
    print(f"seeded {len(batch.records)} records across {len(batch.customers)} "
          f"customers (seed={args.seed})")
    print(f"total at risk: {format_inr(batch.total_at_risk)}")
    return 0


def cmd_detect(args) -> int:
    init_db()
    records = detect_all(REGISTRY)
    total = sum(r.amount for r in records)
    by_type = Counter(r.leak_type.value for r in records)

    if args.json:
        print(json.dumps({
            "records": len(records), "total_at_risk_paise": total,
            "by_leak_type": {k: {"count": v,
                                 "amount_paise": sum(r.amount for r in records
                                                     if r.leak_type.value == k)}
                             for k, v in by_type.items()},
        }, indent=2))
        return 0

    if not records:
        print("no records — run `seed` first")
        return 1

    print(f"{BOLD}{len(records)} at-risk records, {format_inr(total)} at risk"
          f"{OFF}  ({format_inr_short(total)})\n")
    for name, count in by_type.most_common():
        amt = sum(r.amount for r in records if r.leak_type.value == name)
        print(f"  {name:<18} {count:>4}   {format_inr(amt):>14}")
    print(f"\n{DIM}detectors: {', '.join(d.name for d in REGISTRY)}{OFF}")
    return 0


def cmd_verify(args) -> int:
    checks = run_all()
    if args.json:
        print(json.dumps([{"name": c.name, "status": c.status, "detail": c.detail}
                          for c in checks], indent=2))
        return 0 if all(c.ok for c in checks) else 1

    colour = {PASS: GREEN, FAIL: RED, PENDING: YELLOW}
    mark = {PASS: "PASS", FAIL: "FAIL", PENDING: "TODO"}
    print(f"\n{BOLD}ReclaimAI — structural self-audit{OFF}\n")
    for c in checks:
        print(f"  {colour[c.status]}{mark[c.status]}{OFF}  {c.name}")
        print(f"        {DIM}{c.detail}{OFF}")
    passed = sum(c.status == PASS for c in checks)
    failed = sum(c.status == FAIL for c in checks)
    pending = sum(c.status == PENDING for c in checks)
    print(f"\n  {passed} passed, {failed} failed, {pending} pending\n")
    return 0 if failed == 0 else 1


def cmd_harvest(args) -> int:
    from .harvest import FIXTURE, collect, create

    if args.collect:
        payload = collect()
        if args.json:
            print(json.dumps(payload, indent=2))
            return 0
        print(f"{payload['failed_payments']} failed payments of "
              f"{payload['total_payments']} total")
        print()
        if not payload["codes"]:
            print(f"  {YELLOW}nothing harvested — no payment has been attempted "
                  f"yet.{OFF}")
            print(f"  {DIM}fixture left untouched. link status:{OFF}")
            print()
            for l in payload.get("links", []):
                flag = f"{GREEN}attempted{OFF}" if l["attempted"] else f"{YELLOW}untouched{OFF}"
                print(f"     {l['scenario']:<20} {l['status']:<10} {flag}  {l['url']}")
            return 1
        for reason, d in payload["codes"].items():
            print(f"  {GREEN}{reason}{OFF}  code={d['code']} "
                  f"source={d['source']} step={d['step']}")
            print(f"        {DIM}{d['description']}{OFF}")
        print()
        print(f"written to {FIXTURE}")
        return 0

    links = create()
    if args.json:
        print(json.dumps(links, indent=2))
        return 0
    print()
    print(f"{BOLD}Pay each link in a browser, then run: "
          f"reclaim harvest --collect{OFF}")
    print()
    for link in links:
        print(f"  {BOLD}{link['scenario']}{OFF}")
        print(f"     {link['url']}")
        print(f"     {DIM}{link['how']}{OFF}")
        print()
    return 0


def _diagnoser(args):
    """None when the key is absent or --no-llm is passed. Both are real code
    paths: the batch must complete either way."""
    if getattr(args, "no_llm", False):
        return None
    from .brain.diagnosis.llm_diagnoser import LLMDiagnoser

    llm = LLMDiagnoser()
    return llm if llm.available else None


def cmd_diagnose(args) -> int:
    from .brain.diagnosis.accuracy import cohort_counterfactual, score
    from .brain.diagnosis.engine import diagnose_batch

    batch = generate(seed=args.seed)
    llm = _diagnoser(args)
    diagnoses, signals = diagnose_batch(batch.records, batch.traffic, llm=llm)
    report = score(batch.records, diagnoses, batch.truth)
    counter = cohort_counterfactual(batch.records, signals, batch.truth)

    if args.json:
        print(json.dumps({"accuracy": report.as_dict(),
                          "cohort_counterfactual": counter}, indent=2))
        return 0

    print()
    print(f"{BOLD}DIAGNOSIS ACCURACY{OFF}  "
          f"{DIM}(n={report.total}, ground truth known by construction){OFF}")
    print()
    order = ["deterministic", "cohort", "llm", "fallback"]
    for name in sorted(report.layers, key=lambda n: order.index(n)
                       if n in order else 99):
        s = report.layers[name]
        tint = GREEN if s.accuracy >= 0.9 else (YELLOW if s.accuracy > 0 else DIM)
        print(f"  {name:<16} {s.total:>4} records   "
              f"{tint}{s.accuracy:>6.1%}{OFF} correct")
    print()
    print(f"  {BOLD}overall{OFF}          {report.total:>4} records   "
          f"{report.accuracy:>6.1%} correct")

    if report.confusions:
        print()
        print(f"  {DIM}unresolved / confused:{OFF}")
        for (truth, pred), n in report.confusions.most_common(5):
            print(f"     {truth:<22} diagnosed as {pred:<18} {n:>3}")

    print()
    print(f"{BOLD}COHORT SIGNAL — what it prevented{OFF}")
    print(f"  {counter['records_flagged_as_outage']} records on "
          f"{counter['issuer']} carried a generic 'declined by the bank' error.")
    print(f"  Without the cohort signal they read as customer-side failures and "
          f"each earns a message.")
    print(f"  {GREEN}needless customer contacts prevented: "
          f"{counter['needless_contacts_prevented']}{OFF}")
    if llm is not None:
        print()
        print(f"  {DIM}LLM: {llm.calls} calls, {llm.cache_hits} cache hits "
              f"({llm.model}){OFF}")
    elif not getattr(args, "no_llm", False):
        print()
        print(f"  {DIM}no ANTHROPIC_API_KEY — layer 2 skipped, batch still "
              f"completed{OFF}")
    print()
    return 0


def cmd_plan(args) -> int:
    """Day 2 checkpoint: a proposed action for every record, with the policy row
    that decided it. Nothing is executed."""
    from .brain.diagnosis.engine import diagnose_batch
    from .brain.policy import decide

    batch = generate(seed=args.seed)
    llm = _diagnoser(args)
    diagnoses, _ = diagnose_batch(batch.records, batch.traffic, llm=llm)
    actions = [decide(r, diagnoses[r.id]) for r in batch.records]

    from .brain import gate

    report = gate.run(batch.records, diagnoses, actions,
                      {c.id: c for c in batch.customers})

    by_action = Counter(a.action_type.value for a in actions)
    by_policy = Counter(a.policy_ref for a in actions)
    contacts = sum(1 for a in actions if a.action_type.contacts_customer)

    if args.json:
        print(json.dumps({
            "records": len(actions),
            "by_action": dict(by_action),
            "by_policy_ref": dict(by_policy),
            "customer_contacts_proposed": contacts,
            "guardrails": report.as_dict(),
            "actions": [
                {"record_id": a.record_id, "action": a.action_type.value,
                 "channel": a.channel.value if a.channel else None,
                 "policy_ref": a.policy_ref, "attempt": a.attempt_number,
                 "scheduled_for": a.scheduled_for.isoformat(),
                 "idempotency_key": a.idempotency_key,
                 "amount_paise": a.amount}
                for a in actions
            ],
        }, indent=2))
        return 0

    print()
    print(f"{BOLD}PROPOSED ACTIONS{OFF}  {DIM}(nothing executed){OFF}")
    print()
    for name, n in by_action.most_common():
        print(f"  {name:<16} {n:>4}")
    print()
    print(f"  {BOLD}customer contacts proposed: {contacts}{OFF}")
    print()
    print(f"{BOLD}BY POLICY ROW{OFF}")
    for ref, n in by_policy.most_common(8):
        print(f"  {ref:<44} {n:>4}")
    print()
    print(f"{DIM}sample — every action carries the row that decided it:{OFF}")
    for a in actions[:3]:
        print(f"  {a.record_id}  {a.action_type.value:<13} {a.policy_ref:<40}")
        print(f"     {DIM}{a.scheduled_for:%Y-%m-%d %H:%M} IST   "
              f"key={a.idempotency_key}{OFF}")

    print()
    print(f"{BOLD}GUARDRAILS{OFF}")
    print(f"  The agent wanted to take {BOLD}{report.proposed}{OFF} actions.")
    print(f"  It was allowed {GREEN}{report.allowed}{OFF}.")
    print(f"  {RED}{report.blocked}{OFF} were blocked — "
          f"{report.deferred} deferred, {report.requiring_human} sent to a human.")
    print()
    for name, n in report.blocked_by.most_common():
        print(f"     {name:<20} {n:>4}")

    blocked = [o for o in report.outcomes if not o.result.allowed]
    if blocked:
        print()
        print(f"{DIM}every refusal carries its reason:{OFF}")
        seen = set()
        for o in blocked:
            v = o.result.violations[0]
            if v.guardrail in seen:
                continue
            seen.add(v.guardrail)
            print(f"  {o.action.record_id}  {RED}{v.guardrail}{OFF}")
            print(f"     {DIM}{v.reason}{OFF}")
    print()
    return 0


def cmd_run_batch(args) -> int:
    from .runner import BatchCrashed, run_batch

    crashed = None
    try:
        result = run_batch(
            seed=args.seed, llm=_diagnoser(args), crash_at=args.crash_at,
            reseed=not args.resume, dry_run=None if args.live else True,
        )
    except BatchCrashed as exc:
        crashed = str(exc)
        result = None

    if args.json:
        print(json.dumps({"crashed": crashed,
                          "result": result.as_dict() if result else None}, indent=2))
        return 1 if crashed else 0

    if crashed:
        print()
        print(f"  {RED}{crashed}{OFF}")
        print(f"  {DIM}run `reclaim run-batch --resume` to continue{OFF}")
        print()
        return 1

    print()
    print(f"{BOLD}BATCH COMPLETE{OFF}")
    print(f"  proposed          {result.proposed:>5}")
    print(f"  allowed           {GREEN}{result.allowed:>5}{OFF}")
    print(f"  blocked           {RED}{result.blocked:>5}{OFF}")
    print(f"  executed          {result.executed:>5}")
    print(f"  skipped (replay)  {result.skipped_idempotent:>5}")
    print(f"  failed            {result.failed:>5}")
    print(f"  escalated         {result.escalated:>5}")
    print(f"  messages sent     {result.messages_sent:>5}")
    if result.blocked_by:
        print()
        for name, n in result.blocked_by.most_common():
            print(f"     {name:<20} {n:>4}")
    print()
    return 0


def cmd_prove_idempotency(args) -> int:
    """Kills a batch mid-flight, resumes it, and counts the keys. The claim that
    the agent cannot double-charge is demonstrated, not asserted."""
    from sqlalchemy import func

    from .db import ExecutedActionRow, SessionLocal
    from .runner import BatchCrashed, run_batch

    _reset_database()
    print()
    print(f"{BOLD}IDEMPOTENCY UNDER CRASH{OFF}")
    print()

    try:
        run_batch(seed=args.seed, crash_at=args.crash_at, dry_run=True)
    except BatchCrashed as exc:
        print(f"  1. {YELLOW}{exc}{OFF}")

    with SessionLocal() as s:
        claimed = s.query(ExecutedActionRow).count()
    print(f"  2. keys claimed before the crash: {BOLD}{claimed}{OFF}")

    resumed = run_batch(seed=args.seed, reseed=False, dry_run=True)
    print(f"  3. resumed — guardrail #10 blocked "
          f"{BOLD}{resumed.blocked_by['idempotency']}{OFF} replays before they "
          f"reached Razorpay")

    with SessionLocal() as s:
        total = s.query(ExecutedActionRow).count()
        distinct = s.query(
            func.count(func.distinct(ExecutedActionRow.idempotency_key))).scalar()
        dupes = (s.query(ExecutedActionRow.idempotency_key)
                 .group_by(ExecutedActionRow.idempotency_key)
                 .having(func.count("*") > 1).count())

    print()
    print(f"     executed_actions rows   {total:>5}")
    print(f"     distinct keys           {distinct:>5}")
    tint = GREEN if dupes == 0 else RED
    print(f"     {BOLD}duplicate keys{OFF}          {tint}{dupes:>5}{OFF}")
    print()
    if dupes == 0:
        print(f"  {GREEN}No action executed twice. The UNIQUE constraint on "
              f"idempotency_key makes it impossible.{OFF}")
    print()

    if args.json:
        print(json.dumps({"claimed_before_crash": claimed, "rows": total,
                          "distinct_keys": distinct, "duplicates": dupes}))
    return 0 if dupes == 0 else 1


def _reset_database() -> None:
    """Drops the file rather than deleting rows — audit_log rejects DELETE by
    design, so a clean slate means a new database."""
    from pathlib import Path

    from .db import engine, init_db

    engine.dispose()
    db = Path(str(settings.database_url).replace("sqlite:///", ""))
    if db.exists():
        db.unlink()
    init_db()


def cmd_reset(args) -> int:
    _reset_database()
    print("database reset")
    return 0


def cmd_config(args) -> int:
    payload = {
        "dry_run": settings.dry_run,
        "autopilot_enabled": settings.autopilot_enabled,
        "razorpay_credentials": settings.has_razorpay,
        "anthropic_credentials": settings.has_anthropic,
        "model": settings.anthropic_model,
        "database": settings.database_url,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    for k, v in payload.items():
        print(f"  {k:<24} {v}")
    if settings.dry_run:
        print(f"\n{DIM}DRY_RUN is on — no live Razorpay calls will be made.{OFF}")
    return 0


def main(argv: list[str] | None = None) -> int:
    console.init()
    p = argparse.ArgumentParser(prog="reclaim", description="AI revenue recovery agent")
    subs = p.add_subparsers(dest="command", required=True)

    for name, fn, helptext in [
        ("seed", cmd_seed, "generate the synthetic at-risk batch"),
        ("detect", cmd_detect, "run all detectors over the at-risk store"),
        ("verify", cmd_verify, "structural self-audit of the build"),
        ("diagnose", cmd_diagnose, "diagnose the batch and score it against ground truth"),
        ("plan", cmd_plan, "propose an action for every record (nothing executed)"),
        ("run-batch", cmd_run_batch, "run the full pipeline end to end"),
        ("prove-idempotency", cmd_prove_idempotency, "crash a batch, resume it, count the keys"),
        ("reset", cmd_reset, "drop the database and start clean"),
        ("harvest", cmd_harvest, "harvest real Razorpay error codes into fixtures"),
        ("config", cmd_config, "show effective settings"),
    ]:
        sp = subs.add_parser(name, help=helptext)
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.set_defaults(func=fn)

    subs.choices["harvest"].add_argument(
        "--collect", action="store_true",
        help="fetch failed payments and write the fixture (default: mint links)")

    rb = subs.choices["run-batch"]
    rb.add_argument("--crash-at", type=int, default=None,
                    help="simulate a crash after N actions")
    rb.add_argument("--resume", action="store_true",
                    help="continue without regenerating the batch")
    rb.add_argument("--live", action="store_true",
                    help="make real Razorpay test-mode calls")
    pi = subs.choices["prove-idempotency"]
    pi.add_argument("--crash-at", type=int, default=30)

    for name in ("diagnose", "plan", "run-batch", "prove-idempotency"):
        subs.choices[name].add_argument("--seed", type=int, default=settings.seed)
        subs.choices[name].add_argument("--no-llm", action="store_true",
                                        help="skip layer 2 and prove the batch still completes")

    seed_p = subs.choices["seed"]
    seed_p.add_argument("--seed", type=int, default=settings.seed)
    seed_p.add_argument("--count", type=int, default=120)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
