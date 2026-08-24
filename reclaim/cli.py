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
        for reason, d in payload["codes"].items():
            print(f"  {GREEN}{reason}{OFF}  code={d['code']} "
                  f"source={d['source']} step={d['step']}")
            print(f"        {DIM}{d['description']}{OFF}")
        print()
        print(f"written to {FIXTURE}")
        return 0 if payload["codes"] else 1

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
        ("harvest", cmd_harvest, "harvest real Razorpay error codes into fixtures"),
        ("config", cmd_config, "show effective settings"),
    ]:
        sp = subs.add_parser(name, help=helptext)
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.set_defaults(func=fn)

    subs.choices["harvest"].add_argument(
        "--collect", action="store_true",
        help="fetch failed payments and write the fixture (default: mint links)")

    seed_p = subs.choices["seed"]
    seed_p.add_argument("--seed", type=int, default=settings.seed)
    seed_p.add_argument("--count", type=int, default=120)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
