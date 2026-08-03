# UI Architecture — ALTANA Dashboard

## The one rule

**`ui/index.html` is a 31-line thin loader. It must never become a monolithic file.**

The actual dashboard is the Claude Design JSX build. The correct build is:

```
ui/
  index.html          ← 31-line loader ONLY (loads React + JSX files)
  styles.css          ← ALTANA design system (CSS variables, components)
  api.js              ← Live API client (window.API.*, window.useApi)
  data.js             ← DEMO data — fallbacks only, never primary source
  app.jsx             ← App shell + routing
  shell.jsx           ← Sidebar + Topbar + WorkspaceSwitcher
  icons.jsx           ← Icon library
  charts.jsx          ← Chart components (Donut, Spark, Area, Bar)
  tweaks-panel.jsx    ← Dev density/theme tweaks panel
  page-dashboard.jsx  ← Dashboard (portfolio KPIs, chart, review status)
  page-flow.jsx       ← Sources, Tax Center, Review Queue
  page-data.jsx       ← Prices, Portfolio, Charts
  page-system.jsx     ← Audit, Diagnostics, Alerts, Settings
```

`ALTANA_extracted/` mirrors `ui/` and is kept in sync. When you change a file in `ui/`, copy it to `ALTANA_extracted/` too.

---

## What the old monolithic UI is

`index.html.bak` (now deleted) was a 3000+ line all-in-one HTML+JS file that predates the Claude Design build. It is **not the design we use**. If you ever see `ui/index.html` grow past ~50 lines, something has gone wrong.

If git history is ever used to "restore" the UI, the correct restore target is any commit where `ui/index.html` is the 31-line loader (e.g. `f16d431` or later, post `3454355`).

---

## Real API vs DEMO data

`data.js` defines `window.DEMO.*`. This exists for charts that need a shape (sparklines, portfolio area curve). It is **never** a primary data source.

| Component | Source |
|-----------|--------|
| Dashboard KPIs (total value, gains, tax owed) | `window.API.getPortfolio`, `window.API.getSidecar` |
| Dashboard activity feed | `window.API.getJobs` |
| Dashboard health panel | `window.API.getDiagnostics` |
| Dashboard allocation donut | `window.API.getPortfolio` |
| Sources wallets / CSVs / dedup | `window.API.getWorkspace`, `window.API.getDedupSources` |
| Tax Center job history | `window.API.getJobs` |
| Tax Center **Run Job** | `window.API.runWorkspace` → polls `window.API.getJob` |
| Prices health | `window.API.getDiagnostics` |
| Prices **Collect** button | `window.API.collectPrices(year)` → `POST /prices/collect` |
| Portfolio table | `window.API.getPortfolio` |
| Review Queue | `window.API.getReviewSummary` |
| Diagnostics | `window.API.getDiagnostics` |
| Alerts | `window.API.getAlerts` |
| Settings wallets | `window.API.getWorkspace` |
| Workspace switcher | `window.API.getWorkspaces` / `createWorkspace` |

**Sparklines and portfolio time-series chart shape** remain synthetic (`window.DEMO.sparkData`, `window.DEMO.timeSeries`). These are decorative — they scale to real values but use a synthetic curve. This is acceptable until a historical portfolio endpoint exists.

---

## ALTANA branding

| Element | Value |
|---------|-------|
| Product name | **ALTANA** (all caps in sidebar wordmark; title case in prose) |
| Logo | Ledger icon — two horizontal bars, SVG, rendered inside `.brand-mark` div |
| localStorage key | `ALTANA_WORKSPACE` |
| `data-screen-label` | `ALTANA · {route}` |
| Browser `<title>` | `ALTANA` |

Do **not** use: "TAXNOR", "Taxnor", "taxnor", "Taxspine Orchestrator".

---

## Workspace scoping

All workspace-scoped API calls use `_wsParam()` (in `api.js`) which appends `?workspace_id=X` when a named workspace is active (stored in localStorage `ALTANA_WORKSPACE`). The default workspace has no ID; calls are made without the param.

The `WorkspaceSwitcher` component lives in `shell.jsx` and calls `window.API.getWorkspaces()` / `createWorkspace()`.

---

## Supported chains (CHAINS in page-flow.jsx)

Only include chains the tax engine **actually** supports. Do not include aspirational "Coming soon" entries.

| Chain | Status | Notes |
|-------|--------|-------|
| XRPL | Live sync | Full on-chain sync via `blockchain-reader` |
| BTC, ETH, HBAR, DAG, CSPR, FLR, OSMO | Via CSV | Generic-events CSV import only |

---

## Known remaining synthetic data

- Sparkline charts — `window.DEMO.sparkData` — decorative only
- Portfolio area chart shape — `window.DEMO.timeSeries` — shape is synthetic, scaled to real total value
- Candlestick chart in Charts page — entirely synthetic, illustrative only

These are the only remaining DEMO usages. Everything else is live API.
