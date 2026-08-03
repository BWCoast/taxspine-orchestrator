// ALTANA — Pages: Audit, Diagnostics, Alerts, Settings

const Audit = () => {
  const D = window.DEMO;
  const [filter, setFilter] = React.useState('all');
  const filtered = filter === 'all' ? D.AUDIT : D.AUDIT.filter(a => a.type.startsWith(filter));
  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Audit Log</div>
          <div className="page-sub">{D.AUDIT.length} events · 30-day retention · cryptographically chained</div>
        </div>
        <button className="btn"><Icon name="download" size={12}/> Export CSV</button>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="row" style={{gap: 8}}>
            <select value={filter} onChange={e => setFilter(e.target.value)}
                    style={{padding: '4px 8px', borderRadius: 6, background: 'var(--bg-deep)', border: '1px solid var(--border)', color: 'var(--text)', fontSize: 12}}>
              <option value="all">All event types</option>
              <option value="job">job.*</option>
              <option value="sync">sync.*</option>
              <option value="upload">upload</option>
              <option value="bot">bot.*</option>
              <option value="review">review.*</option>
              <option value="prices">prices.*</option>
            </select>
            <select style={{padding: '4px 8px', borderRadius: 6, background: 'var(--bg-deep)', border: '1px solid var(--border)', color: 'var(--text)', fontSize: 12}}>
              <option>All actors</option><option>manual</option><option>scheduler</option><option>orchestrator</option>
            </select>
            <input placeholder="Search…" style={{padding: '4px 10px', borderRadius: 6, background: 'var(--bg-deep)', border: '1px solid var(--border)', color: 'var(--text)', fontSize: 12, width: 200}}/>
          </div>
        </div>
        <table className="tbl">
          <thead><tr><th>Time</th><th>Type</th><th>Actor</th><th>Summary</th><th>Job</th></tr></thead>
          <tbody>
            {filtered.map((a, i) => (
              <tr key={i}>
                <td className="mono muted" style={{fontSize: 11}}>{a.time}</td>
                <td><span className="code">{a.type}</span></td>
                <td><span className="tag">{a.actor}</span></td>
                <td>{a.summary}</td>
                <td className="mono muted" style={{fontSize: 11}}>{a.job}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
};

const Diagnostics = () => {
  const D = window.DEMO;
  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">System Diagnostics</div>
          <div className="page-sub">Last refreshed 8 seconds ago · auto-refresh on</div>
        </div>
        <div className="row" style={{gap: 8}}>
          <span className="tag green"><span className="dot green"/> Healthy</span>
          <button className="btn"><Icon name="refresh" size={12}/> Refresh</button>
        </div>
      </div>

      <div className="grid grid-4" style={{marginBottom: 16}}>
        {D.DIAGNOSTICS.map(d => (
          <div key={d.name} className="card">
            <div className="card-body">
              <div className="row" style={{justifyContent: 'space-between', marginBottom: 6}}>
                <span style={{fontSize: 12, fontWeight: 500}}>{d.name}</span>
                <span className={`dot ${d.status}`}/>
              </div>
              <div className="num" style={{fontSize: 13}}>{d.detail}</div>
              <div style={{fontSize: 11, color: 'var(--text-faint)', marginTop: 4}}>{d.last}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-2">
        <div className="card">
          <div className="card-header"><div className="card-title">Disk usage</div></div>
          <div className="card-body">
            {[
              {label: 'DATA_DIR', used: 2.4, total: 32},
              {label: 'PRICES_DIR', used: 1.2, total: 16},
              {label: 'OUTPUT_DIR', used: 0.6, total: 16},
            ].map(d => {
              const pct = (d.used / d.total) * 100;
              return (
                <div key={d.label} style={{marginBottom: 12}}>
                  <div className="row" style={{justifyContent: 'space-between', fontSize: 12, marginBottom: 4}}>
                    <span className="mono">{d.label}</span>
                    <span className="num muted">{d.used} / {d.total} GB</span>
                  </div>
                  <div className="bar"><div style={{width: pct + '%'}}/></div>
                </div>
              );
            })}
          </div>
        </div>
        <div className="card">
          <div className="card-header"><div className="card-title">CLI binary resolution · SEC-17</div></div>
          <table className="tbl">
            <thead><tr><th>Binary</th><th>Path</th><th>Status</th></tr></thead>
            <tbody>
              {[
                ['ALTANA-engine', '/usr/local/bin/ALTANA-engine', 'green'],
                ['xrpl-reader', '/usr/local/bin/xrpl-reader', 'green'],
                ['bot-sync', '/opt/ALTANA/bin/bot-sync', 'green'],
                ['report-html', '/usr/local/bin/report-html', 'green'],
              ].map(([n, p, s]) => (
                <tr key={n}>
                  <td className="mono">{n}</td>
                  <td className="mono muted" style={{fontSize: 11}}>{p}</td>
                  <td><span className={`dot ${s}`}/> <span style={{fontSize: 11.5}}>OK</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
};

const Alerts = ({ addToast }) => {
  const D = window.DEMO;
  const [showDismissed, setShowDismissed] = React.useState(false);
  const sevTag = (s) => s === 'critical' ? <span className="tag red">Critical</span>
                       : s === 'high' ? <span className="tag amber">High</span>
                       : <span className="tag blue">Info</span>;
  const grouped = {
    Health: D.ALERTS.filter(a => a.cat === 'Health'),
    Review: D.ALERTS.filter(a => a.cat === 'Review'),
    'Lot quality': D.ALERTS.filter(a => a.cat === 'Lot quality'),
  };
  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Alerts Center</div>
          <div className="page-sub">{D.ALERTS.length} active alerts · grouped by category</div>
        </div>
        <div className="row" style={{gap: 8}}>
          <label className="row" style={{gap: 6, fontSize: 12, color: 'var(--text-muted)'}}>
            <span className={`toggle ${showDismissed ? 'on' : ''}`} onClick={() => setShowDismissed(!showDismissed)}/>
            Show dismissed
          </label>
          <button className="btn">Bulk dismiss</button>
        </div>
      </div>

      {Object.entries(grouped).map(([cat, list]) => list.length > 0 && (
        <div key={cat} className="card" style={{marginBottom: 14}}>
          <div className="card-header"><div className="card-title">{cat} · {list.length}</div></div>
          <div className="card-body" style={{padding: 0}}>
            {list.map((a, i) => (
              <div key={i} style={{padding: '12px 16px', borderBottom: i < list.length - 1 ? '1px solid var(--border)' : 'none'}}>
                <div className="row" style={{gap: 10}}>
                  {sevTag(a.sev)}
                  <span style={{flex: 1, fontSize: 13, fontWeight: 500}}>{a.msg}</span>
                  <span className="num muted" style={{fontSize: 11}}>{a.when}</span>
                  <button className="btn ghost sm" onClick={() => addToast({title: 'Acknowledged', msg: a.msg})}><Icon name="check" size={11}/> Ack</button>
                  <button className="btn ghost sm"><Icon name="x" size={11}/></button>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </>
  );
};

const Settings = ({ juris, setJuris }) => {
  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Settings · Workspace</div>
          <div className="page-sub">h.jansen / Nordic Growth · 3 wallets · 6 APIs</div>
        </div>
      </div>

      <div className="grid grid-12">
        <div style={{gridColumn: 'span 8'}}>
          <div className="card" style={{marginBottom: 14}}>
            <div className="card-header"><div className="card-title">Jurisdiction & output</div></div>
            <div className="card-body" style={{display: 'flex', flexDirection: 'column', gap: 14}}>
              <div>
                <div style={{fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6}}>Primary country</div>
                <div className="row" style={{gap: 6}}>
                  <button className={`btn ${juris === 'NO' ? 'primary' : ''}`} style={{flex: 1, justifyContent: 'center'}} onClick={() => setJuris('NO')}>🇳🇴 Norway · NOK</button>
                  <button className={`btn ${juris === 'UK' ? 'primary' : ''}`} style={{flex: 1, justifyContent: 'center'}} onClick={() => setJuris('UK')}>🇬🇧 United Kingdom · GBP</button>
                </div>
              </div>
              <div>
                <div style={{fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6}}>Tax year display</div>
                <div className="row" style={{gap: 6}}>
                  <button className={`btn ${juris === 'NO' ? 'primary' : ''}`} style={{flex: 1, justifyContent: 'center'}}>Calendar (Jan – Dec)</button>
                  <button className={`btn ${juris === 'UK' ? 'primary' : ''}`} style={{flex: 1, justifyContent: 'center'}}>UK (6 Apr – 5 Apr)</button>
                </div>
              </div>
              <div>
                <div style={{fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6}}>Default valuation mode</div>
                <select style={{width: '100%', padding: 6, borderRadius: 7, background: 'var(--bg-deep)', border: '1px solid var(--border)', color: 'var(--text)'}}>
                  <option>prices.db (recommended)</option><option>Price Table CSV</option><option>Dummy</option>
                </select>
              </div>
            </div>
          </div>

          <div className="card" style={{marginBottom: 14}}>
            <div className="card-header"><div className="card-title">XRPL accounts</div><button className="btn sm"><Icon name="plus" size={11}/> Add</button></div>
            <table className="tbl">
              <thead><tr><th>Address</th><th>Label</th><th>Added</th><th></th></tr></thead>
              <tbody>
                {[
                  ['rN7n7otQDd6FczFgLdSqtcsAUxDkw6fzRH', 'Primary'],
                  ['rPVMhWBsfF9iMXYj3aAzJVkPDTFNSyWdKy', 'Cold storage'],
                  ['rhWct2fv4Yc9NrVMXyZ32NmFB1qPSyY1Ej', 'DEX trading'],
                ].map(([a, l]) => (
                  <tr key={a}>
                    <td className="mono" style={{fontSize: 11.5}}>{a}</td>
                    <td>{l}</td>
                    <td className="muted">2024-01-12</td>
                    <td style={{textAlign: 'right'}}><button className="btn ghost sm"><Icon name="x" size={10}/></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <div className="card-header"><div className="card-title">Advanced</div></div>
            <div className="card-body" style={{display: 'flex', flexDirection: 'column', gap: 12}}>
              <div className="row" style={{justifyContent: 'space-between'}}>
                <div>
                  <div style={{fontSize: 12.5, fontWeight: 500}}>Subprocess timeout</div>
                  <div className="muted" style={{fontSize: 11.5}}>How long pipeline workers may run</div>
                </div>
                <div className="row" style={{gap: 6}}>
                  <input type="number" defaultValue={300} style={{width: 80, padding: 4, borderRadius: 6, background: 'var(--bg-deep)', border: '1px solid var(--border)', color: 'var(--text)', fontSize: 12, textAlign: 'right'}}/>
                  <span className="muted" style={{fontSize: 12}}>seconds</span>
                </div>
              </div>
              <div className="row" style={{justifyContent: 'space-between'}}>
                <div>
                  <div style={{fontSize: 12.5, fontWeight: 500}}>Dedup directory</div>
                  <div className="mono muted" style={{fontSize: 11}}>~/.ALTANA/dedup/</div>
                </div>
                <button className="btn ghost sm">Open in Finder</button>
              </div>
            </div>
          </div>
        </div>

        <div style={{gridColumn: 'span 4'}}>
          <div className="card" style={{marginBottom: 14}}>
            <div className="card-header"><div className="card-title">Security</div></div>
            <div className="card-body" style={{display: 'flex', flexDirection: 'column', gap: 10}}>
              <div className="row" style={{justifyContent: 'space-between', fontSize: 12.5}}>
                <span>ORCHESTRATOR_KEY</span>
                <span className="tag green">Set</span>
              </div>
              <div className="row" style={{justifyContent: 'space-between', fontSize: 12.5}}>
                <span>REQUIRE_AUTH</span>
                <span className="toggle on"/>
              </div>
              <div>
                <div style={{fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6}}>Allowed CORS origins</div>
                <div className="mono" style={{fontSize: 11, color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', gap: 2}}>
                  <div>https://ALTANA.app</div>
                  <div>https://*.ALTANA.app</div>
                  <div>http://localhost:5173</div>
                </div>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-header"><div className="card-title">About</div></div>
            <div className="card-body" style={{fontSize: 12.5, color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', gap: 6}}>
              <div className="row" style={{justifyContent: 'space-between'}}><span>Version</span><span className="mono">v0.18.4</span></div>
              <div className="row" style={{justifyContent: 'space-between'}}><span>Engine</span><span className="mono">ALTANA-engine 2.4</span></div>
              <div className="row" style={{justifyContent: 'space-between'}}><span>Build</span><span className="mono">a8f3c91</span></div>
              <div className="row" style={{justifyContent: 'space-between'}}><span>Region</span><span>EU-North-1</span></div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

window.Audit = Audit;
window.Diagnostics = Diagnostics;
window.Alerts = Alerts;
window.Settings = Settings;
