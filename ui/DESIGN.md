# ReclaimAI — dashboard design system

The reference was a light bento dashboard (cream canvas, deep-green accent, big
rounded cards). This is that language applied to a payments operations console.
The old dark console survives as the dark theme, so the demo still reads on a
projector.

## Tokens

All colour lives in `src/app/globals.css` as CSS custom properties, exposed to
Tailwind through `@theme inline`. **Nothing hard-codes a hex value.** The token
*names* are the ones the screens already used before the redesign, which is why
the recovery queue and the audit trail inherited the new look without being
rewritten.

| Token | Light | Dark | Means |
|---|---|---|---|
| `canvas` | `#f1f0eb` | `#0b0d10` | page ground |
| `panel` / `panel2` | `#ffffff` / `#f7f6f2` | `#14181d` / `#191e25` | cards, insets |
| `line` / `linestrong` | `#e4e3dd` / `#d3d2ca` | `#262d36` / `#35404c` | borders, hatching |
| `ink` / `muted` / `dim` | `#10151c` / `#4a5568` / `#5e6a78` | `#e8edf3` / `#a4b0be` / `#8b97a6` | text ladder |
| `green` / `greendeep` / `greenwash` | `#14764f` / `#0d4a33` / `#eaf5ef` | `#34d399` / `#0c3d2b` / `#10241c` | recovered, accent fills |
| `amber` / `amberwash` | `#a05a06` / `#fdf2e2` | `#fbbf24` / `#2a2113` | still in flight |
| `red` / `redwash` | `#b4231f` / `#fdeceb` | `#f87171` / `#2a1516` | refused, written off |

Both themes are defined three times on purpose: bare `:root` (light),
`@media (prefers-color-scheme: dark)` guarded with `:root:not([data-theme="light"])`,
and `:root[data-theme="dark"]`. That is what makes the sidebar toggle win in
both directions without a flash — a blocking script in `layout.tsx` stamps the
stored choice before first paint.

## Semantic rule

Colour is never decorative, and never the only channel.

- **solid green** — money or records recovered
- **amber** — still in flight
- **hatched** (45° `repeating-linear-gradient`, `--line-strong`) — deliberately
  not pursued, or refused
- **red** — a guardrail said no

Every chart pairs its colour with hatching and a text label, so the encoding
survives greyscale and colour blindness.

## Type

`Plus Jakarta Sans` for everything, `JetBrains Mono` for the `.num` class —
every rupee figure, count, ID and timestamp, so columns line up. Both loaded via
`next/font/google`, self-hosted into the static export at build time; no font
CDN is contacted at runtime.

Scale is deliberately small and tight: 30–34px hero stats, 26–30px page title,
15px card titles, 13px body, 11px notes, 10px axis ticks. Nothing between.

## Layout

A 12-column grid (`md:grid-cols-12`) with one `gap-4` gutter everywhere.

```
[ at risk* ][ recovered ][ open ][ written off ]     4 × col-span-3
[ money split band                             ]     col-span-12
[ root-cause columns  8 ][ resolution gauge  4 ]
[ guardrails 4 ][ naive vs ours 5 ][ breaches 3 ]
[ where naive wins       8 ][ cost of recovery 4 ]
```

Breakpoints: single column below `md`, two-up at `md`, the full bento at `lg`.
The rail is `hidden lg:flex`; below that the nav becomes a scrollable pill row
rather than a drawer — four destinations do not justify a focus trap.

## Charts

Hand-built SVG and CSS in `src/components/charts.tsx`. No charting library: the
hatched fills are custom anyway, a static export should not ship a runtime to
draw nine bars, and every chart needs a text alternative that stays in sync.

| Component | Encodes |
|---|---|
| `MoneySplit` | one bar, fully partitioned — recovered / open / written off |
| `CauseChart` | column height = record volume, solid fill = share recovered |
| `ResolutionGauge` | 260° arc, three segments, record recovery rate in the middle |
| `RailBar` | horizontal magnitude (guardrail holds, gap accounting) |
| `PairedBars` | naive vs ReclaimAI, hatched vs solid |

Every one renders a `<details> View as table` from the **same array** it draws
from, so the accessible fallback cannot drift from the picture.

## Accessibility

Verified with a scripted audit over all 273 rendered text nodes, in both themes:

- **0 below WCAG AA** (4.5:1 body, 3:1 large) — oklab alpha compositing accounted for
- no horizontal overflow at 375 / 900 / 1440px
- visible `:focus-visible` ring on every interactive element
- `aria-current="page"` on nav, `aria-pressed` on filters, `aria-label` on
  icon-only buttons, `scope` on every `th`
- table rows are keyboard-activatable (Enter / Space)
- `prefers-reduced-motion` disables all animation
- `cursor-pointer` on everything clickable; hover is colour-only, so nothing
  reflows under the pointer
- icons are SVG on a shared 24×24 grid — no emoji
