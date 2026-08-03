// ALTANA — Page 1: Dashboard / Home

const KPI = ({ label, value, sub, subKind, sparkSeed, currency }) => {
  const data = window.DEMO.sparkData(sparkSeed, 24, 0.04);
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{currency && <span style={{fontSize: 13, color: 'var(--text-faint)', marginRight: 4}}>{currency}</span>}{value}</div>
      {sub && <div className="kpi-sub"><span className={subKind}>{sub}</span></div>}
      <div className="spark"><Sparkline data={data} width={86} height={28} color="auto" fill={true}/></div>
    </div>
  );
};

const fmtNum = (n) => Math.abs(n) >= 1e6 ? (n/1e6).toFixed(2) + 'M' : Math.abs(n) >= 1e3 ? (n/1e3).toLocaleString('en-US', {maximumFractionDigits: 1}) + 'k' : n.toFixed(0);
const fmtFull = (n) => Math.round(n).toLocaleString('en-US').replace(/,/g, ' ');

const Dashboard = ({ juris, year, setYear, addToast }) => {
  const D = window.DEMO;
  const ccy = juris === 'NO' ? 'NOK' : 'GBP';
  const fxRate = juris === 'NO' ? 1 : 0.078; // demo conversion
  const totalGains = 412800 * fxRate;
  const unrealised = D.totalUnrealised * fxRate;
  const taxEstimate = 142500 * fxRate;
  const tsData = D.timeSeries(42, 90, 0.003, 0.022).map(v => v * fxRate);

  const allocation = D.HOLDINGS.slice(0, 6).map(h => ({
    label: h.sym,
    value: h.value,
    color: h.color,
  }));
  const totalAlloc = allocation.reduce((s, a) => s + a.value, 0);

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Good morning, Henrik</div>
          <div className="page-sub">Tax year overview · last sync 2 minutes ago · 14,202 lots tracked</div>
        </div>
        <div className="row" style={{gap: 8}}>
          <div className="year-pill">
            <button className="arrow" onClick={() => setYear(year - 1)}><Icon name="chevron_left" size={12}/></button>
            <span>{year}</span>
            <button className="arrow" onClick={() => setYear(year + 1)} disabled={year >= 2024}><Icon name="chevron_right" size={12}/></button>
          </div>
          <button className="btn primary" onClick={() => addToast({ title: 'Tax job queued', msg: `${juris} · ${year} · running…` })}>
            <Icon name="play" size={12}/> Run tax job
          </button>
        </div>
      </div>

      <div className="grid grid-4" style={{marginBottom: 16}}>
        <KPI label="Total gains" value={fmtFull(totalGains)} sub="↑ 18.4% vs prior year" subKind="pos" sparkSeed={1} currency={ccy}/>
        <KPI label="Unrealised P&L" value={fmtFull(unrealised)} sub={unrealised >= 0 ? "↑ portfolio in profit" : "↓ portfolio in loss"} subKind={unrealised >= 0 ? "pos" : "neg"} sparkSeed={2} currency={ccy}/>
        <KPI label="Assets held" value="10" sub="2 missing basis · 1 newly added" subKind="" sparkSeed={3} currency=""/>
        <KPI label="Tax estimate" value={fmtFull(taxEstimate)} sub={`${juris === 'NO' ? '22%' : '20%'} effective · pending review`} subKind="" sparkSeed={4} currency={ccy}/>
      </div>

      <div className="grid grid-12" style={{marginBottom: 16}}>
        <div style={{gridColumn: 'span 8'}}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">Portfolio value · 90 days
                <span className="meta">· {ccy}</span>
              </div>
              <div className="card-actions">
                <div className="tabs">
                  <button className="active">90D</button>
                  <button>180D</button>
                  <button>1Y</button>
                  <button>All</button>
                </div>
              </div>
            </div>
            <div style={{padding: 12}}>
              <AreaChart data={tsData} width={800} height={220} color="var(--accent)" currency={ccy}/>
            </div>
          </div>
        </div>

        <div style={{gridColumn: 'span 4'}}>
          <div className="card" style={{height: '100%'}}>
            <div className="card-header">
              <div className="card-title">Allocation</div>
              <span className="tag">{D.HOLDINGS.length} assets</span>
            </div>
            <div style={{padding: 16, display: 'flex', alignItems: 'center', gap: 16}}>
              <div style={{position: 'relative', width: 140, height: 140, flexShrink: 0}}>
                <Donut data={allocation} size={140} thickness={16}/>
                <div style={{position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', textAlign: 'center'}}>
                  <div>
                    <div style={{fontSize: 10, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em'}}>Total</div>
                    <div className="num" style={{fontSize: 14, fontWeight: 600}}>{fmtNum(D.totalValue * fxRate)}</div>
                    <div style={{fontSize: 10, color: 'var(--text-muted)'}}>{ccy}</div>
                  </div>
                </div>
              </div>
              <div style={{flex: 1, display: 'flex', flexDirection: 'column', gap: 6}}>
                {allocation.map(a => (
                  <div key={a.label} style={{display: 'flex', alignItems: 'center', gap: 8, fontSize: 11.5}}>
                    <span style={{width: 8, height: 8, borderRadius: 2, background: a.color}}/>
                    <span style={{flex: 1, fontWeight: 500}}>{a.label}</span>
                    <span className="num" style={{color: 'var(--text-muted)'}}>{((a.value / totalAlloc) * 100).toFixed(1)}%</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-12">
        <div style={{gridColumn: 'span 5'}}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">Recent activity</div>
              <button className="btn ghost sm">View all</button>
            </div>
            <div className="card-body" style={{padding: 0}}>
              {D.ACTIVITY.map((a, i) => (
                <div key={i} style={{display: 'flex', gap: 12, padding: '10px 16px', borderBottom: i < D.ACTIVITY.length - 1 ? '1px solid var(--border)' : 'none', alignItems: 'center'}}>
                  <span className="num" style={{fontSize: 11, color: 'var(--text-faint)', width: 38}}>{a.time}</span>
                  <span className="tag">{a.who}</span>
                  <span style={{fontSize: 12.5, flex: 1}}>{a.text}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div style={{gridColumn: 'span 4'}}>
          <div className="card">
            <div className="card-header"><div className="card-title">Filing checklist · {juris} {year}</div></div>
            <div className="card-body">
              <ul className="checklist">
                <li className="done"><span className="chk"><Icon name="check" size={10}/></span> Jurisdiction set</li>
                <li className="done"><span className="chk"><Icon name="check" size={10}/></span> 9 sources connected</li>
                <li className="done"><span className="chk"><Icon name="check" size={10}/></span> Prices collected (12m ago)</li>
                <li><span className="chk"/> Resolve 2 missing-basis assets</li>
                <li><span className="chk"/> Confirm 12 bot FMV entries</li>
                <li><span className="chk"/> Generate {juris === 'NO' ? 'RF-1159' : 'SA108'} report</li>
              </ul>
              <div style={{marginTop: 12}}>
                <div style={{display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-muted)', marginBottom: 4}}>
                  <span>Filing readiness</span><span className="num">50%</span>
                </div>
                <div className="bar amber"><div style={{width: '50%'}}/></div>
              </div>
            </div>
          </div>
        </div>

        <div style={{gridColumn: 'span 3'}}>
          <div className="card">
            <div className="card-header"><div className="card-title">System health</div></div>
            <div className="card-body" style={{display: 'flex', flexDirection: 'column', gap: 8}}>
              {D.DIAGNOSTICS.slice(0, 6).map(d => (
                <div key={d.name} style={{display: 'flex', alignItems: 'center', gap: 8, fontSize: 12}}>
                  <span className={`dot ${d.status}`}/>
                  <span style={{flex: 1}}>{d.name}</span>
                  <span className="num" style={{color: 'var(--text-faint)', fontSize: 10.5}}>{d.last}</span>
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
window.fmtNum = fmtNum;
window.fmtFull = fmtFull;
