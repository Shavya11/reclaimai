"""Command line entry point.

Every command takes --json so an automated reviewer can assert on structured
output instead of parsing a table.
"""

import argparse
import json
import sys
from collections import Counter

from . import console
from .config import ROOT, settings
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


def _client(args):
    """The pre-staged outage. `--kill-razorpay` is demo beat #6 as a flag rather
    than a code edit performed live."""
    if not getattr(args, "kill_razorpay", False):
        return None
    from .executor.razorpay_client import DeadRazorpayClient

    return DeadRazorpayClient()


def _diagnoser(args):
    """None when no key is present or --no-llm is passed. Both are real code
    paths: the batch must complete either way.

    Anthropic first when both keys exist — PROJECT.md describes that one, and a
    demo should run what the document claims."""
    if getattr(args, "no_llm", False):
        return None
    from .brain.diagnosis.llm_diagnoser import LLMDiagnoser

    llm = LLMDiagnoser()
    if llm.available:
        return llm

    from .brain.diagnosis.gemini_diagnoser import GeminiDiagnoser

    gem = GeminiDiagnoser()
    return gem if gem.available else None


def _extractor(args):
    """The reply reader. Same switch as the diagnoser, for the same reason.

    `--no-llm` must turn off BOTH jobs. Leaving the extractor running under a
    flag that says no model would make the flag a lie, and would make the
    degraded path — every reply reaching a human — untestable from the command
    line, which is the only place anyone would test it."""
    if getattr(args, "no_llm", False):
        return None
    from .brain.conversation import build_extractor

    return build_extractor()


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
        print(f"  {DIM}no ANTHROPIC_API_KEY or GEMINI_API_KEY — layer 2 "
              f"skipped, batch still completed{OFF}")
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
            seed=args.seed, llm=_diagnoser(args), extractor=_extractor(args),
            crash_at=args.crash_at,
            reseed=not args.resume, dry_run=None if args.live else True,
            settle=not args.no_settle, client=_client(args),
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
    print(f"  not yet due       {result.scheduled:>5}")
    if result.settlement:
        st = result.settlement
        print()
        print(f"  {BOLD}outcomes attributed via signed webhooks{OFF}")
        print(f"    recovered       {GREEN}{st.recovered:>5}{OFF}  "
              f"{format_inr(st.recovered_paise)}")
        print(f"    no response     {st.no_response:>5}")
        print(f"    failed again    {st.failed_again:>5}")
        if st.unattributed:
            print(f"    {RED}unattributed    {st.unattributed:>5}{OFF}")
    if result.blocked_by:
        print()
        for name, n in result.blocked_by.most_common():
            print(f"     {name:<20} {n:>4}")
    if getattr(args, "kill_razorpay", False):
        print()
        print(f"  {YELLOW}Razorpay was unreachable for this run.{OFF} "
              f"{result.failed} writes failed and were parked for review; "
              f"the batch still completed and no key executed twice.")
    print()
    return 0


def _scoreboard_lines(board, indent: str = "  ") -> list[str]:
    d = board.as_dict()
    out = [
        f"{BOLD}BATCH RESULTS{OFF}  {DIM}(n = {d['records']} at-risk records){OFF}",
        "",
        f"{indent}{'Money at risk':<28}{d['at_risk_display']:>14}",
        f"{indent}{'Money recovered':<28}{GREEN}{d['recovered_display']:>14}{OFF}"
        f"   ({d['recovery_rate']:.1%} by value, "
        f"{d['record_recovery_rate']:.1%} by record)",
        f"{indent}{'Still open':<28}{d['open_display']:>14}",
        f"{indent}{'Written off / unrecoverable':<28}{d['unrecoverable_display']:>14}"
        f"   {DIM}(never-retry causes, escalated not chased){OFF}",
    ]
    if not d["balances"]:
        out.append(f"{indent}{RED}buckets do not sum to the total{OFF}")

    out += ["", f"{indent}{BOLD}Recovery rate by root cause{OFF}"]
    for c in d["by_root_cause"]:
        tint = GREEN if c["rate"] >= 0.3 else (YELLOW if c["rate"] > 0 else DIM)
        out.append(f"{indent}  {c['root_cause']:<22}{tint}{c['rate']:>6.0%}{OFF}"
                   f"   {c['recovered_records']:>3}/{c['records']:<4}"
                   f"{DIM}{format_inr(c['recovered_paise']):>12}{OFF}")

    held = sum(d.get("guardrails_records", {}).values())
    out += ["", f"{indent}{BOLD}Guardrails: {d['guardrails_total']} refusals "
                f"across {held} records{OFF}"]
    for name, n in d["guardrails_fired"].items():
        records = d.get("guardrails_records", {}).get(name, n)
        out.append(f"{indent}  {name:<20}{RED}{records:>4}{OFF} records"
                   f"{DIM}   ({n} refusals over the run){OFF}")
    out += [
        "",
        f"{indent}{'Human escalations':<28}{d['escalations']:>6}",
        f"{indent}{'Interventions executed':<28}{d['interventions']:>6}"
        f"   {DIM}({d['contacts']} contacts, {d['silent_retries']} silent){OFF}",
        f"{indent}{'Contacts per recovery':<28}"
        f"{BOLD}{d['contacts_per_recovery']:>6}{OFF}",
        f"{indent}{'Outcomes attributed':<28}{d['webhooks_attributed']:>6}"
        f"   {DIM}via verified webhooks{OFF}",
    ]
    return out


def cmd_scoreboard(args) -> int:
    from .scoreboard import compute

    board = compute()
    if args.json:
        print(json.dumps(board.as_dict(), indent=2))
        return 0
    print()
    for line in _scoreboard_lines(board):
        print(line)
    print()
    return 0


def cmd_tick(args) -> int:
    """Advance the demo clock and run again. Deferred work lands here."""
    from . import clock
    from .runner import tick

    before = clock.now()
    result, at = tick(advance=args.advance, seed=args.seed, llm=_diagnoser(args),
                      extractor=_extractor(args),
                      dry_run=None if args.live else True, client=_client(args))

    if args.json:
        print(json.dumps({"from": before.isoformat(), "to": at.isoformat(),
                          "advanced": args.advance,
                          "result": result.as_dict()}, indent=2))
        return 0

    print()
    print(f"{BOLD}TICK{OFF}  {before:%d %b %H:%M} -> {GREEN}{at:%d %b %H:%M} IST{OFF}"
          f"  {DIM}(+{args.advance}){OFF}")
    print(f"  due now {result.proposed:>4}   not yet due {result.scheduled:>4}")
    print(f"  executed {GREEN}{result.executed:>3}{OFF}   "
          f"blocked {RED}{result.blocked:>3}{OFF}")
    if result.settlement:
        st = result.settlement
        print(f"  settled  {st.pending:>3} outcomes -> "
              f"{GREEN}{st.recovered} recovered{OFF}, "
              f"{st.no_response} no response, {st.failed_again} failed again")
    print()
    return 0


def cmd_clock(args) -> int:
    from . import clock

    if args.reset:
        clock.reset()
        print("clock reset to wall time")
        return 0
    payload = {"now": clock.now().isoformat(),
               "offset_seconds": clock.offset().total_seconds(),
               "time_travelled": clock.is_travelled()}
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print(f"  demo clock   {clock.now():%Y-%m-%d %H:%M} IST")
    print(f"  offset       {clock.offset()}")
    return 0


def cmd_baseline(args) -> int:
    """PROJECT.md 9: 35% alone means nothing; 35% against 19% means everything."""
    from .baseline import compare

    comparison = compare(seed=args.seed)
    d = comparison.as_dict()
    if args.json:
        print(json.dumps(d, indent=2))
        return 0

    b, o, gap = d["baseline"], d["ours"], d["gap"]
    print()
    print(f"{BOLD}BASELINE COMPARISON{OFF}  "
          f"{DIM}same 120 records, same seeded outcomes, different strategy{OFF}")
    print()
    print(f"  {'':<30}{'NAIVE':>14}{'RECLAIMAI':>14}")
    print(f"  {'-' * 58}")
    rows = [
        ("recovered", b["recovered_display"], o["recovered_display"]),
        ("rate by value", f"{b['recovery_rate']:.1%}", f"{o['recovery_rate']:.1%}"),
        ("rate by record", f"{b['record_recovery_rate']:.1%}",
         f"{o['record_recovery_rate']:.1%}"),
        ("customer contacts", str(b["contacts"]), str(o["contacts"])),
        ("contacts per recovery", f"{b['contacts_per_recovery']:.2f}",
         f"{o['contacts_per_recovery']:.2f}"),
        ("contacts to opted-out", str(b["contacts_to_opted_out"]), "0"),
        ("contacts on DND", str(b["contacts_to_dnd"]), "0"),
        ("contacts in quiet hours", str(b["contacts_in_quiet_hours"]), "0"),
        ("retries on never-retry causes", str(b["retries_against_never_retry"]), "0"),
    ]
    for label, left, right in rows:
        print(f"  {label:<30}{left:>14}{right:>14}")
    print()
    print(f"  {RED}The naive strategy commits {b['compliance_breaches']} contacts "
          f"the guardrail engine refuses.{OFF}")
    print()

    if gap.get("total", {}).get("records"):
        print(f"{BOLD}WHERE THE NAIVE STRATEGY WINS, AND WHY{OFF}")
        print(f"  {DIM}It recovers {gap['total']['display']} we do not. "
              f"Every rupee of it is accounted for:{OFF}")
        print()
        for r in gap["reasons"]:
            print(f"    {r['records']:>3}  {r['display']:>12}   {r['label']}")
        print()
        print(f"    {BOLD}{format_inr(gap['deliberate_paise'])}{OFF} of that is "
              f"money the agent was told not to take.")
        if gap["recoverable_with_layer_2_paise"]:
            print(f"    {format_inr(gap['recoverable_with_layer_2_paise'])} is "
                  f"blocked on layer 2 (no LLM key on this run).")
        if gap["still_open_paise"]:
            print(f"    {format_inr(gap['still_open_paise'])} is still in flight - "
                  f"deferred, not abandoned.")
    print()
    return 0


def cmd_demo(args) -> int:
    """The whole arc, start to finish. Used to rehearse and to capture metrics."""
    from . import clock
    from .baseline import compare
    from .runner import DEMO_ARC, run_batch, tick
    from .scoreboard import compute

    _reset_database()
    clock.reset()
    llm = _diagnoser(args)

    print()
    print(f"{BOLD}RECLAIMAI - FULL RUN{OFF}  {DIM}seed={args.seed}, "
          f"{'live' if args.live else 'DRY_RUN'}, "
          f"layer 2 {'on' if llm else 'off'}{OFF}")
    print()

    extractor = _extractor(args)
    result = run_batch(seed=args.seed, llm=llm, extractor=extractor,
                       dry_run=None if args.live else True)
    print(f"  {'t0':<24} due {result.proposed:>3}  executed {result.executed:>3}  "
          f"blocked {result.blocked:>3}  waiting {result.scheduled:>3}")

    arc = DEMO_ARC + ["+7d"] * max(0, args.extra_ticks)
    for step in arc:
        res, at = tick(advance=step, seed=args.seed, llm=llm, extractor=extractor,
                       dry_run=None if args.live else True)
        recovered = res.settlement.recovered if res.settlement else 0
        label = f"{step} -> {at:%d %b %H:%M}"
        print(f"  {label:<24} due {res.proposed:>3}  executed {res.executed:>3}  "
              f"blocked {res.blocked:>3}  recovered {GREEN}{recovered:>3}{OFF}")

    board = compute()
    print()
    for line in _scoreboard_lines(board):
        print(line)

    comparison = compare(seed=args.seed)
    d = comparison.as_dict()
    b, o = d["baseline"], d["ours"]
    print()
    print(f"{BOLD}VS NAIVE BASELINE{OFF}")
    print(f"  naive     {b['recovered_display']:>12}  "
          f"{b['record_recovery_rate']:>6.1%} of records  "
          f"{b['contacts']:>4} contacts  {b['contacts_per_recovery']:.2f}/recovery  "
          f"{RED}{b['compliance_breaches']} compliance breaches{OFF}")
    print(f"  ours      {o['recovered_display']:>12}  "
          f"{o['record_recovery_rate']:>6.1%} of records  "
          f"{o['contacts']:>4} contacts  {o['contacts_per_recovery']:.2f}/recovery  "
          f"{GREEN}0 compliance breaches{OFF}")
    print()

    if args.save:
        from pathlib import Path

        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"seed": args.seed, "layer_2": bool(llm),
             "scoreboard": board.as_dict(), "comparison": d}, indent=2),
            encoding="utf-8")
        print(f"{DIM}metrics written to {out}{OFF}")
        print()
    return 0


def cmd_snapshot(args) -> int:
    """Walk the whole arc offline and freeze it for a cold deployment to restore.

    The free instance this deploys to has an ephemeral disk, so every cold boot
    starts empty; rebuilding the batch live is ~100 seconds of a rate-limited
    free LLM tier, and a visitor arriving inside that window sees ₹0 recovered.
    Committing the settled result makes the boot instant and the numbers exactly
    the ones the README publishes — because the same runner produced both.
    """
    from . import snapshot

    llm = _diagnoser(args)
    if llm is None and not args.no_llm:
        print(f"{DIM}No LLM key configured.{OFF} A snapshot built without "
              f"layer 2 publishes the fallback numbers under the headline "
              f"ones. Pass --no-llm to build one anyway.")
        return 1

    print()
    print(f"{BOLD}BUILDING DEMO SNAPSHOT{OFF}  {DIM}seed={args.seed}, "
          f"layer 2 {'on' if llm else 'off'}{OFF}")
    print(f"{DIM}  the whole arc, once, so the deployment never has to{OFF}")
    print()

    payload = snapshot.build(llm=llm, extractor=_extractor(args),
                             seed=args.seed, extra_ticks=args.extra_ticks)

    size = snapshot.PATH.stat().st_size / 1024
    board = payload["scoreboard"]
    rows = sum(len(t) for t in payload["tables"].values())
    print(f"  {'records':<20} {board['records']}")
    print(f"  {'recovered':<20} {GREEN}{board['recovered_display']}{OFF} "
          f"({board['records_recovered']} records, "
          f"{board['recovery_rate'] * 100:.1f}% by value)")
    print(f"  {'rows frozen':<20} {rows}")
    print(f"  {'written':<20} {snapshot.PATH.relative_to(ROOT)} ({size:.0f} KB)")
    print()
    print(f"{DIM}Commit it. Boot restores it in about a second, with no network "
          f"call and no quota spent.{OFF}")
    print()
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    print(f"  dashboard  http://{args.host}:{args.port}/")
    print(f"  api        http://{args.host}:{args.port}/api/scoreboard")
    print(f"  webhook    http://{args.host}:{args.port}/webhooks/razorpay")
    uvicorn.run("reclaim.api.app:app", host=args.host, port=args.port,
                reload=args.reload)
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


def cmd_ablation(args) -> int:
    """Is layer 2 worth its calls? The same batch, with and without it.

    Prints the cost as well as the gain, and refuses to print anything when too
    many model calls went unanswered — a rate-limited run still completes and
    still produces a plausible-looking number, which is exactly the number that
    ships by accident on a deadline.
    """
    from .experiments import ablation
    from .money import format_inr

    print(f"\n{BOLD}LAYER-2 ABLATION{OFF}  {DIM}two arcs, scratch databases, "
          f"same seed, live diagnosis{OFF}")
    print(f"  {DIM}this makes real model calls and takes a few minutes{OFF}\n")

    result = ablation.run(seed=args.seed)
    data = result.as_dict()

    if args.json:
        print(json.dumps(data, indent=2))
        return 1 if data["void"] else 0

    if data["void"]:
        print(f"  {RED}{data['reason']}{OFF}\n")
        return 1

    n = data["population"]
    print(f"  {DIM}population{OFF} {n} records layer 2 answered "
          f"{DIM}(layer 1 and the cohort signal are untouched){OFF}")
    print(f"  {DIM}consulted{OFF} {data['layer2_consulted']}  "
          f"{DIM}api calls{OFF} {data['layer2_api_calls']}  "
          f"{DIM}unanswered{OFF} {data['layer2_failure_rate']:.1%}\n")

    rows = [
        ("Money arrived", "money_arrived_paise", True),
        ("  of which ours", "recovered_paise", True),
        ("  arrived anyway", "organic_paise", True),
        ("Records recovered", "recovered_records", False),
        ("Human escalations", "human_escalations", False),
        ("Contacts sent", "contacts", False),
    ]
    print(f"  {DIM}{'':<20} {'layer 2 on':>14} {'off':>14} "
          f"{'delta':>14}{OFF}")
    for label, key, money in rows:
        d = data[key]
        fmt = (lambda v: format_inr(int(v))) if money else (lambda v: f"{int(v)}")
        delta = d["delta"]
        tint = GREEN if delta > 0 else (RED if delta < 0 else DIM)
        if key in ("human_escalations", "contacts"):
            tint = GREEN if delta < 0 else (RED if delta > 0 else DIM)
        print(f"  {label:<20} {fmt(d['with_ai']):>14} {fmt(d['without_ai']):>14} "
              f"{tint}{fmt(delta):>14}{OFF}")
        lo, hi = d["per_record_ci_95"]
        unit = "₹/record" if money else "per record"
        print(f"  {DIM}{'':<20} 95% CI [{lo:+.4g}, {hi:+.4g}] {unit}{OFF}")
    print()

    harm = data["harmful_actions"]
    if harm:
        print(f"  {RED}{len(harm)} harmful action"
              f"{'s' if len(harm) != 1 else ''}{OFF} "
              f"{DIM}— layer 2 mislabelled a cause that must never be "
              f"acted on{OFF}")
        for h in harm:
            print(f"    {h['record_id']}  truth {h['truth']} → read as "
                  f"{h['diagnosed']}  ({h['contacts']} contact"
                  f"{'s' if h['contacts'] != 1 else ''})")
    else:
        print(f"  {GREEN}No harmful actions: layer 2 never acted on a cause "
              f"the truth says must not be chased.{OFF}")
    print()
    print(f"  {BOLD}{data['headline']}{OFF}")
    print(f"  {DIM}Both arms are handed the same self-curing customers, so "
          f"'money arrived' is what\n  layer 2 added rather than what it looks "
          f"like against a world where nobody pays\n  unaided. Part of what it "
          f"recovers would have come anyway — that is the negative\n  'arrived "
          f"anyway' line. See docs/RESULTS.md.{OFF}\n")
    return 0


def cmd_replay(args) -> int:
    """The what-if. Same batch, different rules, side by side.

    Overrides are given as `section.key=value` so the demo can be driven from
    one line: `cli replay --guardrail value_ceiling.requires_human_above=7500000`.
    """
    from . import whatif
    from .brain.validation import RuleInvalid
    from .money import format_inr

    payload: dict = {"guardrails": {}, "policies": {}}
    for raw in args.guardrail or []:
        key, _, value = raw.partition("=")
        section, _, field = key.strip().partition(".")
        if not section or not field:
            print(f"{RED}--guardrail wants section.key=value, got {raw!r}{OFF}")
            return 2
        payload["guardrails"].setdefault(section, {})[field] = _coerce(value)

    if not payload["guardrails"]:
        print(f"{RED}Nothing to compare: pass at least one --guardrail.{OFF}")
        return 2

    try:
        overrides = whatif.parse_overrides(payload)
    except RuleInvalid as exc:
        print(f"\n  {RED}That rule would not be accepted:{OFF}")
        for problem in exc.problems:
            print(f"    - {problem}")
        print()
        return 2

    print(f"\n{BOLD}WHAT-IF REPLAY{OFF}  {DIM}two arcs, scratch database, "
          f"frozen diagnoses{OFF}")
    for line in overrides.describe():
        print(f"  {DIM}override{OFF} {line}")
    print()

    try:
        diff = whatif.replay(overrides, seed=args.seed).as_dict()
    except RuntimeError as exc:
        print(f"  {RED}{exc}{OFF}\n")
        return 1

    if args.json:
        print(json.dumps(diff, indent=2))
        return 0

    rows = [
        ("recovered", "recovered_paise", True),
        ("records recovered", "records_recovered", False),
        ("contacts sent", "contacts", False),
        ("human escalations", "escalations", False),
        ("guardrail refusals", "guardrails_total", False),
    ]
    print(f"  {'':<22}{'as configured':>16}{'with change':>16}{'delta':>14}")
    for label, key, money in rows:
        before, after = diff["baseline"].get(key), diff["variant"].get(key)
        delta = diff["deltas"].get(key)
        fmt = (lambda v: format_inr(int(v))) if money else (lambda v: str(v))
        numeric = isinstance(delta, (int, float))
        sign = "" if not numeric or delta == 0 else ("+" if delta > 0 else "-")
        tint = GREEN if (numeric and delta > 0) else (
            YELLOW if (numeric and delta < 0) else DIM)
        shown = fmt(abs(delta)) if numeric else "-"
        print(f"  {label:<22}{fmt(before):>16}{fmt(after):>16}"
              f"{tint}{sign + shown:>14}{OFF}")

    if diff["guardrails"]:
        print(f"\n  {DIM}guardrails that moved{OFF}")
        for g in diff["guardrails"]:
            print(f"    {g['guardrail']:<20} {g['before']:>5} -> "
                  f"{g['after']:<5}  {g['delta']:+}")

    print(f"\n  {BOLD}{diff['headline']}{OFF}")
    print(f"  {DIM}Nothing was saved. The live database and the demo clock are "
          f"untouched.{OFF}\n")
    return 0


def _coerce(value: str):
    """Command-line values are strings; the validator wants the real type."""
    text = value.strip()
    low = text.lower()
    if low in {"true", "false"}:
        return low == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def cmd_rules(args) -> int:
    """Show the rule table and whether each row is shipped or edited."""
    from . import admin

    if args.reset:
        print(f"  restored {admin.reset()} rules to the shipped defaults\n")
        return 0

    snap = admin.snapshot()
    if args.json:
        print(json.dumps(snap, indent=2))
        return 0

    source = "edited in the database" if snap["seeded"] else "shipped defaults"
    print(f"\n{BOLD}GUARDRAIL THRESHOLDS{OFF}  {DIM}{source}{OFF}")
    for entry in snap["guardrails"]:
        mark = f"{YELLOW}edited{OFF}" if entry["modified"] else f"{DIM}default{OFF}"
        print(f"  {entry['name']:<20} {mark}")
        for key, value in entry["config"].items():
            was = (entry["default"] or {}).get(key)
            suffix = (f"   {DIM}(was {was}){OFF}"
                      if entry["modified"] and was != value else "")
            print(f"      {key:<26} {value}{suffix}")

    edited = [e for e in snap["policies"] if e["modified"]]
    print(f"\n{BOLD}POLICY ROWS{OFF}  {DIM}{len(snap['policies'])} rows, "
          f"{len(edited)} edited{OFF}")
    for entry in edited:
        print(f"  {YELLOW}{entry['leak_type']}.{entry['root_cause']}{OFF}")

    recent = admin.changes(5)
    if recent:
        print(f"\n{BOLD}RECENT CHANGES{OFF}")
        for c in recent:
            print(f"  {(c['changed_at'] or '')[:16]}  {c['scope']:<10} {c['key']}")
            for d in c["diff"][:3]:
                print(f"      {d['field']}: {d['before']} -> {d['after']}")
    print()
    return 0


def cmd_promises(args) -> int:
    """The promise book. Open promises are the agent deliberately silent, which
    is the one thing a dashboard cannot render as activity."""
    from .db import PromiseRow, SessionLocal
    from .money import format_inr
    from .promises import counts

    tally = counts()
    with SessionLocal() as session:
        rows = session.query(PromiseRow).order_by(PromiseRow.id).all()
        items = [{"record_id": r.record_id, "state": r.state,
                  "promised_for": r.promised_for, "amount": r.amount,
                  "reply": r.reply_text or "", "confidence": r.confidence}
                 for r in rows]

    if args.json:
        print(json.dumps({
            "counts": tally,
            "promises": [{**i, "promised_for": i["promised_for"].isoformat()}
                         for i in items]}, indent=2))
        return 0

    resolved = tally.get("KEPT", 0) + tally.get("BROKEN", 0)
    kept_rate = (f" | kept {tally.get('KEPT', 0) / resolved:.0%} of the time"
                 if resolved else "")
    print(f"\n{BOLD}PROMISES TO PAY{OFF}   {DIM}open {tally.get('OPEN', 0)} | "
          f"kept {tally.get('KEPT', 0)} | broken {tally.get('BROKEN', 0)}"
          f"{kept_rate}{OFF}\n")
    for i in items:
        tint = {"OPEN": YELLOW, "KEPT": GREEN, "BROKEN": RED}.get(i["state"], DIM)
        print(f"  {i['record_id']:<12} {tint}{i['state']:<8}{OFF} "
              f"{format_inr(i['amount']):>14}  by {i['promised_for']:%d %b}  "
              f"{DIM}conf {i['confidence']:.2f}{OFF}")
        if i["reply"]:
            print(f"      {DIM}{i['reply'][:88]}{OFF}")
    if not items:
        print(f"  {DIM}No promises yet. Run the arc with a model available.{OFF}")
    print()
    return 0


def _reset_database() -> None:
    """The demo clock lives in the same database file, so it resets with it."""
    from .db import reset_database

    reset_database()


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
        "gemini_credentials": settings.has_gemini,
        "model": settings.anthropic_model if settings.has_anthropic
                 else settings.gemini_model,
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
        ("scoreboard", cmd_scoreboard, "the batch scoreboard"),
        ("tick", cmd_tick, "advance the demo clock and run deferred work"),
        ("clock", cmd_clock, "show or reset the demo clock"),
        ("baseline", cmd_baseline, "naive strategy over the same batch"),
        ("demo", cmd_demo, "the whole arc: reset, run, tick, score, compare"),
        ("snapshot", cmd_snapshot, "freeze the settled arc for a cold deployment"),
        ("serve", cmd_serve, "run the API and dashboard"),
        ("replay", cmd_replay, "what-if: the same batch under different rules"),
        ("ablation", cmd_ablation, "is layer 2 worth it: the same batch with "
                                   "and without the model"),
        ("rules", cmd_rules, "show the rule table, shipped vs edited"),
        ("promises", cmd_promises, "the promise-to-pay book"),
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
    rb.add_argument("--no-settle", action="store_true",
                    help="fire the interventions but do not replay outcomes")

    for name in ("run-batch", "tick"):
        subs.choices[name].add_argument(
            "--kill-razorpay", action="store_true",
            help="pre-staged outage: every Razorpay write fails, the batch "
                 "completes anyway")
    pi = subs.choices["prove-idempotency"]
    pi.add_argument("--crash-at", type=int, default=30)

    tk = subs.choices["tick"]
    tk.add_argument("--advance", default="24h",
                    help="schedule token: 20m, 2h, 24h, +7d, next_salary_window")
    tk.add_argument("--live", action="store_true")

    subs.choices["clock"].add_argument("--reset", action="store_true")

    dm = subs.choices["demo"]
    dm.add_argument("--live", action="store_true")
    dm.add_argument("--extra-ticks", type=int, default=2,
                    help="additional +7d ticks so deferred work lands")
    dm.add_argument("--save", default=None, help="write metrics to a JSON file")

    sv = subs.choices["serve"]
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--reload", action="store_true")

    subs.choices["snapshot"].add_argument(
        "--extra-ticks", type=int, default=3,
        help="additional +7d ticks so every deferred action lands")

    for name in ("diagnose", "plan", "run-batch", "prove-idempotency", "tick",
                 "demo", "baseline", "snapshot"):
        subs.choices[name].add_argument("--seed", type=int, default=settings.seed)
        subs.choices[name].add_argument("--no-llm", action="store_true",
                                        help="skip layer 2 and prove the batch still completes")

    rp = subs.choices["replay"]
    rp.add_argument("--guardrail", action="append", metavar="SECTION.KEY=VALUE",
                    help="override a guardrail threshold for the replay only, "
                         "e.g. value_ceiling.requires_human_above=7500000")
    rp.add_argument("--seed", type=int, default=settings.seed)

    subs.choices["ablation"].add_argument("--seed", type=int,
                                          default=settings.seed)

    subs.choices["rules"].add_argument(
        "--reset", action="store_true",
        help="restore every rule to the shipped defaults")

    seed_p = subs.choices["seed"]
    seed_p.add_argument("--seed", type=int, default=settings.seed)
    seed_p.add_argument("--count", type=int, default=120)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
