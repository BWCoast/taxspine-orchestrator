// ALTANA — Page 1: Dashboard / Home (live data)

// Asset colours — used for allocation donut.
const ASSET_COLORS = {
  XRP: '#346AA9', BTC: '#f7931a', ETH: '#627eea', SOL: '#14f195',
  HBAR: '#8b5cf6', CSPR: '#ef4444', FLR: '#e62058', ADA: '#3cc8c8',
  USDC: '#2775ca', RLUSD: '#22c55e', SOLO: '#3b82f6', CSC: '#10b981',
  ELS: '#f59e0b', CORE: '#a855f7', XAH: '#06b6d4', HASH: '#ec4899',
};
const assetColor = (sym) => ASSET_COLORS[sym] || '#6366f1';

// ── Formatting helpers (exported to window for other pages) ──────────────────
const fmtNum = (n) => {
  if (n == null) return '—';
  const abs = Math.abs(n);
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return (n / 1e3).toLocaleString('en-US', { maximumFractionDigits: 1 }) + 'k';
  return Math.round(n).toString();
};
const fmtFull = (n) => {
  if (n == null) return '—';
  return Math.round(n).toLocaleString('en-US').replace(/,/g, ' '); // thin space
};

// ── KPI card ─────────────────────────────────────────────────────────────────
// Sparklines removed — no historical KPI API exists, so they were always synthetic DEMO data.
const KPI = ({ label, value, sub, subKind, currency, loading }) => {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">
        {currency && <span style={{ fontSize: 13, color: 'var(--text-faint)', marginRight: 4 }}>{currency}</span>}
        {loading ? <span style={{ color: 'var(--text-faint)' }}>—</span> : value}
      </div>
      {sub && <div className="kpi-sub"><span className={subKind}>{sub}</span></div>}
    </div>
  );
};

// ── Age helper — "2m ago" / "3h ago" ─────────────────────────────────────────
const relAge = (isoStr) => {
  if (!isoStr) return null;
  const ts = isoStr.endsWith('Z') ? isoStr : isoStr + 'Z';
  const diff = Math.round((Date.now() - new Date(ts).getTime()) / 60000);
  if (diff < 2)   return 'just now';
  if (diff < 60)  return `${diff}m ago`;
  if (diff < 1440) return `${Math.round(diff / 60)}h ago`;
  return `${Math.round(diff / 1440)}d ago`;
};

// ── Dashboard ─────────────────────────────────────────────────────────────────
const Dashboard = ({ juris, year, setYear, addToast, wsName, userName, setRoute,
                     diags, wsConfig, recentJobs, runningJobs, review }) => {
  // diags, wsConfig, recentJobs, runningJobs, review — shared from App (10 s polling).
  // These are the same objects every other page sees; they all update simultaneously.
  const currentYear = new Date().getFullYear();
  const ccy = juris === 'NO' ? 'NOK' : 'GBP';
  const fx  = juris === 'NO' ? 1 : 0.078;

  // ── Page-local data — poll 10 s while Dashboard is active ─────────────────
  const { data: portfolio } = window.useApi(() => window.API.getPortfolio(year, true), [year], { pollInterval: 10000 });
  const { data: sidecar }   = window.useApi(() => window.API.getSidecar(year),          [year], { pollInterval: 10000 });
  const { data: lotMeta }   = window.useApi(() => window.API.getLotYear(year),           [year], { pollInterval: 10000 });

  // ── KPI values ────────────────────────────────────────────────────────────
  const totalGains  = sidecar ? parseFloat(sidecar.total_gains_nok  || 0) * fx : null;
  const totalLosses = sidecar ? parseFloat(sidecar.total_losses_nok || 0) * fx : null;
  const taxEstimate = sidecar ? parseFloat(sidecar.tax_owed_nok     || 0) * fx : null;

  const totalValue = portfolio?.total_market_value_nok != null
    ? parseFloat(portfolio.total_market_value_nok) * fx : null;
  const unrealised = portfolio?.total_unrealized_gain_nok != null
    ? parseFloat(portfolio.total_unrealized_gain_nok) * fx : null;
  const assetCount = portfolio?.asset_count ?? null;

  // Lot count: prefer lotMeta.active_lots, fall back to summing portfolio lots
  const totalLots = lotMeta?.active_lots
    ?? portfolio?.assets?.reduce((s, a) => s + (a.lot_count || 0), 0)
    ?? null;

  // ── Allocation donut — real portfolio, fallback to DEMO shape ─────────────
  const allocation = (() => {
    if (!portfolio?.assets) return [];
    const sorted = [...portfolio.assets]
      .filter(a => parseFloat(a.market_value_nok || 0) > 0)
      .sort((a, b) => parseFloat(b.market_value_nok) - parseFloat(a.market_value_nok));
    const top = sorted.slice(0, 5).map(a => ({
      label: a.asset,
      value: parseFloat(a.market_value_nok) * fx,
      color: assetColor(a.asset),
    }));
    const otherVal = sorted.slice(5).reduce((s, a) => s + parseFloat(a.market_value_nok) * fx, 0);
    if (otherVal > 0) top.push({ label: 'Other', value: otherVal, color: '#64748b' });
    return top;
  })();
  const totalAlloc = allocation.reduce((s, a) => s + a.value, 0);

  // ── Portfolio chart — only render when real data exists ──────────────────
  // totalValue===null means portfolio hasn't loaded OR workspace has no lots.
  // hasRealPortfolio guards against showing fake DEMO curves for empty workspaces.
  const hasRealPortfolio = totalValue != null && totalValue > 0;
  const [chartRange, setChartRange] = React.useState('90D');
  const chartPoints = { '90D': 90, '180D': 180, '1Y': 365, 'All': 730 }[chartRange] || 90;
  const tsRaw   = window.DEMO.timeSeries(42, chartPoints, 0.003, 0.022);
  const tsScale = hasRealPortfolio ? totalValue / (tsRaw[tsRaw.length - 1] || 1_800_000) : 1;
  const tsData  = hasRealPortfolio ? tsRaw.map(v => v * tsScale * fx) : [];

  // ── Activity feed — completed jobs only ──────────────────────────────────
  // recentJobs is sharedJobs (all statuses). Filter to completed only so the
  // feed doesn't show running/failed jobs as "recent activity".
  const activity = (recentJobs ?? [])
    .filter(j => j.status === 'completed')
    .map(j => ({
      time: relAge(j.updated_at) || '—',
      who:  j.status,
      text: `Tax job ${j.input?.country}-${j.input?.tax_year} · ${j.status}`,
    }));

  // ── System health ─────────────────────────────────────────────────────────
  const wsWallets = wsConfig?.xrpl_accounts?.length ?? 0;
  const wsCsvs    = wsConfig?.csv_files?.length    ?? 0;
  const wsRunning = runningJobs?.length             ?? 0;
  const wsFailed  = (recentJobs ?? []).filter(j => j.status === 'failed').length;

  const healthItems = diags
    ? [
        {
          name: 'Lot store',
          status: diags.lots?.db_exists ? 'green' : 'red',
          // Surface "error" rather than opaque "no data" when the backend reports a failure.
          last: diags.lots?.size_kb ? `${diags.lots.size_kb} KB`
              : diags.lots?.error   ? 'error — see diagnostics' : 'no data',
        },
        {
          name: 'Price cache',
          status: (diags.prices?.combined_csvs?.length ?? 0) > 0 ? 'green' : 'amber',
          last: diags.prices?.combined_csvs?.[0]?.age_hours != null
            ? `${diags.prices.combined_csvs[0].age_hours.toFixed(1)}h ago` : 'empty',
        },
        {
          name: 'prices.db',
          status: diags.prices?.prices_db?.exists ? 'green' : 'amber',
          // toFixed(1) avoids rounding 4,600 → "5k"; shows "4.6k" which matches Diagnostics.
          last: diags.prices?.prices_db?.row_count
            ? `${(diags.prices.prices_db.row_count / 1000).toFixed(1)}k rows` : '—',
        },
        {
          name: 'Active jobs',
          status: wsRunning > 0 ? 'amber' : wsFailed > 0 ? 'amber' : 'green',
          // Show failed count when idle; running takes priority in the label.
          last: [
            wsRunning > 0 ? `${wsRunning} running` : '',
            wsFailed  > 0 ? `${wsFailed} failed`   : '',
          ].filter(Boolean).join(' · ') || '0 running',
        },
        {
          name: 'Dedup store',
          status: (diags.dedup?.total_skips ?? 0) > 500 ? 'amber' : 'green',
          last: `${diags.dedup?.total_skips ?? 0} skips`,
        },
        {
          // Workspace-scoped: wallets + CSVs for THIS workspace only (not default).
          name: 'Workspace',
          status: (wsWallets + wsCsvs) > 0 ? 'green' : 'amber',
          last: `${wsWallets} wallet${wsWallets !== 1 ? 's' : ''} · ${wsCsvs} CSV${wsCsvs !== 1 ? 's' : ''}`,
        },
      ]
    : [];

  // ── Filing checklist ──────────────────────────────────────────────────────
  // Three states per item:
  //   done:true            — signal loaded and condition met      → green tick
  //   done:false           — signal loaded and condition NOT met  → empty box
  //   unknown:true         — signal still loading, can't evaluate → dimmed dash
  // unknown items are excluded from the readiness percentage so a loading diags
  // doesn't make the checklist appear worse than it is.
  const missingBasisCount = review?.missing_basis_count ?? 0;
  const warningCount      = review?.total_warnings ?? 0;
  // Completed job: null when recentJobs not yet loaded, false when loaded but none completed.
  // MUST be declared before reviewDataMeaningful — it is referenced there.
  const hasCompletedJob = recentJobs == null ? null
    : recentJobs.some(j => j.status === 'completed');
  // Review data is only meaningful after at least one completed job — otherwise the
  // API returns empty/zero counts that look "resolved" but actually mean "never computed".
  // reviewDataMeaningful is true only when a completed job is confirmed.
  const reviewDataMeaningful = hasCompletedJob === true;
  // unknown for review items: no completed job (null = loading, false = none run yet).
  const reviewUnknown = !reviewDataMeaningful;
  const mbLabel = reviewUnknown
    ? (hasCompletedJob == null ? 'Missing basis — checking…' : 'Missing basis — no job run yet')
    : missingBasisCount > 0 ? `Resolve ${missingBasisCount} missing-basis assets` : 'Missing basis clear';
  const warnLabel = reviewUnknown
    ? (hasCompletedJob == null ? 'Warnings — checking…' : 'Warnings — no job run yet')
    : warningCount > 0 ? `Resolve ${warningCount} warning(s)` : 'All warnings resolved';
  // Prices: null when diags not yet loaded (unknown), boolean once loaded.
  const hasPrices = diags == null ? null
    : (diags.prices?.combined_csvs?.length ?? 0) > 0 || !!diags.prices?.prices_db?.exists;
  // Sources: null when wsConfig not yet loaded.
  const sourceCount = wsConfig == null ? null
    : (wsConfig.xrpl_accounts?.length ?? 0) + (wsConfig.csv_files?.length ?? 0);

  const checklistItems = [
    { done: true,    label: 'Jurisdiction set' },
    { done: sourceCount == null ? false : sourceCount > 0,
      unknown: sourceCount == null,
      label: sourceCount == null ? 'Sources — checking…'
           : `${sourceCount} source${sourceCount !== 1 ? 's' : ''} connected` },
    { done: !!hasPrices,
      unknown: hasPrices == null,
      label: 'Prices collected' },
    { done: reviewDataMeaningful && missingBasisCount === 0,
      unknown: reviewUnknown,
      label: mbLabel },
    { done: reviewDataMeaningful && warningCount === 0,
      unknown: reviewUnknown,
      label: warnLabel },
    { done: !!hasCompletedJob,
      unknown: hasCompletedJob == null,
      label: juris === 'NO' ? 'Generate RF-1159 report' : 'Generate SA108 report' },
  ];
  // Exclude unknown items from readiness so loading state doesn't distort the %.
  const knownItems   = checklistItems.filter(c => !c.unknown);
  const readinessPct = knownItems.length === 0 ? 0
    : Math.round(knownItems.filter(c => c.done).length / knownItems.length * 100);
  const readinessColor = readinessPct === 100 ? 'green' : readinessPct >= 50 ? 'amber' : '';

  // ── Last sync label ───────────────────────────────────────────────────────
  const lastJob = recentJobs?.[0];
  const lastSyncLabel = lastJob?.updated_at ? relAge(lastJob.updated_at) : 'no jobs yet';

  // ── Lots label ────────────────────────────────────────────────────────────
  const lotsLabel = totalLots != null
    ? `${Number(totalLots).toLocaleString('en-US')} lots tracked`
    : diags ? 'no lot data' : '—';

  const [jobRunning, setJobRunning] = React.useState(false);
  const runTaxJob = async () => {
    if (sourceCount === 0) {
      addToast({ title: 'No sources', msg: 'Add a CSV file or XRPL wallet first on the Sources page', kind: 'warn' });
      return;
    }
    setJobRunning(true);
    try {
      const country = juris === 'NO' ? 'norway' : 'uk';
      const job = await window.API.runWorkspace({ tax_year: year, country });
      const jobId = job?.job_id || job?.id || '';
      addToast({ title: 'Tax job started', msg: `Job ${jobId.slice(0, 8)}… · ${juris} ${year}` });
    } catch (e) {
      const msg = String(e.message || e);
      addToast({ title: 'Job failed to start', msg: msg.slice(0, 120), kind: 'error' });
    } finally {
      setJobRunning(false);
    }
  };

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">{userName || wsName || 'Dashboard'}</div>
          <div className="page-sub">
            Tax year overview · last run {lastSyncLabel} · {lotsLabel}
          </div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <div className="year-pill">
            <button className="arrow" onClick={() => setYear(year - 1)}>
              <Icon name="chevron_left" size={12} />
            </button>
            <span>{year}</span>
            <button
              className="arrow"
              onClick={() => setYear(year + 1)}
              disabled={year >= currentYear}
            >
              <Icon name="chevron_right" size={12} />
            </button>
          </div>
          <button className="btn primary" onClick={runTaxJob} disabled={jobRunning}>
            <Icon name="play" size={12} /> {jobRunning ? 'Starting…' : 'Run tax job'}
          </button>
        </div>
      </div>

      {/* ── KPI row ─────────────────────────────────────────────────────── */}
      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <KPI
          label="Total gains"
          value={fmtFull(totalGains)}
          sub={totalGains != null
            ? (totalLosses != null ? `Net: ${ccy} ${fmtNum(totalGains - totalLosses)}` : '22% tax rate')
            : 'run a tax job'}
          subKind={totalGains != null ? 'pos' : ''}
          sparkSeed={1} currency={totalGains != null ? ccy : ''} loading={!sidecar} showSpark={hasRealPortfolio}
        />
        <KPI
          label="Unrealised P&L"
          value={fmtFull(unrealised)}
          sub={unrealised != null
            ? (unrealised >= 0 ? '↑ portfolio in profit' : '↓ portfolio in loss')
            : 'no lot data'}
          subKind={unrealised != null ? (unrealised >= 0 ? 'pos' : 'neg') : ''}
          sparkSeed={2} currency={unrealised != null ? ccy : ''} loading={!portfolio} showSpark={hasRealPortfolio}
        />
        <KPI
          label="Assets held"
          value={assetCount != null ? String(assetCount) : '—'}
          sub={missingBasisCount > 0 ? `${missingBasisCount} missing basis` : 'all bases resolved'}
          subKind={missingBasisCount > 0 ? 'neg' : 'pos'}
          sparkSeed={3} currency="" loading={!portfolio} showSpark={hasRealPortfolio}
        />
        <KPI
          label="Tax estimate"
          value={fmtFull(taxEstimate)}
          sub={taxEstimate != null ? `${juris === 'NO' ? '22%' : '20%'} · pending review` : 'run tax job'}
          subKind=""
          sparkSeed={4} currency={taxEstimate != null ? ccy : ''} loading={!sidecar} showSpark={hasRealPortfolio}
        />
      </div>

      {/* ── UK FX disclaimer — hardcoded rate, shown only for UK jurisdiction ── */}
      {juris !== 'NO' && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '6px 12px', marginBottom: 12,
          background: 'var(--bg-elevated, #1e2130)',
          border: '1px solid var(--border-muted, #2a2f45)',
          borderRadius: 6,
          fontSize: 11, color: 'var(--text-muted)',
        }}>
          <span style={{ fontSize: 13 }}>⚠</span>
          <span>
            GBP values are approximate — converted from NOK using a fixed rate of
            {' '}≈ £1 = NOK {(1 / 0.078).toFixed(1)} (hardcoded). Do not rely on these
            figures for filing; use the RF-1159 / HMRC outputs directly.
          </span>
        </div>
      )}

      {/* ── Portfolio chart + Allocation donut ──────────────────────────── */}
      <div className="grid grid-12" style={{ marginBottom: 16 }}>
        <div style={{ gridColumn: 'span 8' }}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">
                Portfolio value · 90 days
                <span className="meta">· {ccy}</span>
              </div>
              <div className="card-actions">
                <div className="tabs">
                  {['90D', '180D', '1Y', 'All'].map(r => (
                    <button key={r} className={chartRange === r ? 'active' : ''} onClick={() => setChartRange(r)}>{r}</button>
                  ))}
                </div>
              </div>
            </div>
            <div style={{ padding: 12 }}>
              {hasRealPortfolio
                ? <AreaChart data={tsData} width={800} height={220} color="var(--accent)" currency={ccy} />
                : <div style={{ height: 220, display: 'grid', placeItems: 'center', color: 'var(--text-faint)', fontSize: 13 }}>
                    No portfolio data — run a tax job to populate
                  </div>
              }
            </div>
          </div>
        </div>

        <div style={{ gridColumn: 'span 4' }}>
          <div className="card" style={{ height: '100%' }}>
            <div className="card-header">
              <div className="card-title">Allocation</div>
              <span className="tag">{assetCount != null ? assetCount : '0'} assets</span>
            </div>
            <div style={{ padding: 16, display: 'flex', alignItems: 'center', gap: 16 }}>
              {!hasRealPortfolio
                ? <div style={{ width: '100%', textAlign: 'center', color: 'var(--text-faint)', fontSize: 12, padding: '24px 0' }}>
                    No data yet
                  </div>
                : <>
              <div style={{ position: 'relative', width: 140, height: 140, flexShrink: 0 }}>
                <Donut data={allocation} size={140} thickness={16} />
                <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', textAlign: 'center' }}>
                  <div>
                    <div style={{ fontSize: 10, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Total</div>
                    <div className="num" style={{ fontSize: 14, fontWeight: 600 }}>{fmtNum(totalValue)}</div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{ccy}</div>
                  </div>
                </div>
              </div>
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                {allocation.map(a => (
                  <div key={a.label} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11.5 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: a.color }} />
                    <span style={{ flex: 1, fontWeight: 500 }}>{a.label}</span>
                    <span className="num" style={{ color: 'var(--text-muted)' }}>
                      {totalAlloc > 0 ? ((a.value / totalAlloc) * 100).toFixed(1) + '%' : '—'}
                    </span>
                  </div>
                ))}
              </div>
              </>}
            </div>
          </div>
        </div>
      </div>

      {/* ── Activity · Checklist · Health ────────────────────────────────── */}
      <div className="grid grid-12">
        <div style={{ gridColumn: 'span 5' }}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">Recent activity</div>
              <button className="btn ghost sm" onClick={() => setRoute('audit')}>View all</button>
            </div>
            <div className="card-body" style={{ padding: 0 }}>
              {activity.map((a, i) => (
                <div key={i} style={{
                  display: 'flex', gap: 12, padding: '10px 16px', alignItems: 'center',
                  borderBottom: i < activity.length - 1 ? '1px solid var(--border)' : 'none',
                }}>
                  <span className="num" style={{ fontSize: 11, color: 'var(--text-faint)', width: 46 }}>{a.time}</span>
                  <span className="tag">{a.who}</span>
                  <span style={{ fontSize: 12.5, flex: 1 }}>{a.text}</span>
                </div>
              ))}
              {!activity.length && (
                <div style={{ padding: '20px 16px', color: 'var(--text-faint)', fontSize: 12 }}>
                  No completed jobs yet
                </div>
              )}
            </div>
          </div>
        </div>

        <div style={{ gridColumn: 'span 4' }}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">Filing checklist · {juris} {year}</div>
            </div>
            <div className="card-body">
              <ul className="checklist">
                {checklistItems.map((item, i) => (
                  <li key={i} className={item.done ? 'done' : ''}>
                    <span className="chk">{item.done && <Icon name="check" size={10} />}</span>
                    {item.label}
                  </li>
                ))}
              </ul>
              <div style={{ marginTop: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>
                  <span>Filing readiness</span>
                  <span className="num">{readinessPct}%</span>
                </div>
                <div className={`bar ${readinessColor}`}>
                  <div style={{ width: readinessPct + '%' }} />
                </div>
              </div>
            </div>
          </div>
        </div>

        <div style={{ gridColumn: 'span 3' }}>
          <div className="card">
            <div className="card-header"><div className="card-title">System health</div></div>
            <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {!diags && [
                'Lot store', 'Price cache', 'prices.db', 'Active jobs', 'Dedup store', 'Workspace',
              ].map(name => (
                <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                  <span className="dot" style={{ background: 'var(--text-faint)' }}/>
                  <span style={{ flex: 1 }}>{name}</span>
                  <span className="num" style={{ color: 'var(--text-faint)', fontSize: 10.5 }}>—</span>
                </div>
              ))}
              {healthItems.map(d => (
                <div key={d.name} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                  <span className={`dot ${d.status}`} />
                  <span style={{ flex: 1 }}>{d.name}</span>
                  <span className="num" style={{ color: 'var(--text-faint)', fontSize: 10.5 }}>{d.last}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

window.Dashboard = Dashboard;
window.fmtNum    = fmtNum;
window.fmtFull   = fmtFull;
window.relAge    = relAge;
window.assetColor = assetColor;
