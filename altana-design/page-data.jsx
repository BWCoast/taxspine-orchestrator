// ALTANA — Pages: Prices, Portfolio, Charts

const Prices = ({ juris, addToast }) => {
  const D = window.DEMO;
  const ccy = juris === 'NO' ? 'NOK' : 'GBP';
  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Prices & Valuation</div>
          <div className="page-sub">prices.db is the active source for all jobs · last collect 12m ago</div>
        </div>
        <button className="btn primary" onClick={() => addToast({title: 'Price collect started', msg: '142 symbols queued'})}>
          <Icon name="refresh" size={12}/> Collect prices now
        </button>
      </div>

      <div className="grid grid-3" style={{marginBottom: 16}}>
        <div className="card">
          <div className="card-header"><div className="card-title">prices.db health</div><span className="tag green"><span className="dot green"/> Healthy</span></div>
          <div className="card-body">
            <div className="kpi-value" style={{fontSize: 18}}>184,200 <span style={{fontSize: 12, color: 'var(--text-faint)'}}>rows</span></div>
            <div className="kpi-sub">4.2 MB · 142 symbols · 28d depth</div>
            <div style={{marginTop: 10, fontSize: 11.5, color: 'var(--text-muted)'}}>Freshness: <span className="num">12m</span> ago</div>
            <div className="bar" style={{marginTop: 4}}><div style={{width: '92%'}}/></div>
          </div>
        </div>
        <div className="card">
          <div className="card-header"><div className="card-title">Last collect</div></div>
          <div className="card-body">
            <div className="row" style={{gap: 10}}>
              <span className="num pos" style={{fontSize: 18, fontWeight: 600, color: 'var(--green)'}}>142</span>
              <span style={{fontSize: 12, color: 'var(--text-muted)'}}>fetched</span>
            </div>
            <div className="row" style={{gap: 16, marginTop: 6, fontSize: 12, color: 'var(--text-muted)'}}>
              <span><span className="num">8</span> skipped</span>
              <span><span className="num">0</span> failed</span>
            </div>
            <div style={{marginTop: 10, fontSize: 11, color: 'var(--text-faint)'}}>Sources: Kraken Ticker × Norges Bank · 5-min cache</div>
          </div>
        </div>
        <div className="card">
          <div className="card-header"><div className="card-title">Active mode</div></div>
          <div className="card-body">
            <div style={{fontSize: 14, fontWeight: 500}}>prices.db</div>
            <div className="kpi-sub">Used by all jobs run after Apr 2024</div>
            <div className="row" style={{marginTop: 12, gap: 6}}>
              <button className="btn sm">Switch to CSV</button>
              <button className="btn ghost sm">Manual entry</button>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-12" style={{marginBottom: 16}}>
        <div style={{gridColumn: 'span 7'}}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">Price CSVs <span className="meta">· combined_{ccy.toLowerCase()}_*.csv</span></div>
              <button className="btn sm"><Icon name="upload" size={11}/> Import</button>
            </div>
            <table className="tbl">
              <thead><tr><th>Year</th><th className="num">Rows</th><th className="num">Size</th><th>Last modified</th><th></th></tr></thead>
              <tbody>
                {[2024, 2023, 2022, 2021].map(y => (
                  <tr key={y}>
                    <td className="num" style={{fontWeight: 500}}>{y}</td>
                    <td className="num">{(48000 + y * 12).toLocaleString()}</td>
                    <td className="num muted">{(1.2 + (2024 - y) * 0.4).toFixed(1)} MB</td>
                    <td className="muted">2024-04-12</td>
                    <td style={{textAlign: 'right'}}><button className="btn ghost sm"><Icon name="download" size={11}/></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div style={{gridColumn: 'span 5'}}>
          <div className="card">
            <div className="card-header"><div className="card-title">XRPL asset registry</div><button className="btn sm"><Icon name="plus" size={11}/> Add</button></div>
            <table className="tbl">
              <thead><tr><th>Symbol</th><th>Issuer</th><th></th></tr></thead>
              <tbody>
                {[
                  ['SOLO', 'rsoLo2…3vN8'],
                  ['CSC', 'rCSC9…ar9q'],
                  ['ELS', 'rELS5…hYx2'],
                  ['CORE', 'rcoreNywa…vU'],
                  ['XAH', 'rXAH4…kk2c'],
                ].map(([s, i]) => (
                  <tr key={s}>
                    <td style={{fontWeight: 500}}>{s}</td>
                    <td className="mono muted" style={{fontSize: 11.5}}>{i}</td>
                    <td style={{textAlign: 'right'}}><button className="btn ghost sm"><Icon name="x" size={10}/></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
};

const Portfolio = ({ juris, year }) => {
  const D = window.DEMO;
  const ccy = juris === 'NO' ? 'NOK' : 'GBP';
  const fxRate = juris === 'NO' ? 1 : 0.078;
  const [expanded, setExpanded] = React.useState(null);
  const [priceMode, setPriceMode] = React.useState('live');

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Portfolio · Holdings</div>
          <div className="page-sub">10 assets · 84 lots · {ccy} {fmtFull(D.totalValue * fxRate)} total · year-end {year}</div>
        </div>
        <div className="row" style={{gap: 8}}>
          <div className="tabs">
            <button className={priceMode === 'yearend' ? 'active' : ''} onClick={() => setPriceMode('yearend')}>Year-end</button>
            <button className={priceMode === 'live' ? 'active' : ''} onClick={() => setPriceMode('live')}>Live · 5m cache</button>
          </div>
          <button className="btn"><Icon name="download" size={12}/> Export CSV</button>
        </div>
      </div>

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
            {D.HOLDINGS.map((h, i) => {
              const value = h.value * fxRate;
              const cost = h.cost * fxRate;
              const pnl = h.unrealised * fxRate;
              const isExp = expanded === h.sym;
              const sparkSeed = i * 13 + 7;
              return (
                <React.Fragment key={h.sym}>
                  <tr className={isExp ? 'expanded' : ''} onClick={() => setExpanded(isExp ? null : h.sym)} style={{cursor: 'pointer'}}>
                    <td><Icon name={isExp ? 'chevron_down' : 'chevron_right'} size={11}/></td>
                    <td>
                      <div className="row" style={{gap: 8}}>
                        <span style={{width: 18, height: 18, borderRadius: 4, background: h.color, color: 'white', fontSize: 9, fontWeight: 700, display: 'grid', placeItems: 'center'}}>{h.sym.slice(0,1)}</span>
                        <span style={{fontWeight: 500}}>{h.sym}</span>
                        <span className="muted" style={{fontSize: 11}}>{h.name}</span>
                        {h.missingBasis && <span className="tag amber">Missing basis</span>}
                      </div>
                    </td>
                    <td className="num">{h.lots}</td>
                    <td className="num">{h.qty < 1 ? h.qty.toFixed(4) : h.qty.toLocaleString('en-US', {maximumFractionDigits: 2})}</td>
                    <td className="num muted">{h.avgCost ? (h.avgCost * fxRate).toLocaleString('en-US', {maximumFractionDigits: 2}) : '—'}</td>
                    <td className="num">{(h.price * fxRate).toLocaleString('en-US', {maximumFractionDigits: h.price < 10 ? 2 : 0})}</td>
                    <td><Sparkline data={D.sparkData(sparkSeed, 18, 0.05)} width={64} height={20} color="auto"/></td>
                    <td className="num" style={{fontWeight: 500}}>{fmtFull(value)}</td>
                    <td className={`num ${pnl >= 0 ? 'pos' : 'neg'}`}>{pnl >= 0 ? '+' : ''}{h.avgCost ? fmtFull(pnl) : '—'}</td>
                    <td style={{textAlign: 'right'}}><button className="btn ghost sm"><Icon name="more" size={11}/></button></td>
                  </tr>
                  {isExp && (
                    <tr className="lot-detail">
                      <td></td>
                      <td colSpan={9}>
                        <div style={{padding: '4px 0'}}>
                          <div style={{fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8}}>Carry-forward lots · {h.lots}</div>
                          <table className="tbl" style={{background: 'transparent'}}>
                            <thead><tr><th>Acquired</th><th className="num">Qty</th><th className="num">Cost basis ({ccy})</th><th>Origin event</th><th>Source</th></tr></thead>
                            <tbody>
                              {Array.from({length: Math.min(h.lots, 4)}).map((_, j) => {
                                const days = (h.lots - j) * 38;
                                const date = new Date(2024, 0, 1); date.setDate(date.getDate() - days);
                                return (
                                  <tr key={j}>
                                    <td className="mono muted">{date.toISOString().split('T')[0]}</td>
                                    <td className="num">{(h.qty / h.lots).toLocaleString('en-US', {maximumFractionDigits: 4})}</td>
                                    <td className="num">{h.avgCost ? fmtFull(h.qty / h.lots * h.avgCost * fxRate) : <span className="tag amber">missing</span>}</td>
                                    <td className="muted">{['Buy', 'Trade in', 'Reward', 'Buy'][j % 4]}</td>
                                    <td className="muted">{['Kraken', 'Coinbase', 'Bot', 'Firi'][j % 4]}</td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
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

const Charts = ({ juris }) => {
  const D = window.DEMO;
  const ccy = juris === 'NO' ? 'NOK' : 'GBP';
  const fxRate = juris === 'NO' ? 1 : 0.078;
  const tsData = D.timeSeries(7, 90, 0.003, 0.022).map(v => v * fxRate);
  const wfData = D.HOLDINGS.filter(h => h.unrealised !== 0).slice(0, 8).map(h => ({label: h.sym, value: h.unrealised * fxRate}));

  // OHLC candle data
  const candles = (() => {
    let v = 642000 * fxRate, r = 11;
    const out = [];
    for (let i = 0; i < 60; i++) {
      r = (r * 9301 + 49297) % 233280;
      const noise = (r / 233280 - 0.5) * 0.05;
      const o = v;
      v = v * (1 + 0.002 + noise);
      const c = v;
      const h = Math.max(o, c) * (1 + Math.random() * 0.015);
      const l = Math.min(o, c) * (1 - Math.random() * 0.015);
      out.push({o, h, l, c});
    }
    return out;
  })();

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Charts</div>
          <div className="page-sub">Portfolio analytics · price history · bot fill overlays</div>
        </div>
      </div>

      <div className="grid grid-12" style={{marginBottom: 16}}>
        <div style={{gridColumn: 'span 8'}}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">Portfolio value · 90 days <span className="meta">· {ccy}</span></div>
              <div className="tabs">
                <button>NOK</button>
                <button className="active">{ccy}</button>
              </div>
            </div>
            <div style={{padding: 12}}>
              <AreaChart data={tsData} width={800} height={260} color="var(--accent)" currency={ccy}/>
            </div>
          </div>
        </div>
        <div style={{gridColumn: 'span 4'}}>
          <div className="card" style={{height: '100%'}}>
            <div className="card-header"><div className="card-title">Asset P&L · 2024</div></div>
            <div style={{padding: 12}}>
              <Waterfall data={wfData} width={420} height={260}/>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-12">
        <div style={{gridColumn: 'span 8'}}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">BTC · {ccy} <span className="meta">· 60d candles</span></div>
              <div className="row" style={{gap: 8}}>
                <label className="row" style={{gap: 6, fontSize: 12, color: 'var(--text-muted)'}}>
                  <span className="toggle on"/> Bot fills
                </label>
                <div className="tabs">
                  <button>1H</button><button>4H</button><button className="active">1D</button><button>1W</button>
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
                {D.HOLDINGS.slice(0, 5).map((h, i) => {
                  const ratio = h.avgCost ? (h.price / h.avgCost) : 1;
                  const pct = (ratio - 1) * 100;
                  return (
                    <div key={h.sym}>
                      <div className="row" style={{justifyContent: 'space-between', fontSize: 11.5, marginBottom: 4}}>
                        <span style={{fontWeight: 500}}>{h.sym}</span>
                        <span className={`num ${pct >= 0 ? 'pos' : 'neg'}`} style={{color: pct >= 0 ? 'var(--green)' : 'var(--red)'}}>
                          {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                        </span>
                      </div>
                      <div className="bar" style={{height: 6}}>
                        <div style={{width: Math.min(100, Math.abs(pct) + 30) + '%', background: pct >= 0 ? 'var(--green)' : 'var(--red)'}}/>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

window.Prices = Prices;
window.Portfolio = Portfolio;
window.Charts = Charts;
