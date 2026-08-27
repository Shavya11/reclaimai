# DEPLOY — Render (API) + Vercel (dashboard)

> **Status:** GitHub and Vercel are done. Render is not.
>
> | Piece | State |
> |---|---|
> | GitHub | `github.com/Shavya11/reclaimai` (private), 10 commits |
> | Vercel | **live** — https://reclaimai-eight.vercel.app |
> |  | Use Vercel's own auto-assigned domain. A `.vercel.app` subdomain added by hand through the API gets served a "Vercel Security Checkpoint" that a real browser does not get past. |
> | Render | **not deployed** — needs a browser signup, see step 1 |
>
> The dashboard loads and says so plainly — "Dashboard loaded. No API
> connected." — because it has no API to call until Render is up. Step 1 and
> step 2.5 below are all that is left.

**Read this first: the point of deploying is the webhook, not the hosting.**

Every other part of this system has been exercised. The webhook receiver, the
HMAC verification and the attribution chain carry 23 tests, but no delivery has
ever arrived *from Razorpay*, because there is no public URL on the build
machine for Razorpay to reach. A deployment gives you one. That is the whole
return on this hour.

**Do not demo from the deployed URL.** Render's free instances spin down after
~15 minutes idle and cold-start in 30–50 seconds, which is a bad thing to
discover in front of judges. Drive the demo locally with `reclaim serve`; let the
deployment receive webhooks and act as a link you can put in the submission.

```
   Vercel  ──────────►  Render  ◄──────────  Razorpay
  dashboard   /api/*      API        webhook   test mode
                        + webhook
```

---

## 0. Push to GitHub  ✅ DONE

`github.com/Shavya11/reclaimai`, private, 10 commits. `.env` is gitignored and
was verified absent from the push before it ran.

---

## 1. Render — the API  (you, 15 min)

1. render.com → **New** → **Blueprint** → pick the repo. It reads
   [render.yaml](render.yaml) and creates a service called `reclaimai-api`.
2. It will prompt for the values marked `sync: false`. Set:

   | Variable | Value |
   |---|---|
   | `RAZORPAY_KEY_ID` | your `rzp_test_…` |
   | `RAZORPAY_KEY_SECRET` | the matching secret |
   | `RAZORPAY_WEBHOOK_SECRET` | **invent a string now and write it down** — you type the same one into Razorpay in step 3 |
   | `ANTHROPIC_API_KEY` | leave blank if you don't have one; layer 2 just stays off |
   | `CORS_ORIGINS` | leave blank for now, you get the Vercel URL in step 2 |

3. Deploy. When it's live, check it:

```bash
curl https://<your-service>.onrender.com/api/health
curl https://<your-service>.onrender.com/api/scoreboard
```

`/api/health` should report `ok: true`. `/api/scoreboard` should show 120
records — `SEED_ON_BOOT` generates a batch because the disk starts empty.

**The free instance has no persistent disk.** The database is at `/tmp` and is
wiped on every restart; a new batch is generated on the next boot. That is fine
for a demo. To keep it, move to a paid plan and uncomment the `disk:` block in
render.yaml — nothing else changes.

---

## 2. Vercel — the dashboard  ✅ DONE (except step 2.5)

1. vercel.com → **Add New** → **Project** → same repo.
2. **Set Root Directory to `ui`.** This is the one setting people miss, and
   without it Vercel tries to build the Python at the repo root and fails.
   Framework preset should auto-detect as Next.js.
3. Add an environment variable:

   ```
   NEXT_PUBLIC_API_BASE = https://<your-service>.onrender.com
   ```

   No trailing slash.

   **Next.js inlines `NEXT_PUBLIC_*` at build time, not at runtime.** Changing
   it later in the Vercel dashboard does nothing until you redeploy. This is the
   single most common way a deployed dashboard ends up quietly calling
   `localhost`.

4. Deploy. Copy the resulting URL.

5. **Back in Render**, set `CORS_ORIGINS` to that Vercel URL (no trailing slash)
   and let it redeploy. Until you do, the dashboard loads but every API call is
   blocked by the browser and you get a red banner. Preview deploys
   (`*.vercel.app` per-commit URLs) are already matched by a regex in
   `api/app.py` and do not need listing.

Open the Vercel URL. You should see the scoreboard, populated.

---

## 3. Razorpay — point the webhook at Render  (you, 10 min)

Razorpay Dashboard → **Settings → Webhooks → Add New Webhook**.

| Field | Value |
|---|---|
| Webhook URL | `https://<your-service>.onrender.com/webhooks/razorpay` |
| Secret | the exact `RAZORPAY_WEBHOOK_SECRET` from step 1 |
| Active events | `payment.captured`, `payment.failed`, `payment_link.paid`, `order.paid`, `subscription.charged` |

**The secret must match character for character.** A mismatch does not produce a
helpful error — every delivery fails verification and is discarded, which is the
correct behaviour and an infuriating way to spend an afternoon. If deliveries
are being refused, `GET /api/webhooks` shows what arrived, and the audit log
records each rejection.

---

## 4. Prove it  (you, 5 min)

The point of all of the above.

```bash
# Turn off DRY_RUN on Render so the API mints real test-mode links, then:
curl -X POST "https://<your-service>.onrender.com/api/run-batch"
```

Open the dashboard, find any record in `AT_RISK` with a `SEND_LINK`
intervention, and pay its link in a browser with a Razorpay test card.

Then:

```bash
curl https://<your-service>.onrender.com/api/webhooks | head -40
```

You want to see a `payment_link.paid` event with `"outcome": "PROCESSED"` and
`"simulated": false` — the `false` is the whole point, that one came from
Razorpay rather than from our own replay. The record's state flips to
`RECOVERED` and the scoreboard's recovered total moves.

**Then set `DRY_RUN` back to `true`.** A public URL that can mint payment links
is worth being deliberate about, even in test mode.

---

## Troubleshooting

**Dashboard shows a red banner.** `CORS_ORIGINS` on Render does not match the
Vercel URL exactly — check scheme, host, and no trailing slash. Confirm with:

```bash
curl -si -H "Origin: https://<your-vercel-url>" \
  https://<your-service>.onrender.com/api/health | grep -i access-control
```

No `access-control-allow-origin` header means the browser will block it.

**Dashboard calls localhost.** `NEXT_PUBLIC_API_BASE` was set after the build.
Redeploy on Vercel.

**First request takes 40 seconds.** Free instance cold start. Expected. Do not
demo from here.

**Scoreboard is empty after a restart.** Ephemeral disk. `SEED_ON_BOOT` rebuilds
on the next boot, or `POST /api/run-batch`.

**Webhook deliveries show as refused.** Secret mismatch — see step 3.

**Everything is broken and the demo is in ten minutes.** Run it locally. The
whole demo works from one terminal: `cli demo --extra-ticks 3` then
`reclaim serve`. See [DEMO.md](DEMO.md). The deployment is proof, not the
product.
