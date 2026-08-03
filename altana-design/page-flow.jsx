// ALTANA — Pages: Sources, Tax Center, Review Queue

const Sources = ({ addToast }) => {
  const D = window.DEMO;
  const [tab, setTab] = React.useState('apis');
  const [drag, setDrag] = React.useState(false);
  const statusTag = (s) => {
    if (s === 'synced') return <span className="tag green"><span className="dot green"/> Synced</span>;
    if (s === 'syncing') return <span className="tag blue"><span className="dot blue"/> Syncing…</span>;
    return <span className="tag red"><span className="dot red"/> Error</span>;
  };
  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Data Sources</div>
          <div className="page-sub">9 connected · 18,420 events ingested · 32 dedup collisions to review</div>
        </div>
        <div className="row" style={{gap: 8}}>
          <button className="btn"><Icon name="refresh" size={12}/> Sync all</button>
          <button className="btn primary"><Icon name="plus" size={12}/> Add source</button>
        </div>
      </div>

      <div className="grid grid-12" style={{marginBottom: 16}}>
        <div style={{gridColumn: 'span 8'}}>
          <div className="card">
            <div className="card-header">
              <div className="tabs">
                <button className={tab === 'apis' ? 'active' : ''} onClick={() => setTab('apis')}>Exchange APIs</button>
                <button className={tab === 'wallets' ? 'active' : ''} onClick={() => setTab('wallets')}>XRPL Wallets</button>
                <button className={tab === 'uploads' ? 'active' : ''} onClick={() => setTab('uploads')}>Uploads</button>
                <button className={tab === 'bot' ? 'active' : ''} onClick={() => setTab('bot')}>Bot (DuckDB)</button>
              </div>
              <button className="btn sm"><Icon name="plus" size={11}/> Add</button>
            </div>
            {tab === 'apis' && (
              <table className="tbl">
                <thead><tr><th>Exchange</th><th>Status</th><th>Last sync</th><th className="num">Events</th><th></th></tr></thead>
                <tbody>
                  {D.SOURCES.filter(s => s.kind === 'API').map(s => (
                    <tr key={s.id}>
                      <td style={{fontWeight: 500}}>{s.name}</td>
                      <td>{statusTag(s.status)}</td>
                      <td className="muted">{s.last}</td>
                      <td className="num">{s.count.toLocaleString()}</td>
                      <td style={{textAlign: 'right'}}>
                        <button className="btn ghost sm" onClick={() => addToast({title: `${s.name} sync queued`})}><Icon name="refresh" size={11}/></button>
                        <button className="btn ghost sm"><Icon name="more" size={11}/></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {tab === 'wallets' && (
              <table className="tbl">
                <thead><tr><th>Address</th><th>Label</th><th>Last ledger</th><th className="num">IOUs</th><th></th></tr></thead>
                <tbody>
                  {D.SOURCES.filter(s => s.kind === 'Wallet').map(s => (
                    <tr key={s.id}>
                      <td className="mono" style={{fontSize: 11.5}}>{s.name}</td>
                      <td className="muted">Primary</td>
                      <td className="muted">{s.last}</td>
                      <td className="num">{s.count}</td>
                      <td style={{textAlign: 'right'}}><button className="btn ghost sm"><Icon name="refresh" size={11}/></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {tab === 'uploads' && (
              <table className="tbl">
                <thead><tr><th>Filename</th><th>Source</th><th className="num">Rows</th><th>Uploaded</th><th>Dedup (ok / skip)</th></tr></thead>
                <tbody>
                  {D.UPLOADS.map((u, i) => (
                    <tr key={i}>
                      <td className="mono" style={{fontSize: 11.5}}>{u.name}</td>
                      <td><span className="tag">{u.source}</span></td>
                      <td className="num">{u.rows.toLocaleString()}</td>
                      <td className="muted">{u.time}</td>
                      <td className="num muted">{u.dedup}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {tab === 'bot' && (
              <div className="card-body">
                <div style={{display: 'flex', gap: 12, marginBottom: 12}}>
                  <div className="kpi" style={{flex: 1, padding: 12}}>
                    <div className="kpi-label">BOT_DB path</div>
                    <div className="mono" style={{fontSize: 12, marginTop: 6}}>~/.ALTANA/bot/****.duckdb</div>
                  </div>
                  <div className="kpi" style={{flex: 1, padding: 12}}>
                    <div className="kpi-label">Last sync</div>
                    <div className="kpi-value" style={{fontSize: 14, marginTop: 4}}>4,218 events</div>
                    <div className="kpi-sub"><span className="dot green"/> 8m ago · 0 errors</div>
                  </div>
                </div>
                <div style={{fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', margin: '8px 0 6px'}}>Token whitelist</div>
                <div className="row" style={{flexWrap: 'wrap', gap: 6}}>
                  {['XRP', 'SOLO.rsoLo2…', 'CSC.rCSC…', 'XAH', 'ELS.rELS…', 'CORE.rCORE…'].map(t => (
                    <span key={t} className="tag green">{t}</span>
                  ))}
                  <button className="btn sm"><Icon name="plus" size={10}/> Add</button>
                </div>
              </div>
            )}
          </div>
        </div>

        <div style={{gridColumn: 'span 4'}}>
          <div className="card">
            <div className="card-header"><div className="card-title">Upload CSV / XLSX</div></div>
            <div className="card-body">
              <div className={`dz ${drag ? 'on' : ''}`}
                   onDragOver={e => { e.preventDefault(); setDrag(true); }}
                   onDragLeave={() => setDrag(false)}
                   onDrop={e => { e.preventDefault(); setDrag(false); addToast({title: 'File queued', msg: 'Detecting source type…'}); }}>
                <Icon name="upload" size={20}/>
                <div className="dz-title" style={{marginTop: 8}}>Drop CSV / XLSX here</div>
                <div style={{fontSize: 11.5}}>or click to browse · auto-detects source type</div>
              </div>
              <div style={{marginTop: 14}}>
                <div style={{fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6}}>Source type</div>
                <select style={{width: '100%', padding: 6, borderRadius: 7, background: 'var(--bg-deep)', border: '1px solid var(--border)', color: 'var(--text)'}}>
                  <option>Auto-detect</option><option>Coinbase</option><option>Firi</option><option>Kraken</option>
                  <option>NBX</option><option>Uphold</option><option>Bybit</option><option>Generic Events</option>
                </select>
              </div>
              <button className="btn ghost sm" style={{marginTop: 12, width: '100%'}}>
                <Icon name="download" size={11}/> Download Generic Events template
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="card-title">Supported chains & readers <span className="meta">· {D.CHAINS.length} chains</span></div>
        </div>
        <div className="card-body">
          <div className="grid" style={{gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10}}>
            {D.CHAINS.map(c => (
              <div key={c.name} className="tile">
                <div style={{display: 'flex', alignItems: 'center', gap: 8}}>
                  <div className="tile-icon">{c.sym.slice(0,2)}</div>
                  <div style={{flex: 1}}>
                    <div className="tile-name">{c.name}</div>
                    <div className="tile-meta">{c.events}</div>
                  </div>
                </div>
                <div style={{marginTop: 4}}>
                  {c.status === 'supported' && <span className="tag green"><span className="dot green"/> Supported</span>}
                  {c.status === 'beta' && <span className="tag blue"><span className="dot blue"/> Beta</span>}
                  {c.status === 'soon' && <span className="tag">Coming soon</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
};

const TaxCenter = ({ juris, year, addToast }) => {
  const D = window.DEMO;
  const [running, setRunning] = React.useState(false);
  const [progress, setProgress] = React.useState(0);
  const [completed, setCompleted] = React.useState(false);
  const [valuation, setValuation] = React.useState('db');
  const [pipeline, setPipeline] = React.useState(juris === 'NO' ? 'multi' : 'per-file');
  const [dryRun, setDryRun] = React.useState(false);
  const [sources, setSources] = React.useState({ kraken: true, coinbase: true, firi: true, bot: true, manual: false });

  const runJob = () => {
    setRunning(true); setCompleted(false); setProgress(0);
    let p = 0;
    const tick = setInterval(() => {
      p += 8 + Math.random() * 12;
      if (p >= 100) { p = 100; setProgress(100); clearInterval(tick); setRunning(false); setCompleted(true); addToast({title: 'Tax job completed', msg: '412 disposals · 0 errors'}); }
      else setProgress(p);
    }, 220);
  };

  const ccy = juris === 'NO' ? 'NOK' : 'GBP';
  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Tax Center</div>
          <div className="page-sub">Run jobs, download outputs, audit prior runs · {juris === 'NO' ? 'Norway · RF-1159' : 'United Kingdom · SA100/SA108'}</div>
        </div>
      </div>

      <div className="grid grid-12" style={{marginBottom: 16}}>
        <div style={{gridColumn: 'span 7'}}>
          <div className="card">
            <div className="card-header"><div className="card-title">Run a tax job</div>{dryRun && <span className="tag amber">Dry run</span>}</div>
            <div className="card-body">
              <div className="grid grid-2" style={{gap: 12, marginBottom: 14}}>
                <div>
                  <div style={{fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6}}>Country</div>
                  <div className="row" style={{gap: 6}}>
                    <button className={`btn ${juris === 'NO' ? 'primary' : ''}`} style={{flex: 1, justifyContent: 'center'}}>🇳🇴 Norway</button>
                    <button className={`btn ${juris === 'UK' ? 'primary' : ''}`} style={{flex: 1, justifyContent: 'center'}}>🇬🇧 United Kingdom</button>
                  </div>
                </div>
                <div>
                  <div style={{fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6}}>Tax year</div>
                  <select style={{width: '100%', padding: 6, borderRadius: 7, background: 'var(--bg-deep)', border: '1px solid var(--border)', color: 'var(--text)'}}>
                    <option>{year}</option><option>{year - 1}</option><option>{year - 2}</option>
                  </select>
                </div>
              </div>

              <div style={{marginBottom: 14}}>
                <div style={{fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6}}>Valuation mode</div>
                <div className="row" style={{gap: 6}}>
                  {['dummy', 'csv', 'db'].map(m => (
                    <button key={m} className={`btn ${valuation === m ? 'primary' : ''}`} onClick={() => setValuation(m)} style={{flex: 1, justifyContent: 'center'}}>
                      {m === 'dummy' ? 'Dummy' : m === 'csv' ? 'Price Table' : 'prices.db'}
                    </button>
                  ))}
                </div>
                {valuation === 'db' && (
                  <div className="row" style={{marginTop: 8, fontSize: 11.5, color: 'var(--text-muted)', gap: 6}}>
                    <span className="dot green"/> prices.db · 184k rows · 12m ago
                    <button className="btn ghost sm" style={{marginLeft: 'auto'}} onClick={() => addToast({title: 'Price collect started'})}><Icon name="refresh" size={11}/> Collect</button>
                  </div>
                )}
              </div>

              {juris === 'NO' && (
                <div style={{marginBottom: 14}}>
                  <div style={{fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6}}>Pipeline mode <span className="tip" data-tip="NOR_MULTI batches all sources for FIFO single-pool consistency"><Icon name="info" size={11}/></span></div>
                  <div className="row" style={{gap: 6}}>
                    <button className={`btn ${pipeline === 'per-file' ? 'primary' : ''}`} onClick={() => setPipeline('per-file')} style={{flex: 1, justifyContent: 'center'}}>Per-file</button>
                    <button className={`btn ${pipeline === 'multi' ? 'primary' : ''}`} onClick={() => setPipeline('multi')} style={{flex: 1, justifyContent: 'center'}}>NOR_MULTI</button>
                  </div>
                </div>
              )}

              <div style={{marginBottom: 14}}>
                <div style={{fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6}}>Sources</div>
                <div style={{display: 'flex', flexDirection: 'column', gap: 4}}>
                  {Object.entries({kraken: 'Kraken API', coinbase: 'Coinbase API', firi: 'Firi API', bot: 'BOT_DB (DuckDB)', manual: 'manual-events-2024.csv'}).map(([k, label]) => (
                    <label key={k} style={{display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5, padding: '4px 0', cursor: 'pointer'}}>
                      <input type="checkbox" checked={sources[k]} onChange={e => setSources({...sources, [k]: e.target.checked})}/>
                      {label}
                    </label>
                  ))}
                </div>
              </div>

              <div className="row" style={{justifyContent: 'space-between', borderTop: '1px solid var(--border)', paddingTop: 14}}>
                <label style={{display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5, cursor: 'pointer'}}>
                  <span className={`toggle ${dryRun ? 'on' : ''}`} onClick={() => setDryRun(!dryRun)}/>
                  Dry-run (no files written)
                </label>
                <button className="btn primary" onClick={runJob} disabled={running}>
                  <Icon name="play" size={12}/> {running ? 'Running…' : 'Run job'}
                </button>
              </div>

              {(running || completed) && (
                <div style={{marginTop: 14, padding: 12, background: 'var(--bg-deep)', borderRadius: 8, border: '1px solid var(--border)'}}>
                  <div className="row" style={{justifyContent: 'space-between', marginBottom: 6}}>
                    <span style={{fontSize: 12, fontWeight: 500}}>{completed ? '✓ Completed' : 'Running pipeline…'}</span>
                    <span className="num" style={{fontSize: 11, color: 'var(--text-muted)'}}>{Math.round(progress)}%</span>
                  </div>
                  <div className="bar"><div style={{width: progress + '%'}}/></div>
                  {completed && (
                    <div style={{marginTop: 10, fontSize: 12, color: 'var(--text-muted)'}}>
                      412 disposals · total gain {ccy} {fmtFull(412800)} · 0 unresolved · 0 errors
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        <div style={{gridColumn: 'span 5'}}>
          {completed && (
            <div className="card" style={{marginBottom: 16}}>
              <div className="card-header"><div className="card-title">Output files</div><span className="tag green"><Icon name="check" size={10}/> Ready</span></div>
              <div className="card-body" style={{display: 'flex', flexDirection: 'column', gap: 6}}>
                {['Gains CSV', 'Wealth CSV', 'Summary JSON', juris === 'NO' ? 'RF-1159 JSON' : 'SA108 JSON', 'HTML Report', 'Review JSON'].map(f => (
                  <div key={f} className="row" style={{padding: '6px 10px', background: 'var(--bg-deep)', borderRadius: 7, border: '1px solid var(--border)'}}>
                    <Icon name="file"/>
                    <span style={{flex: 1, fontSize: 12.5}}>{f}</span>
                    <button className="btn ghost sm"><Icon name="download" size={11}/></button>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="card">
            <div className="card-header"><div className="card-title">Job history</div></div>
            <table className="tbl">
              <thead><tr><th>Year</th><th>Country</th><th>Status</th><th>Run</th><th className="num">Duration</th></tr></thead>
              <tbody>
                {D.JOBS.map(j => (
                  <tr key={j.id}>
                    <td className="num">{j.year}</td>
                    <td>{j.country === 'NO' ? '🇳🇴 NO' : '🇬🇧 UK'}</td>
                    <td>{j.status === 'completed' ? <span className="tag green">Completed</span> : <span className="tag red">Failed</span>}</td>
                    <td className="muted" style={{fontSize: 11}}>{j.date.split(' ')[0]}</td>
                    <td className="num muted">{j.dur}</td>
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

const ReviewQueue = ({ juris, addToast }) => {
  const D = window.DEMO;
  const [tab, setTab] = React.useState('unlinked');
  const sevTag = (s) => s === 'high' ? <span className="tag amber">High</span> : s === 'critical' ? <span className="tag red">Critical</span> : <span className="tag blue">Info</span>;
  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Review Queue</div>
          <div className="page-sub">Resolve before filing · all items must be reviewed for {juris === 'NO' ? 'RF-1159' : 'SA108'}</div>
        </div>
        <button className="btn"><Icon name="check" size={12}/> Mark all as reviewed</button>
      </div>

      <div style={{padding: '12px 16px', background: 'oklch(0.92 0.10 75 / 0.4)', border: '1px solid oklch(0.78 0.12 75 / 0.4)', borderRadius: 10, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 10, color: 'oklch(0.42 0.14 75)'}}>
        <Icon name="alerts" size={18}/>
        <div style={{flex: 1}}>
          <div style={{fontWeight: 600, fontSize: 13}}>5 items need your attention before filing</div>
          <div style={{fontSize: 12, opacity: 0.8}}>3 unlinked transfers · 2 missing-basis assets · 3 warnings (1 LP event, 12 bot fills, 1 airdrop)</div>
        </div>
        <button className="btn">View all</button>
      </div>

      <div className="grid grid-4" style={{marginBottom: 16}}>
        <KPI label="Unlinked transfers" value="3" sub="oldest: 2024-02-14" subKind="" sparkSeed={5}/>
        <KPI label="Missing basis" value="2" sub="DAG, CSPR" subKind="neg" sparkSeed={6}/>
        <KPI label="Warnings" value="3" sub="1 high · 2 info" subKind="" sparkSeed={7}/>
        <KPI label="Unresolved disposals" value="0" sub="all clear" subKind="pos" sparkSeed={8}/>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="tabs">
            <button className={tab === 'unlinked' ? 'active' : ''} onClick={() => setTab('unlinked')}>Unlinked transfers · 3</button>
            <button className={tab === 'missing' ? 'active' : ''} onClick={() => setTab('missing')}>Missing basis · 2</button>
            <button className={tab === 'warnings' ? 'active' : ''} onClick={() => setTab('warnings')}>Warnings · 3</button>
          </div>
        </div>
        {tab === 'unlinked' && (
          <table className="tbl">
            <thead><tr><th>Time</th><th>Asset</th><th className="num">Amount</th><th>Source</th><th>Destination</th><th></th></tr></thead>
            <tbody>
              {D.REVIEW.unlinked.map((u, i) => (
                <tr key={i}>
                  <td className="muted mono" style={{fontSize: 11}}>{u.time}</td>
                  <td style={{fontWeight: 500}}>{u.asset}</td>
                  <td className="num">{u.amount}</td>
                  <td className="muted">{u.src}</td>
                  <td><span className="tag red"><span className="dot red"/> Unknown</span></td>
                  <td style={{textAlign: 'right'}}>
                    <button className="btn sm" onClick={() => addToast({title: 'Linked as personal transfer'})}><Icon name="link" size={11}/> Link</button>
                    <button className="btn ghost sm">Mark personal</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === 'missing' && (
          <table className="tbl">
            <thead><tr><th>Asset</th><th className="num">Lots</th><th className="num">Total qty</th><th>Oldest</th><th></th></tr></thead>
            <tbody>
              {D.REVIEW.missing.map((m, i) => (
                <tr key={i}>
                  <td style={{fontWeight: 500}}>{m.asset}</td>
                  <td className="num">{m.lots}</td>
                  <td className="num">{m.qty.toLocaleString()}</td>
                  <td className="muted">{m.oldest}</td>
                  <td style={{textAlign: 'right'}}><button className="btn sm">Enter cost basis</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === 'warnings' && (
          <div style={{padding: 0}}>
            {D.REVIEW.warnings.map((w, i) => (
              <div key={i} style={{padding: '12px 16px', borderBottom: i < D.REVIEW.warnings.length - 1 ? '1px solid var(--border)' : 'none'}}>
                <div className="row" style={{gap: 10}}>
                  {sevTag(w.sev)}
                  <span className="tag">{w.cat}</span>
                  <span style={{flex: 1, fontSize: 13, fontWeight: 500}}>{w.msg}</span>
                  <button className="btn sm">Resolve</button>
                </div>
                <div style={{fontSize: 12, color: 'var(--text-muted)', marginTop: 6, paddingLeft: 4}}>{w.detail}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
};

window.Sources = Sources;
window.TaxCenter = TaxCenter;
window.ReviewQueue = ReviewQueue;
