# ALTANA — Project Status Report
**Last updated:** 2026-05-11  
**Owner:** the owner (KR, owner@example.com)  
**Purpose:** Keep Claude from drifting. This is the single source of truth for intent, state, and open work.

---

## The Product

**Name: ALTANA.** Not TAXNOR. Not Taxnor. Not taxnor. ALTANA everywhere.

**Brand:** Scandinavian-minimal, glassmorphic. Single forest-green accent `oklch(0.7 0.12 155)`. Dark-first. Ledger logo (two horizontal bars). Inter + JetBrains Mono.

**Ledger logo SVG (use this exactly in JSX):**
```jsx
<svg viewBox="0 0 100 100" width="28" height="28" style={{color: 'var(--accent)'}}>
  <rect x="22" y="36" width="40" height="10" rx="2" fill="currentColor"/>
  <rect x="22" y="54" width="56" height="10" rx="2" fill="currentColor"/>
</svg>
```

**User identity — hard rules:**
- Name: the owner
- Initials: KR (avatar shows KR, not MK, not HJ)
- Never: Henrik, h.jansen, Nordic Growth, MK

---

## Two Repos — Do Not Confuse Them

| Repo | Path | Purpose |
|---|---|---|
| Tax spine (engine) | `%USERPROFILE%\Documents\Project F` | FIFO, classification, valuation, RF-1159. Python, 6114 tests. |
| Orchestrator + UI | `%USERPROFILE%\Documents\Repo cloning\taxspine-orchestrator` | FastAPI coordinator, job runner, ALTANA dashboard. |

**Rule:** Never touch FIFO maths, cost basis, or classification from the orchestrator repo. Engine is source of truth.

---

## What IS Done (as of 2026-05-11)

### Branding — COMPLETE
- [x] All TAXNOR/Taxnor/taxnor strings replaced with ALTANA across all `ui/*.jsx` and `ui/index.html`
- [x] Ledger SVG logo in sidebar (two-bar design, green accent)
- [x] Avatar shows KR
- [x] Page title: `ALTANA — Crypto Tax & Portfolio Dashboard`
- [x] Settings subtitle: `the owner · N XRPL accounts`
- [x] `data-screen-label="ALTANA · {route}"`

### Audit Remediation — COMPLETE
- [x] ALL 139 findings closed (Batches 1–29 FINAL, 2026-03-18)
- [x] HANDOFF.md was outdated — no further batches needed
- [x] No audit work remains

### ADR-0015 (NFT Classification — §9-7) — COMPLETE
- [x] `CLASSIFICATION_POLICY_VERSION = "1.2.0"` in `src/tax_spine/classification/engine.py`
- [x] `_parse_donor_basis_nok(notes)` in `src/tax_spine/fifo/engine.py`
- [x] `_process_gift_in()` in `src/tax_spine/fifo/engine.py`:
  - `donor_basis_nok=<amount>` in notes → `BasisStatus.KNOWN`, basis = that amount
  - No marker → `BasisStatus.KNOWN`, basis = `Decimal("0")` (zero-basis fallback per ADR-0015)
- [x] Review queue Step J (`_items_from_gift_in_no_donor_basis`) — surfaces GIFT_IN lots missing donor basis
- [x] `TestGiftInOut` in `tests/test_fifo_engine.py` updated:
  - 4 new tests (no-basis zero-known, explicit 48000, explicit zero, decimal 12345.67)
  - `test_gift_out_consumes_lots` preserved
  - All 5 pass

### Tax Spine Test Suite — CLEAN
- [x] 6114 passed, 21 skipped, 0 failed (2026-05-11)

### Data Migration — PRICES DONE, EVENTS STAGED
- [x] Migration script: `C:\tmp\migrate_taxspine_db.py` — exports taxspine.db → orchestrator CSVs
- [x] Price CSVs: 8 years (2017–2026) exported from taxspine.db price_cache, copied to orchestrator:
  - `C:\tmp\taxspine_orchestrator\data\prices\combined_nok_{year}.csv`
  - Deduplicated by tier priority (topper_actual > peg > cg_direct > cc_usd_nb > usd_spot_override)
  - 97,242 unique (asset, date) pairs
- [x] Event CSVs generated in `C:\tmp\taxspine_migration\events\`:
  - `owner_{year}_events.csv` for 2017–2026
  - 66,143 events from taxspine.db (DEPOSIT→TRANSFER, WITHDRAWAL→TRANSFER, BUY/SELL→TRADE)
  - **NOT yet ingested into orchestrator — see open work**

---

## What Is NOT Done — Open Work (Priority Order)

### 1. Ingest Ola's Event CSVs — NEXT UP
The events are exported and ready. They need to be uploaded into the orchestrator and run:

1. Copy CSVs to orchestrator workspace:
   - Source: `C:\tmp\taxspine_migration\events\owner_{year}_events.csv`
   - Target: Upload via Sources page OR POST to `/workspace/sources`
2. Run NOR_MULTI pipeline sequentially per year **with carry-forward lots**:
   - Order: 2021 → 2022 → 2023 → 2024 → 2025
   - Verify `lots.db` is populated after each run
3. Check Dashboard: lots, gains, RF-1159 output, review queue items

**NOTE: Do NOT re-run 2026 as a tax year — exported for completeness only.**

### 2. Review Queue Step J Tests
- [ ] Write `tests/test_review_queue_engine.py` tests for `_items_from_gift_in_no_donor_basis`
- [ ] Cover: no GIFT_IN events → empty, GIFT_IN with donor basis → no item, GIFT_IN without → REVIEW_ZERO_BASIS_NFT

### 3. Orchestrator Test Suite Check
- [ ] Run `python -m pytest` in `taxspine-orchestrator` — verify still at 1731+ passing after session changes
- [ ] Last known: 1153 tests (pre-session). Count may have changed with new tests.

### 4. Cryptodog (Deferred — after XRPL pipeline confirmed)
- Friend's data, separate workspace, XRPL from 2017 onward
- Visual representation in charts alongside Ola's data
- Profile name: Cryptodog. **Trigger: after step 1 above confirmed working.**

### 5. Firi 2026 CSV Upload (Deferred)
- Upload Firi 2026 activity CSV when available

---

## Data State

| Data | Location | State |
|---|---|---|
| taxspine.db (source of truth) | `%USERPROFILE%\Documents\Finans\Crypto taxes\_state\taxspine.db` | 66,143 events, 120,595 price rows. READ-ONLY reference. |
| Event CSVs (migrated) | `C:\tmp\taxspine_migration\events\` | Generated, NOT YET ingested |
| Price CSVs (migrated) | `C:\tmp\taxspine_orchestrator\data\prices\` | Copied, ready |
| Orchestrator lots.db | `C:\tmp\taxspine_orchestrator\data\lots.db` | 0 rows — empty until ingestion run |
| Cryptodog database | Unknown | Deferred |

**The April 2025 filing computation (FIFO lots, etc.) lived in taxspine.db `fifo_lots` table — which had 0 rows. FIFO was computed at report time and discarded. The PDF skatteoppgjør is the only artifact of that computation. Re-running with current data is the path forward.**

---

## Dashboard File Map

```
ui/index.html          — entry point (React 18 + Babel standalone)
ui/styles.css          — ALTANA design system (green glassmorphic)
ui/data.js             — DEMO fallback data (used when API offline)
ui/tweaks-panel.jsx    — density toggles
ui/icons.jsx           — SVG icon set
ui/charts.jsx          — Sparkline, AreaChart, Donut, Waterfall, Candlestick
ui/shell.jsx           — Sidebar (Ledger logo + ALTANA, KR avatar), Topbar, Toast
ui/page-dashboard.jsx  — KPIs, charts, activity, filing checklist
ui/page-flow.jsx       — Sources (live XRPL CRUD), TaxCenter (live jobs), ReviewQueue
ui/page-data.jsx       — Portfolio, Prices, Charts
ui/page-system.jsx     — Diagnostics (live), Alerts (live), Settings (KR identity), Audit
ui/app.jsx             — Root: loads /workspace, /diagnostics, /jobs, /health
```

---

## Rules — Do Not Break These

1. **Do not touch backend logic** for UI changes. `ui/` only.
2. **Do not re-run XRPL pipeline or wipe lots.db** without explicit approval.
3. **Do not modify FIFO, classification, or RF-1159** without an ADR and Ola's sign-off.
4. **Do not invent user identity.** the owner, KR, owner@example.com. Never Henrik. Never MK.
5. **Always read this file** before starting a new session or picking up a task.
6. **ALTANA is the name.** Zero occurrences of TAXNOR/Taxnor/taxnor are allowed anywhere.
7. **Audit is done.** Do not open new audit batches or re-examine closed findings.
