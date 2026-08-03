// ALTANA — Pages: Audit, Diagnostics, Alerts, Settings

const Audit = ({ jobs }) => {
  // jobs — shared from App (10 s polling). No local fetch needed.
  const [filter, setFilter] = React.useState('all');

  // Map jobs to audit-like entries
  const entries = jobs
    ? jobs.map(j => ({
        time:    j.created_at?.replace('T', ' ').slice(0, 19) || '—',
        type:    j.status === 'completed' ? 'job.complete'
               : j.status === 'failed'   ? 'job.failed'
               : j.status === 'running'  ? 'job.running'
               : 'job.pending',
        actor:   'orchestrator',
        summary: `${j.input?.country}-${j.input?.tax_year} · ${j.status}${j.input?.case_name ? ' · ' + j.input.case_name : ''}`,
        job:     j.id,
      }))
    : [];

  const filtered = filter === 'all' ? entries : entries.filter(e => e.type.startsWith(filter));

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Audit Log</div>
          <div className="page-sub">{entries.length} events · job execution history</div>
        </div>
        <button className="btn" onClick={() => {
          if (!entries.length) return;
          const header = 'time,type,actor,summary,job_id';
          const rows = entries.map(e =>
            [e.time, e.type, e.actor, `"${e.summary.replace(/"/g,'""')}"`, e.job || ''].join(',')
          );
          const blob = new Blob([header + '\n' + rows.join('\n')], { type: 'text/csv' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a'); a.href = url;
          a.download = `audit_log_${new Date().toISOString().slice(0,10)}.csv`;
          document.body.appendChild(a); a.click(); a.remove();
          URL.revokeObjectURL(url);
        }}><Icon name="download" size={12}/> Export CSV</button>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="row" style={{ gap: 8 }}>
            <select value={filter} onChange={e => setFilter(e.target.value)}
                    style={{ padding: '4px 8px', borderRadius: 6, background: 'var(--bg-deep)', border: '1px solid var(--border)', color: 'var(--text)', fontSize: 12 }}>
              <option value="all">All event types</option>
              <option value="job">job.*</option>
            </select>
          </div>
        </div>
        <table className="tbl">
          <thead>
            <tr><th>Time</th><th>Type</th><th>Actor</th><th>Summary</th><th>Job ID</th></tr>
          </thead>
          <tbody>
            {!jobs && <tr><td colSpan={5} style={{ padding: '12px 16px', color: 'var(--text-faint)', fontSize: 12 }}>Loading…</td></tr>}
            {filtered.map((a, i) => (
              <tr key={i}>
                <td className="mono muted" style={{ fontSize: 11 }}>{a.time}</td>
                <td><span className="code">{a.type}</span></td>
                <td><span className="tag">{a.actor}</span></td>
                <td>{a.summary}</td>
                <td className="mono muted" style={{ fontSize: 11 }}>{a.job?.slice(0, 12) || '—'}</td>
              </tr>
            ))}
            {jobs && filtered.length === 0 && (
              <tr><td colSpan={5} style={{ padding: '12px 16px', color: 'var(--text-faint)', fontSize: 12 }}>No entries</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
};


// ── Diagnostics ───────────────────────────────────────────────────────────────
const Diagnostics = ({ diags, diagsErr, reloadDiags, pollError, sharedWorkspace }) => {
  // diags, diagsErr, reloadDiags, pollError, sharedWorkspace — shared from App (10 s polling).
  // sharedWorkspace is workspace-scoped; diags.workspace.* is for the DEFAULT workspace only.
  const loading = !diags && !diagsErr;
  const error   = diagsErr;
  const reload  = reloadDiags;

  const overall = !diags ? null
    : (diags.lots?.error || diags.prices?.error || diags.jobs?.error) ? 'degraded'
    : 'healthy';

  // Extract workspace counts here so the Workspace card can apply proper pluralization.
  const _wsWallets = sharedWorkspace?.xrpl_accounts?.length ?? 0;
  const _wsCsvs    = sharedWorkspace?.csv_files?.length     ?? 0;
  const _wsAssets  = sharedWorkspace?.xrpl_assets?.length   ?? 0;

  // Build diagnostic cards from live data
  const cards = diags ? [
    {
      name:   'Lot store',
      status: diags.lots?.db_exists ? 'green' : (diags.lots?.error ? 'red' : 'amber'),
      detail: diags.lots?.db_exists
        ? `${diags.lots.years?.length ?? 0} year(s) · ${diags.lots.size_kb ?? '?'} KB`
        : diags.lots?.error || 'no data',
    },
    {
      name:   'Price CSV cache',
      status: (diags.prices?.combined_csvs?.length ?? 0) > 0 ? 'green' : 'amber',
      detail: diags.prices?.combined_csvs?.length
        ? `${diags.prices.combined_csvs.length} file(s) · newest ${diags.prices.combined_csvs[0]?.age_hours}h old`
        : 'empty',
    },
    {
      name:   'prices.db',
      status: diags.prices?.prices_db?.exists ? 'green' : 'amber',
      detail: diags.prices?.prices_db?.exists
        ? `${(diags.prices.prices_db.row_count ?? 0).toLocaleString()} rows · ${diags.prices.prices_db.size_kb ?? '?'} KB`
        : 'not found',
    },
    {
      // "global" label: /diagnostics is server-wide, not workspace-scoped.
      // Tax Center job history shows only this workspace's jobs — different scope.
      name:   'Jobs (global)',
      status: (diags.jobs?.running ?? 0) > 0 ? 'amber'
            : (diags.jobs?.failed  ?? 0) > 0 ? 'amber' : 'green',
      detail: `${diags.jobs?.total ?? 0} total · ${diags.jobs?.running ?? 0} running · ${diags.jobs?.failed ?? 0} failed`,
    },
    {
      name:   'Dedup store',
      status: (diags.dedup?.total_skips ?? 0) > 500 ? 'amber' : 'green',
      detail: `${diags.dedup?.source_count ?? 0} source(s) · ${diags.dedup?.total_skips ?? 0} skips`,
    },
    {
      // Use sharedWorkspace (active workspace-scoped) not diags.workspace (default workspace only).
      name:   'Workspace',
      status: (_wsWallets + _wsCsvs) > 0 ? 'green' : 'amber',
      detail: `${_wsWallets} wallet${_wsWallets !== 1 ? 's' : ''} · ${_wsCsvs} CSV${_wsCsvs !== 1 ? 's' : ''} · ${_wsAssets} XRPL asset${_wsAssets !== 1 ? 's' : ''}`,
    },
    {
      name:   'Output dir',
      status: diags.disk_usage?.output_dir?.exists ? 'green' : 'amber',
      detail: diags.disk_usage?.output_dir?.exists
        ? `${diags.disk_usage.output_dir.size_mb ?? 0} MB · ${diags.disk_usage.output_dir.file_count ?? 0} files`
        : 'not found',
    },
    {
      name:   'Prices dir',
      status: diags.disk_usage?.prices_dir?.exists ? 'green' : 'amber',
      detail: diags.disk_usage?.prices_dir?.exists
        ? `${diags.disk_usage.prices_dir.size_mb ?? 0} MB · ${diags.disk_usage.prices_dir.file_count ?? 0} files`
        : 'not found',
    },
  ] : [];

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">System Diagnostics</div>
          <div className="page-sub">{loading ? 'Loading…' : error ? 'API error — see below' : 'Live snapshot'}</div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          {overall === 'healthy'  && <span className="tag green"><span className="dot green"/> Healthy</span>}
          {overall === 'degraded' && <span className="tag amber"><span className="dot amber"/> Degraded</span>}
          {!overall && !loading && !error && <span className="tag red"><span className="dot red"/> Error</span>}
          {error && <span className="tag red"><span className="dot red"/> API error</span>}
          <button className="btn" onClick={reload}><Icon name="refresh" size={12}/> Refresh</button>
        </div>
      </div>

      {error && (
        <div style={{
          padding: '12px 16px', marginBottom: 14,
          background: 'oklch(0.25 0.08 15 / 0.6)',
          border: '1px solid oklch(0.45 0.18 15 / 0.5)',
          borderRadius: 8, fontSize: 12.5, color: 'var(--text-muted)',
        }}>
          <div style={{ fontWeight: 600, marginBottom: 4, color: 'var(--red)' }}>
            Could not reach /diagnostics
          </div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--text-faint)', wordBreak: 'break-all' }}>
            {error}
          </div>
          <div style={{ marginTop: 8, fontSize: 11.5 }}>
            Check that the orchestrator is running and your API key is set in Settings.
          </div>
        </div>
      )}

      {pollError && !error && (
        <div style={{
          padding: '12px 16px', marginBottom: 14,
          background: 'oklch(0.25 0.08 15 / 0.4)',
          border: '1px solid oklch(0.45 0.18 15 / 0.3)',
          borderRadius: 8, fontSize: 12.5, color: 'var(--text-muted)',
        }}>
          <div style={{ fontWeight: 600, marginBottom: 4, color: 'var(--amber)', display: 'flex', alignItems: 'center', gap: 6 }}>
            <Icon name="alerts" size={13}/> Background poll error
          </div>
          <div>
            One or more shared signals (diagnostics, workspace, jobs, or review) failed their most
            recent 10 s refresh. The bell icon in the header turns red when this happens.
            Data shown across all pages may be stale until connectivity is restored.
          </div>
          <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--text-faint)' }}>
            Common causes: orchestrator restarted, network hiccup, API key expired.
            The platform retries automatically every 10 seconds.
          </div>
        </div>
      )}

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        {loading && [1,2,3,4,5,6,7,8].map(n => (
          <div key={n} className="card">
            <div className="card-body">
              <div style={{ height: 60, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-faint)', fontSize: 12 }}>Loading…</div>
            </div>
          </div>
        ))}
        {cards.map(d => (
          <div key={d.name} className="card">
            <div className="card-body">
              <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
                <span style={{ fontSize: 12, fontWeight: 500 }}>{d.name}</span>
                <span className={`dot ${d.status}`}/>
              </div>
              <div className="num" style={{ fontSize: 12 }}>{d.detail}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Disk usage bars */}
      <div className="grid grid-2">
        <div className="card">
          <div className="card-header"><div className="card-title">Disk usage</div></div>
          <div className="card-body">
            {diags && [
              { label: 'OUTPUT_DIR',  info: diags.disk_usage?.output_dir },
              { label: 'PRICES_DIR',  info: diags.disk_usage?.prices_dir },
              { label: 'UPLOAD_DIR',  info: diags.disk_usage?.upload_dir },
            ].map(({ label, info }) => {
              const usedMB   = info?.size_mb ?? 0;
              const totalMB  = info?.total_mb ?? null;   // backend may supply total
              const totalGB  = totalMB != null ? totalMB / 1024 : (info?.total_gb ?? 32);
              const usedGB   = usedMB / 1024;
              const pct      = Math.min(100, (usedGB / totalGB) * 100);
              // Show MB when < 1 GB, GB otherwise — never mislabel MB as GB.
              // toFixed(1) keeps the bar label consistent with the top card's exact value.
              const usedLabel = usedMB < 1024
                ? `${usedMB.toFixed(1)} MB`
                : `${usedGB.toFixed(2)} GB`;
              const barColor = pct > 90 ? 'var(--red)' : pct > 70 ? 'var(--amber)' : undefined;
              return (
                <div key={label} style={{ marginBottom: 12 }}>
                  <div className="row" style={{ justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                    <span className="mono">{label}</span>
                    <span className="num muted">{usedLabel} / {totalGB} GB</span>
                  </div>
                  <div className="bar">
                    <div style={{ width: pct + '%', ...(barColor ? { background: barColor } : {}) }}/>
                  </div>
                </div>
              );
            })}
            {loading && <div style={{ color: 'var(--text-faint)', fontSize: 12 }}>Loading…</div>}
          </div>
        </div>

        <div className="card">
          <div className="card-header"><div className="card-title">Lot years available</div></div>
          <div className="card-body">
            {diags?.lots?.years?.length > 0 ? (
              <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
                {diags.lots.years.map(y => (
                  <span key={y} className="tag green">{y}</span>
                ))}
              </div>
            ) : (
              <div style={{ color: 'var(--text-faint)', fontSize: 12 }}>
                {loading ? 'Loading…' : 'No lot snapshots found — run a tax job first'}
              </div>
            )}
            {diags?.lots?.db_exists && (
              <div style={{ marginTop: 12, fontSize: 11, color: 'var(--text-faint)' }}>
                {diags.lots.size_kb} KB · {diags.lots.years?.length ?? 0} year(s) stored
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
};


// ── Alerts ────────────────────────────────────────────────────────────────────
const Alerts = ({ addToast, sharedJobs }) => {
  // sharedJobs — shared from App (10 s polling). Used to filter alerts by workspace.
  const { data: alertsRaw, loading, error: alertsErr, reload } = window.useApi(
    () => window.API.getAlerts(30), [], { pollInterval: 10000 }
  );
  // Use sharedJobs (limit 50) from App instead of a local fetch (was limit 200).
  // Workspace-scope: filter review alerts whose job_id is not in this workspace.
  const wsJobIds = React.useMemo(() => new Set((sharedJobs || []).map(j => j.id)), [sharedJobs]);
  const alerts = React.useMemo(() => {
    if (!alertsRaw) return alertsRaw; // keep null/undefined as-is (loading state)
    return alertsRaw.filter(a => {
      // Health alerts are always workspace-global — always show.
      if (a.category === 'health') return true;
      // Review/lot_quality alerts with no job_id are informational — always show.
      if (!a.job_id) return true;
      // Review alerts: only show if the job belongs to this workspace.
      // If sharedJobs hasn't loaded yet, show all (avoid flash of empty state).
      if (!sharedJobs) return true;
      return wsJobIds.has(a.job_id);
    });
  }, [alertsRaw, sharedJobs, wsJobIds]);

  const [showDismissed, setShowDismissed] = React.useState(false);
  const [dismissed, setDismissed]         = React.useState(new Set());
  const [expanded, setExpanded]           = React.useState(new Set());

  const toggleExpanded = (key) => setExpanded(prev => {
    const next = new Set(prev);
    next.has(key) ? next.delete(key) : next.add(key);
    return next;
  });

  // Build a stable key for each alert so dismissals survive poll refreshes.
  // Uses category + job_id (if any) + first 60 chars of message — stable across rerenders.
  const alertKey = (a) => `${a.category||'health'}|${a.job_id||''}|${(a.message||'').slice(0, 60)}`;
  const dismiss  = (key) => setDismissed(s => new Set(s).add(key));

  const sevTag = (s) => {
    if (s === 'error')  return <span className="tag red">Error</span>;
    if (s === 'warn')   return <span className="tag amber">Warning</span>;
    return <span className="tag blue">Info</span>;
  };

  const activeAlerts    = alerts?.filter(a => !dismissed.has(alertKey(a))) || [];
  const dismissedAlerts = alerts?.filter(a =>  dismissed.has(alertKey(a))) || [];

  const grouped = { health: [], review: [], lot_quality: [] };
  activeAlerts.forEach((a) => {
    const cat = a.category || 'health';
    if (!grouped[cat]) grouped[cat] = [];
    grouped[cat].push({ ...a, _key: alertKey(a) });
  });

  const catLabel = { health: 'Health', review: 'Review', lot_quality: 'Lot quality' };

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Alerts Center</div>
          <div className="page-sub">
            {loading ? 'Loading…' : `${activeAlerts.length} active alert${activeAlerts.length !== 1 ? 's' : ''} · ${dismissedAlerts.length} dismissed`}
          </div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <label className="row" style={{ gap: 6, fontSize: 12, color: 'var(--text-muted)' }}>
            <span className={`toggle ${showDismissed ? 'on' : ''}`} onClick={() => setShowDismissed(!showDismissed)}/>
            Show dismissed
          </label>
          <button className="btn" onClick={reload}><Icon name="refresh" size={12}/> Refresh</button>
          <button className="btn" onClick={() => alerts?.forEach((_, i) => dismiss(i))}>Bulk dismiss</button>
        </div>
      </div>

      {loading && (
        <div className="card">
          <div className="card-body" style={{ color: 'var(--text-faint)', fontSize: 12 }}>Loading alerts…</div>
        </div>
      )}

      {alertsErr && !loading && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontWeight: 500, fontSize: 13, color: 'var(--text-muted)' }}>
              Alerts — feature placeholder
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-faint)', lineHeight: 1.6 }}>
              The <span className="mono">/alerts</span> endpoint is not available on this
              backend version. Active alert data (missing basis, missing prices, failed jobs)
              is visible in the <strong>Review Queue</strong> and <strong>Audit Log</strong> pages.
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-faint)', fontFamily: 'monospace' }}>
              {alertsErr}
            </div>
          </div>
        </div>
      )}

      {!loading && activeAlerts.length > 0 && (
        <div style={{
          padding: '10px 16px', marginBottom: 12,
          background: 'var(--bg-deep)', borderRadius: 8, fontSize: 12,
          color: 'var(--text-faint)', display: 'flex', gap: 8, alignItems: 'flex-start',
        }}>
          <Icon name="info" size={14}/>
          <div>
            Common alert causes: <strong style={{color:'var(--text-muted)'}}>Missing basis</strong> — lots acquired before earliest recorded transaction (provide cost basis manually).{' '}
            <strong style={{color:'var(--text-muted)'}}>Missing price</strong> — asset not in price database for that date (collect prices, or assets may not have a price source configured).{' '}
            Dust BTC/ETH holdings will show missing-price alerts if prices.db is empty.
          </div>
        </div>
      )}

      {!loading && activeAlerts.length === 0 && !showDismissed && (
        <div className="card">
          <div className="card-body" style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text-muted)', fontSize: 13 }}>
            <Icon name="check" size={16}/> No active alerts — system is clean
          </div>
        </div>
      )}

      {Object.entries(grouped).map(([cat, list]) => list.length === 0 ? null : (
        <div key={cat} className="card" style={{ marginBottom: 14 }}>
          <div className="card-header">
            <div className="card-title">{catLabel[cat] || cat} · {list.length}</div>
          </div>
          <div className="card-body" style={{ padding: 0 }}>
            {list.map((a, i) => {
              const key = `${cat}-${i}`;
              const isExpanded = expanded.has(key);
              return (
                <div key={i} style={{ padding: '12px 16px', borderBottom: i < list.length - 1 ? '1px solid var(--border)' : 'none' }}>
                  <div className="row" style={{ gap: 10 }}>
                    {sevTag(a.severity)}
                    <span style={{ flex: 1, fontSize: 13, fontWeight: 500 }}>{a.message}</span>
                    <span className="num muted" style={{ fontSize: 11 }}>
                      {a.raised_at ? window.relAge?.(a.raised_at) || '' : ''}
                    </span>
                    {a.detail?.length > 0 && (
                      <button className="btn ghost sm" onClick={() => toggleExpanded(key)}>
                        <Icon name={isExpanded ? 'chevron_up' : 'chevron_down'} size={11}/>
                        {isExpanded ? 'Less' : 'Detail'}
                      </button>
                    )}
                    <button className="btn ghost sm" onClick={() => { dismiss(a._key); addToast({ title: 'Acknowledged', msg: a.message }); }}>
                      <Icon name="check" size={11}/> Ack
                    </button>
                    <button className="btn ghost sm" onClick={() => dismiss(a._key)}>
                      <Icon name="x" size={11}/>
                    </button>
                  </div>
                  {a.detail?.length > 0 && isExpanded && (
                    <div style={{ marginTop: 8, padding: '8px 12px', background: 'var(--bg-deep)', borderRadius: 6 }}>
                      {a.detail.map((d, di) => (
                        <div key={di} style={{ fontSize: 11.5, color: 'var(--text-muted)', padding: '2px 0', display: 'flex', gap: 6 }}>
                          <span style={{ color: 'var(--text-faint)' }}>·</span>
                          <span>{d}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {a.detail?.length > 0 && !isExpanded && (
                    <div style={{ marginTop: 4, paddingLeft: 4, fontSize: 11.5, color: 'var(--text-faint)' }}>
                      {a.detail.slice(0, 2).join(' · ')}
                      {a.detail.length > 2 && <span style={{ cursor: 'pointer', color: 'var(--accent)' }} onClick={() => toggleExpanded(key)}> · +{a.detail.length - 2} more</span>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}

      {showDismissed && dismissedAlerts.length > 0 && (
        <div className="card">
          <div className="card-header"><div className="card-title">Dismissed · {dismissedAlerts.length}</div></div>
          <div className="card-body" style={{ padding: 0 }}>
            {dismissedAlerts.map((a, i) => (
              <div key={i} style={{ padding: '10px 16px', opacity: 0.5, borderBottom: i < dismissedAlerts.length - 1 ? '1px solid var(--border)' : 'none' }}>
                <span style={{ fontSize: 12 }}>{a.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
};


// ── Settings ──────────────────────────────────────────────────────────────────
const Settings = ({ juris, setJuris, userName, setUserName, wsConfig, reloadWorkspace }) => {
  // Read localStorage once on mount; re-read after the user clicks Apply.
  const [apiKeySet, setApiKeySet] = React.useState(() => !!localStorage.getItem('ORCHESTRATOR_KEY'));
  // Valuation mode — persisted in localStorage so Tax Center can read it as a hint.
  const [valMode, setValMode] = React.useState(() => localStorage.getItem('ALTANA_VAL_MODE') || 'prices.db (recommended)');
  // wsConfig and reloadWorkspace — shared from App (10 s polling).
  // Mutations (add/remove account, add/remove asset) call reloadWorkspace() so the
  // App-level signal refreshes immediately and propagates to all pages.
  const workspace = wsConfig;
  const reloadWs  = reloadWorkspace;

  const xrplAccounts = workspace?.xrpl_accounts || [];
  const xrplAssets   = workspace?.xrpl_assets   || [];

  // Profile editing
  const [editingUser, setEditingUser] = React.useState(false);
  const [userInput, setUserInput]     = React.useState(userName || '');

  const saveUserName = () => {
    setUserName(userInput.trim());
    setEditingUser(false);
  };

  // XRPL account add
  const [addingAccount, setAddingAccount] = React.useState(false);
  const [acctInput, setAcctInput]         = React.useState('');
  const [acctLabel, setAcctLabel]         = React.useState('');
  const [acctError, setAcctError]         = React.useState('');
  const [acctSaving, setAcctSaving]       = React.useState(false);

  const handleAddAccount = async () => {
    setAcctError('');
    if (!acctInput.trim()) { setAcctError('Address is required'); return; }
    setAcctSaving(true);
    try {
      await window.API.addXrplAccount(acctInput.trim());
      reloadWs();
      setAddingAccount(false);
      setAcctInput(''); setAcctLabel('');
    } catch (e) {
      setAcctError(String(e.message || e));
    } finally {
      setAcctSaving(false);
    }
  };

  const handleRemoveAccount = async (addr) => {
    try {
      await window.API.removeXrplAccount(addr);
      reloadWs();
    } catch (e) {
      // Surface the error in the accounts section (same acctError state used by add form).
      setAcctError(`Remove failed: ${String(e.message || e)}`);
    }
  };

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Settings · Workspace</div>
          <div className="page-sub">
            {xrplAccounts.length} wallet{xrplAccounts.length !== 1 ? 's' : ''} ·{' '}
            {(workspace?.csv_files?.length ?? 0)} CSV file{(workspace?.csv_files?.length ?? 0) !== 1 ? 's' : ''}
          </div>
        </div>
      </div>

      <div className="grid grid-12">
        <div style={{ gridColumn: 'span 8' }}>

          {/* ── Profile ─────────────────────────────────────────────────────── */}
          <div className="card" style={{ marginBottom: 14 }}>
            <div className="card-header">
              <div className="card-title">Profile</div>
              {!editingUser && (
                <button className="btn sm" onClick={() => { setUserInput(userName || ''); setEditingUser(true); }}>
                  Edit
                </button>
              )}
            </div>
            <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>
                  Your name <span style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 400 }}>— shown in dashboard title &amp; avatar · stored locally per workspace</span>
                </div>
                {editingUser ? (
                  <div className="row" style={{ gap: 6 }}>
                    <input
                      autoFocus
                      value={userInput}
                      onChange={e => setUserInput(e.target.value)}
                      placeholder="e.g. Ola"
                      onKeyDown={e => { if (e.key === 'Enter') saveUserName(); if (e.key === 'Escape') setEditingUser(false); }}
                      style={{ flex: 1, padding: '5px 10px', borderRadius: 6, background: 'var(--bg-deep)', border: '1px solid var(--border)', color: 'var(--text)', fontSize: 13 }}
                    />
                    <button className="btn sm primary" onClick={saveUserName}>Save</button>
                    <button className="btn ghost sm" onClick={() => setEditingUser(false)}>Cancel</button>
                  </div>
                ) : (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    {userName ? (
                      <>
                        <div className="avatar" style={{ width: 34, height: 34, fontSize: 13 }}>
                          {userName.trim().split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase()}
                        </div>
                        <span style={{ fontSize: 14, fontWeight: 500 }}>{userName}</span>
                      </>
                    ) : (
                      <span style={{ fontSize: 13, color: 'var(--text-faint)' }}>
                        Not set — dashboard will show "Dashboard"
                      </span>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="card" style={{ marginBottom: 14 }}>
            <div className="card-header"><div className="card-title">Jurisdiction &amp; output</div></div>
            <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Primary country</div>
                <div className="row" style={{ gap: 6 }}>
                  <button className={`btn ${juris === 'NO' ? 'primary' : ''}`} style={{ flex: 1, justifyContent: 'center' }} onClick={() => setJuris('NO')}>🇳🇴 Norway · NOK</button>
                  <button className={`btn ${juris === 'UK' ? 'primary' : ''}`} style={{ flex: 1, justifyContent: 'center' }} onClick={() => setJuris('UK')}>🇬🇧 United Kingdom · GBP</button>
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Default valuation mode</div>
                <select
                  value={valMode}
                  onChange={e => { setValMode(e.target.value); localStorage.setItem('ALTANA_VAL_MODE', e.target.value); }}
                  style={{ width: '100%', padding: 6, borderRadius: 7, background: 'var(--bg-deep)', border: '1px solid var(--border)', color: 'var(--text)' }}>
                  <option>prices.db (recommended)</option>
                  <option>Price Table CSV</option>
                  <option>Dummy</option>
                </select>
                <div style={{ marginTop: 4, fontSize: 10.5, color: 'var(--text-faint)' }}>Stored locally · applied on next job run in Tax Center</div>
              </div>
            </div>
          </div>

          <div className="card" style={{ marginBottom: 14 }}>
            <div className="card-header">
              <div className="card-title">XRPL accounts</div>
              <button className="btn sm" onClick={() => { setAddingAccount(a => !a); setAcctInput(''); setAcctLabel(''); setAcctError(''); }}>
                <Icon name="plus" size={11}/> Add
              </button>
            </div>

            {addingAccount && (
              <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'var(--bg-deep)' }}>
                <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>Add XRPL wallet</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <input
                    autoFocus
                    value={acctInput}
                    onChange={e => setAcctInput(e.target.value)}
                    placeholder="r… (XRPL address)"
                    onKeyDown={e => { if (e.key === 'Enter') handleAddAccount(); if (e.key === 'Escape') setAddingAccount(false); }}
                    style={{ padding: '5px 10px', borderRadius: 6, background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)', fontSize: 12, fontFamily: 'monospace' }}
                  />
                  {acctError && <div style={{ fontSize: 11.5, color: 'var(--red)' }}>{acctError}</div>}
                  <div className="row" style={{ gap: 6 }}>
                    <button className="btn sm primary" onClick={handleAddAccount} disabled={acctSaving}>
                      {acctSaving ? 'Adding…' : 'Add wallet'}
                    </button>
                    <button className="btn ghost sm" onClick={() => setAddingAccount(false)}>Cancel</button>
                  </div>
                </div>
              </div>
            )}

            <table className="tbl">
              <thead>
                <tr><th>Address</th><th>Label</th><th></th></tr>
              </thead>
              <tbody>
                {xrplAccounts.length === 0 && !addingAccount && (
                  <tr>
                    <td colSpan={3} style={{ padding: '12px 16px', color: 'var(--text-faint)', fontSize: 12 }}>
                      No XRPL accounts configured — click Add above
                    </td>
                  </tr>
                )}
                {xrplAccounts.map(addr => {
                  const label = workspace?.wallet_labels?.[addr] || '—';
                  return (
                    <tr key={addr}>
                      <td className="mono" style={{ fontSize: 11.5 }}>{addr}</td>
                      <td>{label}</td>
                      <td style={{ textAlign: 'right' }}>
                        <button className="btn ghost sm" onClick={() => handleRemoveAccount(addr)}>
                          <Icon name="x" size={10}/>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {xrplAssets.length > 0 && (
            <div className="card">
              <div className="card-header">
                <div className="card-title">XRPL asset registry</div>
                <button className="btn sm" title="Add via Prices page → XRPL asset registry"><Icon name="plus" size={11}/> Add</button>
              </div>
              <table className="tbl">
                <thead><tr><th>Spec (symbol.issuer)</th><th></th></tr></thead>
                <tbody>
                  {xrplAssets.map(a => (
                    <tr key={a}>
                      <td className="mono" style={{ fontSize: 11.5 }}>{a}</td>
                      <td style={{ textAlign: 'right' }}>
                        <button className="btn ghost sm" onClick={async () => {
                          try { await window.API.removeXrplAsset(a); reloadWs(); } catch {}
                        }}><Icon name="x" size={10}/></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div style={{ gridColumn: 'span 4' }}>
          <div className="card" style={{ marginBottom: 14 }}>
            <div className="card-header"><div className="card-title">Security</div></div>
            <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', fontSize: 12.5 }}>
                <div>
                  <div>ORCHESTRATOR_KEY</div>
                  <div style={{ fontSize: 10.5, color: 'var(--text-faint)', marginTop: 2 }}>
                    Server-side auth key — guards mutating endpoints. Set in <span className="mono">.env</span> or as an environment variable.
                  </div>
                </div>
                <span className="tag" style={{ flexShrink: 0, marginLeft: 8 }}>Server-managed</span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between', fontSize: 12.5 }}>
                <div>
                  <div>API key (browser)</div>
                  <div style={{ fontSize: 10.5, color: 'var(--text-faint)', marginTop: 2 }}>
                    Sent as <span className="mono">X-Orchestrator-Key</span> header with every request.
                  </div>
                </div>
                <span className={`tag ${apiKeySet ? 'green' : 'red'}`} style={{ flexShrink: 0, marginLeft: 8 }}>
                  {apiKeySet ? 'Set' : 'Not set'}
                </span>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Set browser API key</div>
                <div className="row" style={{ gap: 6 }}>
                  <input
                    placeholder="Paste key…"
                    type="password"
                    style={{ flex: 1, padding: '4px 8px', borderRadius: 6, background: 'var(--bg-deep)', border: '1px solid var(--border)', color: 'var(--text)', fontSize: 12 }}
                    onChange={e => {
                      if (e.target.value) localStorage.setItem('ORCHESTRATOR_KEY', e.target.value);
                      else localStorage.removeItem('ORCHESTRATOR_KEY');
                      setApiKeySet(!!e.target.value);
                    }}
                  />
                  <button className="btn sm" onClick={() => window.location.reload()}>Apply</button>
                </div>
                <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-faint)' }}>Stored in localStorage · reloads page</div>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-header"><div className="card-title">About</div></div>
            <div className="card-body" style={{ fontSize: 12.5, color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div className="row" style={{ justifyContent: 'space-between' }}><span>App</span><span className="mono">ALTANA</span></div>
              <div className="row" style={{ justifyContent: 'space-between' }}><span>Engine</span><span className="mono">taxspine-orchestrator</span></div>
              <div className="row" style={{ justifyContent: 'space-between' }}><span>Pipeline</span><span className="mono">Project F · Phase 2</span></div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

window.Audit       = Audit;
window.Diagnostics = Diagnostics;
window.Alerts      = Alerts;
window.Settings    = Settings;
