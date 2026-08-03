// ALTANA — Pages: Prices, Portfolio, Charts

// ── Helpers (safe references; page-dashboard.jsx defines and exports these) ──
const _fmtFull   = n => (window.fmtFull   || (x => x.toLocaleString('en-US', {maximumFractionDigits: 0})))(n);
const _assetColor = s => (window.assetColor || (() => '#6366f1'))(s);

// ─────────────────────────────────────────────────────────────────────────────
// Prices page
// ─────────────────────────────────────────────────────────────────────────────
const Prices = ({ juris, addToast }) => {
  const ccy = juris === 'NO' ? 'NOK' : 'GBP';
  const [collecting,   setCollecting]   = React.useState(false);
  const [deletingSpec, setDeletingSpec] = React.useState(null);

  const { data: diags, loading: diagsLoading, reload: reloadDiags } = window.useApi(() => window.API.getDiagnostics(), []);
  const { data: ws, reload: reloadWs }  = window.useApi(() => window.API.getWorkspace(), []);

  const removeXrplAsset = async (spec) => {
    setDeletingSpec(spec);
    try {
      await window.API.removeXrplAsset(spec);
      addToast({ title: 'Asset removed', msg: spec });
      reloadWs();
    } catch (e) {
      addToast({ title: 'Remove failed', msg: String(e.message || e).slice(0, 100), kind: 'error' });
    } finally {
      setDeletingSpec(null);
    }
  };

  // ── prices.db stats ──────────────────────────────────────────────────────
  const db        = diags?.prices?.prices_db ?? {};
  const dbHealthy = !!(db.exists && !db.error);
  const dbRows    = db.row_count != null ? db.row_count.toLocaleString() : '—';
  const dbSizeMB  = db.size_kb  != null ? (db.size_kb / 1024).toFixed(1) + ' MB' : '—';
  const dbAge     = (() => {
    if (!db.last_fetched_at) return '—';
    const h = (Date.now() - new Date(db.last_fetched_at).getTime()) / 3_600_000;
    return h < 1 ? `${Math.round(h * 60)}m` : `${h.toFixed(1)}h`;
  })();

  // ── combined CSV files (from diagnostics) ────────────────────────────────
  const combinedCsvs = (diags?.prices?.combined_csvs ?? [])
    .map(f => {
      const m = (f.name || '').match(/combined_(nok|gbp)_(\d{4})\.csv/i);
      return {
        ...f,
        year:     m ? parseInt(m[2]) : '?',
        currency: m ? m[1].toUpperCase() : '?',
      };
    })
    .sort((a, b) => (b.year || 0) - (a.year || 0));

  const csvTotal  = diags?.prices?.csv_count ?? 0;

  // ── XRPL asset registry (from workspace) ─────────────────────────────────
  const xrplAssets = ws?.xrpl_assets ?? [];

  const fmtAge = h => h < 1 ? `${Math.round(h * 60)}m` : `${h.toFixed(1)}h`;

  const collectNow = async () => {
    setCollecting(true);
    try {
      const year = new Date().getFullYear();
      await window.API.collectPrices(year);
      addToast({ title: 'Price collect started', msg: `Fetching ${year} prices — reload in a moment` });
      setTimeout(reloadDiags, 5000);
    } catch (e) {
      addToast({ title: 'Collect failed', msg: String(e.message || e).slice(0, 120) });
    } finally {
      setCollecting(false);
    }
  };

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Prices & Valuation</div>
          <div className="page-sub">
            {diagsLoading
              ? 'Loading…'
              : dbHealthy
                ? `prices.db active · ${dbRows} rows · last collect ${dbAge} ago`
                : 'prices.db not found · CSV fallback active'}
          </div>
        </div>
        <button className="btn primary" onClick={collectNow} disabled={collecting}>
          <Icon name="refresh" size={12}/> {collecting ? 'Collecting…' : 'Collect prices now'}
        </button>
      </div>

      {/* ── Top stat cards ── */}
      <div className="grid grid-3" style={{marginBottom: 16}}>
        <div className="card">
          <div className="card-header">
            <div className="card-title">prices.db health</div>
            <span className={`tag ${dbHealthy ? 'green' : 'red'}`}>
              <span className={`dot ${dbHealthy ? 'green' : 'red'}`}/>
              {dbHealthy ? 'Healthy' : 'Missing'}
            </span>
          </div>
          <div className="card-body">
            <div className="kpi-value" style={{fontSize: 18}}>
              {dbRows} <span style={{fontSize: 12, color: 'var(--text-faint)'}}>rows</span>
            </div>
            <div className="kpi-sub">{dbSizeMB} · {csvTotal} CSV files cached</div>
            <div style={{marginTop: 10, fontSize: 11.5, color: 'var(--text-muted)'}}>
              Freshness: <span className="num">{dbAge}</span> ago
            </div>
            <div className="bar" style={{marginTop: 4}}>
              <div style={{width: dbHealthy ? '92%' : '0%'}}/>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-header"><div className="card-title">Combined CSVs</div></div>
          <div className="card-body">
            <div className="row" style={{gap: 10}}>
              <span className="num pos" style={{fontSize: 18, fontWeight: 600, color: 'var(--green)'}}>
                {combinedCsvs.length}
              </span>
              <span style={{fontSize: 12, color: 'var(--text-muted)'}}>cached years</span>
            </div>
            <div className="row" style={{gap: 16, marginTop: 6, fontSize: 12, color: 'var(--text-muted)'}}>
              <span><span className="num">{combinedCsvs.filter(f => f.currency === 'NOK').length}</span> NOK</span>
              <span><span className="num">{combinedCsvs.filter(f => f.currency === 'GBP').length}</span> GBP</span>
            </div>
            <div style={{marginTop: 10, fontSize: 11, color: 'var(--text-faint)'}}>
              Sources: Kraken × Norges Bank · OnTheDEX · CoinGecko
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-header"><div className="card-title">Active mode</div></div>
          <div className="card-body">
            <div style={{fontSize: 14, fontWeight: 500}}>{dbHealthy ? 'prices.db' : 'CSV fallback'}</div>
            <div className="kpi-sub">
              {dbHealthy
                ? `${dbRows} rows · updated ${dbAge} ago`
                : 'Run POST /prices/fetch to populate'}
            </div>
            <div className="row" style={{marginTop: 12, gap: 6}}>
              <button className="btn sm" onClick={collectNow} disabled={collecting}>
                {collecting ? 'Collecting…' : 'Refresh now'}
              </button>
              <button className="btn ghost sm"
                onClick={() => addToast({ title: 'Manual price entry', msg: 'Edit combined_nok_YYYY.csv directly and click "Collect prices now" to re-index.', kind: 'info' })}>
                Manual entry
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ── Combined CSVs table + XRPL registry ── */}
      <div className="grid grid-12" style={{marginBottom: 16}}>
        <div style={{gridColumn: 'span 7'}}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">
                Combined price CSVs
                <span className="meta"> · combined_{ccy.toLowerCase()}_*.csv</span>
              </div>
              <button className="btn sm" onClick={() => addToast({ title: 'Import price CSV', msg: 'Place combined_nok_YYYY.csv in the prices folder, then click "Collect prices now" to re-index.', kind: 'info' })}>
                <Icon name="upload" size={11}/> Import
              </button>
            </div>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Year</th>
                  <th>Currency</th>
                  <th className="num">Age</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {combinedCsvs.length === 0 && !diagsLoading && (
                  <tr>
                    <td colSpan={4} style={{textAlign: 'center', color: 'var(--text-faint)', padding: '24px 0'}}>
                      No combined price CSVs found — run POST /prices/fetch to generate
                    </td>
                  </tr>
                )}
                {combinedCsvs.map(f => (
                  <tr key={`${f.currency}-${f.year}`}>
                    <td className="num" style={{fontWeight: 500}}>{f.year}</td>
                    <td>
                      <span className={`tag ${f.currency === 'NOK' ? 'blue' : 'amber'}`}>{f.currency}</span>
                    </td>
                    <td className="num muted">{fmtAge(f.age_hours)} ago</td>
                    <td style={{textAlign: 'right'}}>
                      <button className="btn ghost sm"
                        title="File is on the server filesystem — access via /prices endpoint or SSH"
                        onClick={() => addToast({ title: 'Price CSV', msg: `${f.name} is stored on the server. Access via the API or server filesystem.`, kind: 'info' })}>
                        <Icon name="download" size={11}/>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div style={{gridColumn: 'span 5'}}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">XRPL asset registry</div>
              <button className="btn sm"
                onClick={() => addToast({ title: 'Use Settings → XRPL Assets to add', kind: 'info' })}>
                <Icon name="plus" size={11}/> Add
              </button>
            </div>
            <table className="tbl">
              <thead><tr><th>Symbol</th><th>Issuer</th><th></th></tr></thead>
              <tbody>
                {xrplAssets.length === 0 && (
                  <tr>
                    <td colSpan={3} style={{textAlign: 'center', color: 'var(--text-faint)', padding: '20px 0'}}>
                      No XRPL assets registered
                    </td>
                  </tr>
                )}
                {xrplAssets.map(spec => {
                  const dot    = spec.indexOf('.');
                  const sym    = dot >= 0 ? spec.slice(0, dot) : spec;
                  const issuer = dot >= 0 ? spec.slice(dot + 1, dot + 7) + '…' : '—';
                  return (
                    <tr key={spec}>
                      <td style={{fontWeight: 500}}>{sym}</td>
                      <td className="mono muted" style={{fontSize: 11.5}}>{issuer}</td>
                      <td style={{textAlign: 'right'}}>
                        <button className="btn ghost sm"
                          disabled={deletingSpec === spec}
                          onClick={() => removeXrplAsset(spec)}>
                          <Icon name="x" size={10}/>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// Portfolio page
// ─────────────────────────────────────────────────────────────────────────────
const Portfolio = ({ juris, year, addToast }) => {
  const ccy    = juris === 'NO' ? 'NOK' : 'GBP';
  const fxRate = juris === 'NO' ? 1 : 0.078;
  const D      = window.DEMO;

  const [expanded,  setExpanded]  = React.useState(null);
  const [priceMode, setPriceMode] = React.useState('live');

  const exportCsv = () => {
    if (!assets.length) {
      addToast && addToast({ title: 'No data', msg: 'Load portfolio data first', kind: 'warn' });
      return;
    }
    const rows = [
      ['Asset', 'Lots', 'Quantity', 'AvgCost_NOK', 'Price_NOK', 'MarketValue_NOK', 'UnrealisedGain_NOK'],
      ...assets.map(a => [
        a.asset,
        a.lot_count ?? '',
        a.total_quantity ?? '',
        a.avg_cost_nok_per_unit ?? '',
        a.price_nok ?? '',
        a.market_value_nok ?? '',
        a.unrealized_gain_nok ?? '',
      ]),
    ];
    const csv  = rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url; a.download = `portfolio_${year}_holdings.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const { data: portfolio, loading, error, reload } = window.useApi(
    () => window.API.getPortfolio(year, priceMode === 'live'),
    [year, priceMode]
  );

  const assets     = portfolio?.assets ?? [];
  const totalValue = parseFloat(portfolio?.total_market_value_nok ?? '0') * fxRate;
  const totalLots  = assets.reduce((s, a) => s + (a.lot_count || 0), 0);

  const subtitle = portfolio
    ? `${portfolio.asset_count} assets · ${totalLots} lots · ${ccy} ${_fmtFull(totalValue)} total · ${priceMode === 'live' ? 'live prices' : `year-end ${year}`}`
    : loading ? 'Loading…' : 'Portfolio data unavailable';

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Portfolio · Holdings</div>
          <div className="page-sub">{subtitle}</div>
        </div>
        <div className="row" style={{gap: 8}}>
          <div className="tabs">
            <button className={priceMode === 'yearend' ? 'active' : ''} onClick={() => setPriceMode('yearend')}>
              Year-end
            </button>
            <button className={priceMode === 'live' ? 'active' : ''} onClick={() => setPriceMode('live')}>
              Live · 5m cache
            </button>
          </div>
          <button className="btn" onClick={reload}><Icon name="refresh" size={12}/></button>
          <button className="btn" onClick={exportCsv}><Icon name="download" size={12}/> Export CSV</button>
        </div>
      </div>

      {error && (
        <div className="card" style={{padding: '16px 20px', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text-muted)'}}>
          <Icon name="alert" size={15}/>
          <span style={{flex: 1, fontSize: 13}}>{error}</span>
          <button className="btn sm" onClick={reload}>Retry</button>
        </div>
      )}

      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th style={{width: 28}}></th>
              <th>Asset</th>
              <th className="num">Lots</th>
              <th className="num">Quantity</th>
              <th className="num">Avg cost</th>
              <th className="num">Price</th>
              <th></th>
              <th className="num">Market value</th>
              <th className="num">Unrealised P&L</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading && assets.length === 0 && (
              <tr>
                <td colSpan={10} style={{textAlign: 'center', color: 'var(--text-faint)', padding: '36px 0'}}>
                  Loading portfolio…
                </td>
              </tr>
            )}

            {assets.map((h, i) => {
              const qty     = parseFloat(h.total_quantity        ?? '0');
              const avgCost = h.avg_cost_nok_per_unit ? parseFloat(h.avg_cost_nok_per_unit) : null;
              const price   = h.price_nok             ? parseFloat(h.price_nok)             : null;
              const value   = parseFloat(h.market_value_nok  ?? '0') * fxRate;
              const pnl     = parseFloat(h.unrealized_gain_nok ?? '0') * fxRate;
              const color   = _assetColor(h.asset);
              const isExp   = expanded === h.asset;
              const seed    = i * 13 + 7;

              return (
                <React.Fragment key={h.asset}>
                  <tr
                    className={isExp ? 'expanded' : ''}
                    onClick={() => setExpanded(isExp ? null : h.asset)}
                    style={{cursor: 'pointer'}}
                  >
                    <td><Icon name={isExp ? 'chevron_down' : 'chevron_right'} size={11}/></td>

                    <td>
                      <div className="row" style={{gap: 8}}>
                        <span style={{
                          width: 18, height: 18, borderRadius: 4, background: color,
                          color: 'white', fontSize: 9, fontWeight: 700,
                          display: 'grid', placeItems: 'center',
                        }}>
                          {h.asset.slice(0, 1)}
                        </span>
                        <span style={{fontWeight: 500}}>{h.asset}</span>
                        {h.has_missing_basis && <span className="tag amber">Missing basis</span>}
                        {h.has_missing_price  && <span className="tag red">Missing price</span>}
                      </div>
                    </td>

                    <td className="num">{h.lot_count}</td>

                    <td className="num">
                      {qty < 1
                        ? qty.toFixed(6)
                        : qty.toLocaleString('en-US', {maximumFractionDigits: 2})}
                    </td>

                    <td className="num muted">
                      {avgCost != null
                        ? (avgCost * fxRate).toLocaleString('en-US', {maximumFractionDigits: 2})
                        : '—'}
                    </td>

                    <td className="num">
                      {price != null
                        ? (price * fxRate).toLocaleString('en-US', {maximumFractionDigits: price < 10 ? 4 : 0})
                        : '—'}
                    </td>

                    <td>
                      <Sparkline data={D.sparkData(seed, 18, 0.05)} width={64} height={20} color="auto"/>
                    </td>

                    <td className="num" style={{fontWeight: 500}}>{_fmtFull(value)}</td>

                    <td className={`num ${pnl >= 0 ? 'pos' : 'neg'}`}>
                      {avgCost != null ? `${pnl >= 0 ? '+' : ''}${_fmtFull(pnl)}` : '—'}
                    </td>

                    <td style={{textAlign: 'right'}}>
                      <button className="btn ghost sm"><Icon name="more" size={11}/></button>
                    </td>
                  </tr>

                  {isExp && (
                    <tr className="lot-detail">
                      <td></td>
                      <td colSpan={9}>
                        <div style={{padding: '6px 0 4px'}}>
                          <div style={{fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8}}>
                            Carry-forward lots · {h.lot_count} · {h.asset}
                          </div>
                          <div style={{fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.7}}>
                            <span style={{fontWeight: 500}}>Total quantity:</span>{' '}
                            {qty < 1 ? qty.toFixed(8) : qty.toLocaleString()} {h.asset}
                            {avgCost != null && (
                              <>{' · '}<span style={{fontWeight: 500}}>Avg cost basis:</span>{' '}
                              {(avgCost * fxRate).toLocaleString('en-US', {maximumFractionDigits: 4})} {ccy}</>
                            )}
                            {' · '}<span style={{fontWeight: 500}}>Market value:</span>{' '}
                            {_fmtFull(value)} {ccy}
                            {h.has_missing_basis && (
                              <span className="tag amber" style={{marginLeft: 8}}>⚠ some lots missing basis</span>
                            )}
                          </div>
                          <div style={{marginTop: 8, fontSize: 11, color: 'var(--text-faint)'}}>
                            Individual lot breakdown available via the Lots API · /lots/{year}/{h.asset}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// Charts page
// ─────────────────────────────────────────────────────────────────────────────
const Charts = ({ juris }) => {
  const D      = window.DEMO;
  const ccy    = juris === 'NO' ? 'NOK' : 'GBP';
  const fxRate = juris === 'NO' ? 1 : 0.078;

  // Use last completed tax year — current year rarely has lot snapshots yet.
  const chartYear = new Date().getFullYear() - 1;

  // Pull year-end portfolio for P&L charts (year-end prices, not live)
  const { data: portfolio, error: portfolioError } = window.useApi(
    () => window.API.getPortfolio(chartYear, false),
    []
  );
  const { data: diags } = window.useApi(() => window.API.getDiagnostics(), []);
  const hasPrices = !!(diags?.prices?.prices_db?.exists || (diags?.prices?.combined_csvs?.length ?? 0) > 0);

  // ── Time-series: synthetic shape scaled to real portfolio total ──────────
  const totalNOK   = parseFloat(portfolio?.total_market_value_nok ?? '0');
  const tsScale    = (totalNOK > 0 ? totalNOK : 930_000) * fxRate;
  const tsRaw      = D.timeSeries(7, 90, 0.003, 0.022);
  const tsLast     = tsRaw[tsRaw.length - 1] || 1;
  const tsData     = tsRaw.map(v => (v / tsLast) * tsScale);

  // ── Waterfall: real unrealised P&L per asset ─────────────────────────────
  const wfData = (portfolio?.assets ?? [])
    .filter(a => parseFloat(a.unrealized_gain_nok ?? '0') !== 0)
    .slice(0, 8)
    .map(a => ({ label: a.asset, value: parseFloat(a.unrealized_gain_nok) * fxRate }));

  // ── Lot cost vs market: real price / avg_cost ratio ───────────────────────
  const lotBars = (portfolio?.assets ?? [])
    .filter(a => a.avg_cost_nok_per_unit && a.price_nok)
    .slice(0, 5)
    .map(a => ({
      sym: a.asset,
      pct: (parseFloat(a.price_nok) / parseFloat(a.avg_cost_nok_per_unit) - 1) * 100,
    }));

  // ── Synthetic OHLC candles (no historical price API) ─────────────────────
  // Start ~12% below current portfolio value (assumes ~12% growth over 60 days)
  const candles = (() => {
    let v = tsScale * 0.88, r = 11;
    const out = [];
    for (let i = 0; i < 60; i++) {
      r = (r * 9301 + 49297) % 233280;
      const noise = (r / 233280 - 0.5) * 0.05;
      const o = v;
      v = v * (1 + 0.002 + noise);
      const c = v;
      const r2 = (r * 1234 + 5678) % 9999;
      out.push({
        o,
        h: Math.max(o, c) * (1 + (r2 % 100) / 7000),
        l: Math.min(o, c) * (1 - (r2 % 100) / 7000),
        c,
      });
    }
    return out;
  })();

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Charts</div>
          <div className="page-sub">Portfolio analytics · {chartYear} · unrealised P&amp;L · cost vs market</div>
        </div>
      </div>

      {/* ── No-prices banner ── */}
      {!hasPrices && diags && (
        <div style={{
          padding: '12px 16px', marginBottom: 14,
          background: 'oklch(0.32 0.08 75 / 0.5)',
          border: '1px solid oklch(0.55 0.12 75 / 0.4)',
          borderRadius: 10, display: 'flex', alignItems: 'center', gap: 10,
          color: 'oklch(0.88 0.08 75)',
        }}>
          <Icon name="alerts" size={16}/>
          <div style={{flex: 1}}>
            <div style={{fontWeight: 600, fontSize: 13}}>No price data — P&amp;L charts will be empty</div>
            <div style={{fontSize: 12, opacity: 0.85}}>
              Go to <strong>Prices</strong> and click <strong>Collect prices now</strong> to populate prices.db, then re-run your tax job.
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-12" style={{marginBottom: 16}}>
        <div style={{gridColumn: 'span 8'}}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">
                Portfolio value · 90 days <span className="meta">· {ccy} · {chartYear}</span>
              </div>
              <div className="tabs">
                <button>NOK</button>
                <button className="active">{ccy}</button>
              </div>
            </div>
            <div style={{padding: 12}}>
              {totalNOK > 0
                ? <AreaChart data={tsData} width={800} height={260} color="var(--accent)" currency={ccy}/>
                : <div style={{height: 260, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: 'var(--text-faint)', gap: 8}}>
                    <Icon name="charts" size={28}/>
                    <div style={{fontSize: 13}}>No portfolio value for {chartYear}</div>
                    <div style={{fontSize: 11.5}}>Run a tax job for {chartYear} first</div>
                  </div>
              }
            </div>
          </div>
        </div>

        <div style={{gridColumn: 'span 4'}}>
          <div className="card" style={{height: '100%'}}>
            <div className="card-header">
              <div className="card-title">Unrealised P&amp;L · {chartYear}</div>
            </div>
            <div style={{padding: 12}}>
              {wfData.length > 0
                ? <Waterfall data={wfData} width={420} height={260}/>
                : <div style={{height: 260, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: 'var(--text-faint)', gap: 8, textAlign: 'center', padding: '0 16px'}}>
                    <Icon name="prices" size={24}/>
                    <div style={{fontSize: 13}}>No P&amp;L data</div>
                    <div style={{fontSize: 11.5}}>
                      {!hasPrices
                        ? 'Collect prices first, then re-run tax job'
                        : 'Re-run tax job for ' + chartYear}
                    </div>
                  </div>
              }
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-12">
        <div style={{gridColumn: 'span 8'}}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">
                Portfolio · {ccy} <span className="meta">· 60d synthetic</span>
              </div>
              <div className="row" style={{gap: 8}}>
                <label className="row" style={{gap: 6, fontSize: 12, color: 'var(--text-muted)'}}>
                  <span className="toggle on"/> Bot fills
                </label>
                <div className="tabs">
                  <button>1H</button><button>4H</button>
                  <button className="active">1D</button><button>1W</button>
                </div>
              </div>
            </div>
            <div style={{padding: 12}}>
              <Candlestick data={candles} width={800} height={280}/>
            </div>
          </div>
        </div>

        <div style={{gridColumn: 'span 4'}}>
          <div className="card">
            <div className="card-header"><div className="card-title">Lot cost vs market</div></div>
            <div className="card-body">
              <div style={{display: 'flex', flexDirection: 'column', gap: 10}}>
                {lotBars.map((b, i) => (
                  <div key={b.sym || i}>
                    <div className="row" style={{justifyContent: 'space-between', fontSize: 11.5, marginBottom: 4}}>
                      <span style={{fontWeight: 500}}>{b.sym}</span>
                      <span className={`num ${b.pct >= 0 ? 'pos' : 'neg'}`}
                            style={{color: b.pct >= 0 ? 'var(--green)' : 'var(--red)'}}>
                        {b.pct >= 0 ? '+' : ''}{b.pct.toFixed(1)}%
                      </span>
                    </div>
                    <div className="bar" style={{height: 6}}>
                      <div style={{
                        width: Math.min(100, Math.abs(b.pct) + 30) + '%',
                        background: b.pct >= 0 ? 'var(--green)' : 'var(--red)',
                      }}/>
                    </div>
                  </div>
                ))}
                {lotBars.length === 0 && (
                  <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, padding: '20px 8px', textAlign: 'center', color: 'var(--text-faint)'}}>
                    <Icon name="prices" size={20}/>
                    <div style={{fontSize: 12}}>No cost/market data</div>
                    <div style={{fontSize: 11}}>
                      {!hasPrices
                        ? 'Collect prices → re-run tax job'
                        : 'Assets need both cost basis and market price'}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

window.Prices    = Prices;
window.Portfolio = Portfolio;
window.Charts    = Charts;
