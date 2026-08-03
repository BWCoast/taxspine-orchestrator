// ALTANA — Pages: Sources, Tax Center, Review Queue

// Static chain registry — reflects what the tax engine actually supports.
// "via CSV" means the engine processes generic-events CSVs from that source;
// it does not mean live on-chain syncing is implemented for that chain.
const CHAINS = [
  { name: 'XRPL',          sym: 'XRP',  status: 'supported', events: 'trade · transfer · IOU · DEX' },
  { name: 'Bitcoin',       sym: 'BTC',  status: 'via CSV',   events: 'transfer (CSV import)' },
  { name: 'Ethereum',      sym: 'ETH',  status: 'via CSV',   events: 'trade · transfer (CSV import)' },
  { name: 'Hedera',        sym: 'HBAR', status: 'via CSV',   events: 'transfer (CSV import)' },
  { name: 'Constellation', sym: 'DAG',  status: 'via CSV',   events: 'transfer (CSV import)' },
  { name: 'Casper',        sym: 'CSPR', status: 'via CSV',   events: 'transfer (CSV import)' },
  { name: 'Flare',         sym: 'FLR',  status: 'via CSV',   events: 'transfer · airdrop (CSV import)' },
  { name: 'Osmosis',       sym: 'OSMO', status: 'via CSV',   events: 'trade · LP (CSV import)' },
];

// Map human-readable source type labels → API values
const SOURCE_TYPE_API = {
  'Auto-detect': 'generic_events',
  'Coinbase': 'coinbase_csv',
  'Firi': 'firi_csv',
  'Kraken': 'kraken_spot_csv',
  'NBX': 'nbx_csv',
  'Uphold': 'uphold_csv',
  'Bybit': 'bybit_uta_csv',
  'Generic Events': 'generic_events',
};

const Sources = ({ addToast, setRoute }) => {
  const [tab, setTab] = React.useState('wallets');
  const [drag, setDrag] = React.useState(false);
  const fileInputRef = React.useRef(null);
  const [uploadState, setUploadState]     = React.useState('idle'); // idle|uploading|done|error
  const [uploadError, setUploadError]     = React.useState('');
  const [selectedSourceType, setSelectedSourceType] = React.useState('Auto-detect');

  const { data: workspace, reload: reloadWs } = window.useApi(() => window.API.getWorkspace(), []);
  const { data: dedupSrc }                     = window.useApi(() => window.API.getDedupSources(), []);
  const { data: botStatus }                    = window.useApi(() => window.API.getBotStatus(), []);

  // Inline add-wallet form
  const [addingWallet, setAddingWallet] = React.useState(false);
  const [walletAddr, setWalletAddr]     = React.useState('');
  const [walletLabel, setWalletLabel]   = React.useState('');
  const [walletError, setWalletError]   = React.useState('');
  const [walletSaving, setWalletSaving] = React.useState(false);

  // Inline label editing — keyed by address
  const [editingLabel, setEditingLabel]   = React.useState(null); // addr being edited
  const [labelInput, setLabelInput]       = React.useState('');

  const startEditLabel = (addr, current) => {
    setEditingLabel(addr);
    setLabelInput(current === 'Unlabelled' ? '' : current);
  };
  const saveLabel = async (addr) => {
    const trimmed = labelInput.trim();
    try {
      if (trimmed) await window.API.setWalletLabel(addr, trimmed);
      else         await window.API.removeWalletLabel(addr);
      reloadWs();
    } catch (e) {
      addToast({ title: 'Label error', msg: String(e.message || e), kind: 'error' });
    } finally {
      setEditingLabel(null);
    }
  };

  const handleAddWallet = async () => {
    setWalletError('');
    if (!walletAddr.trim()) { setWalletError('Address required'); return; }
    setWalletSaving(true);
    try {
      await window.API.addXrplAccount(walletAddr.trim());
      if (walletLabel.trim()) await window.API.setWalletLabel(walletAddr.trim(), walletLabel.trim());
      reloadWs();
      setAddingWallet(false);
      setWalletAddr('');
      setWalletLabel('');
      addToast({ title: 'Wallet added', msg: walletAddr.trim().slice(0, 16) + '…' });
    } catch (e) {
      setWalletError(String(e.message || e));
    } finally {
      setWalletSaving(false);
    }
  };

  const handleRemoveWallet = async (addr) => {
    try {
      await window.API.removeXrplAccount(addr);
      reloadWs();
      addToast({ title: 'Wallet removed', msg: addr.slice(0, 16) + '…' });
    } catch (e) {
      addToast({ title: 'Error', msg: String(e.message || e), kind: 'error' });
    }
  };

  const handleUpload = async (file) => {
    if (!file) return;
    setUploadState('uploading'); setUploadError('');
    try {
      const apiType = SOURCE_TYPE_API[selectedSourceType] || 'generic_events';
      await window.API.uploadCsv(file, apiType);
      reloadWs();
      setUploadState('done');
      addToast({ title: 'CSV uploaded', msg: file.name });
      setTimeout(() => setUploadState('idle'), 2500);
    } catch (e) {
      setUploadError(String(e.message || e));
      setUploadState('error');
    }
  };

  const handleRemoveCsv = async (f) => {
    try {
      await window.API.removeCsv(f.path, f.source_type || 'generic_events');
      reloadWs();
      addToast({ title: 'CSV removed', msg: (f.path || '').split(/[\\/]/).pop() });
    } catch (e) {
      addToast({ title: 'Error', msg: String(e.message || e), kind: 'error' });
    }
  };

  const downloadTemplate = () => {
    const headers = [
      'event_id', 'timestamp', 'event_type', 'source', 'account',
      'asset_in', 'amount_in', 'asset_out', 'amount_out',
      'fee_asset', 'fee_amount', 'tx_hash', 'exchange_tx_id',
      'label', 'complex_tax_treatment', 'note',
    ].join(',');
    const blob = new Blob([headers + '\n'], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'generic_events_template.csv';
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  const xrplAccounts = workspace?.xrpl_accounts || [];
  const csvFiles     = workspace?.csv_files      || [];
  const xrplAssets   = workspace?.xrpl_assets    || [];
  const totalEvents  = csvFiles.reduce((s, f) => s + (f.row_count || 0), 0);
  const dedupSkips   = dedupSrc?.reduce?.((s, d) => s + (d.total_skips || 0), 0) ?? 0;

  const statusTag = (s) => {
    if (s === 'synced')  return <span className="tag green"><span className="dot green"/> Synced</span>;
    if (s === 'syncing') return <span className="tag blue"><span className="dot blue"/> Syncing…</span>;
    return <span className="tag red"><span className="dot red"/> Error</span>;
  };

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Data Sources</div>
          <div className="page-sub">
            {xrplAccounts.length} wallet{xrplAccounts.length !== 1 ? 's' : ''} ·{' '}
            {csvFiles.length} CSV file{csvFiles.length !== 1 ? 's' : ''} ·{' '}
            {totalEvents.toLocaleString()} events ·{' '}
            {dedupSkips} dedup skips
          </div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn" onClick={() => { reloadWs(); addToast({ title: 'Refreshed', msg: 'Workspace data reloaded' }); }}>
            <Icon name="refresh" size={12}/> Refresh
          </button>
          <button className="btn primary" onClick={() => { setTab('wallets'); setAddingWallet(true); }}>
            <Icon name="plus" size={12}/> Add wallet
          </button>
        </div>
      </div>

      <div className="grid grid-12" style={{ marginBottom: 16 }}>
        <div style={{ gridColumn: 'span 8' }}>
          <div className="card">
            <div className="card-header">
              <div className="tabs">
                <button className={tab === 'wallets'  ? 'active' : ''} onClick={() => setTab('wallets')}>XRPL Wallets</button>
                <button className={tab === 'uploads'  ? 'active' : ''} onClick={() => setTab('uploads')}>CSV Files</button>
                <button className={tab === 'bot'      ? 'active' : ''} onClick={() => setTab('bot')}>Bot (DuckDB)</button>
              </div>
              {tab === 'wallets' && (
                <button className="btn sm" onClick={() => { setAddingWallet(a => !a); setWalletAddr(''); setWalletError(''); }}>
                  <Icon name="plus" size={11}/> Add
                </button>
              )}
            </div>

            {tab === 'wallets' && (
              <>
                {addingWallet && (
                  <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'var(--bg-deep)' }}>
                    <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>Add XRPL wallet</div>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                      <input
                        autoFocus
                        value={walletAddr}
                        onChange={e => setWalletAddr(e.target.value)}
                        placeholder="r… (XRPL address)"
                        onKeyDown={e => { if (e.key === 'Enter') handleAddWallet(); if (e.key === 'Escape') setAddingWallet(false); }}
                        style={{ flex: 2, padding: '5px 10px', borderRadius: 6, background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)', fontSize: 12, fontFamily: 'monospace' }}
                      />
                      <input
                        value={walletLabel}
                        onChange={e => setWalletLabel(e.target.value)}
                        placeholder="Label (optional)"
                        onKeyDown={e => { if (e.key === 'Enter') handleAddWallet(); if (e.key === 'Escape') setAddingWallet(false); }}
                        style={{ flex: 1, padding: '5px 10px', borderRadius: 6, background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)', fontSize: 12 }}
                      />
                      <button className="btn sm primary" onClick={handleAddWallet} disabled={walletSaving}>
                        {walletSaving ? 'Adding…' : 'Add'}
                      </button>
                      <button className="btn ghost sm" onClick={() => setAddingWallet(false)}>Cancel</button>
                    </div>
                    {walletError && <div style={{ marginTop: 6, fontSize: 11.5, color: 'var(--red)' }}>{walletError}</div>}
                  </div>
                )}
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Address</th>
                      <th>Label</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {xrplAccounts.length === 0 && !addingWallet && (
                      <tr>
                        <td colSpan={3} style={{ padding: '20px 16px', color: 'var(--text-faint)', fontSize: 12 }}>
                          No XRPL wallets configured — click Add above
                        </td>
                      </tr>
                    )}
                    {xrplAccounts.map(addr => {
                      const label = workspace?.wallet_labels?.[addr] || 'Unlabelled';
                      const isEditing = editingLabel === addr;
                      return (
                        <tr key={addr}>
                          <td className="mono" style={{ fontSize: 11.5 }}>{addr}</td>
                          <td>
                            {isEditing ? (
                              <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                                <input
                                  autoFocus
                                  value={labelInput}
                                  onChange={e => setLabelInput(e.target.value)}
                                  placeholder="Label…"
                                  onKeyDown={e => { if (e.key === 'Enter') saveLabel(addr); if (e.key === 'Escape') setEditingLabel(null); }}
                                  style={{ flex: 1, padding: '3px 8px', borderRadius: 5, background: 'var(--surface)', border: '1px solid var(--accent)', color: 'var(--text)', fontSize: 12 }}
                                />
                                <button className="btn sm primary" onClick={() => saveLabel(addr)}>Save</button>
                                <button className="btn ghost sm" onClick={() => setEditingLabel(null)}>✕</button>
                              </div>
                            ) : (
                              <span
                                className={label === 'Unlabelled' ? 'muted' : ''}
                                onClick={() => startEditLabel(addr, label)}
                                title="Click to edit label"
                                style={{ cursor: 'text', borderBottom: '1px dashed var(--border)', paddingBottom: 1 }}
                              >
                                {label}
                              </span>
                            )}
                          </td>
                          <td style={{ textAlign: 'right' }}>
                            <button className="btn ghost sm" onClick={() => handleRemoveWallet(addr)}>
                              <Icon name="x" size={11}/>
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </>
            )}

            {tab === 'uploads' && (
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Filename</th>
                    <th>Source type</th>
                    <th className="num">Rows</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {csvFiles.length === 0 && (
                    <tr>
                      <td colSpan={4} style={{ padding: '20px 16px', color: 'var(--text-faint)', fontSize: 12 }}>
                        No CSV files — upload one below
                      </td>
                    </tr>
                  )}
                  {csvFiles.map((f, i) => {
                    const fname = f.path?.split(/[\\/]/).pop() || f.path || '';
                    // Flag files that look like price/valuation data rather than exchange CSVs
                    const looksLikePrice = /combined_nok|combined_gbp|prices?\.csv|price_table/i.test(fname);
                    return (
                      <tr key={i}>
                        <td className="mono" style={{ fontSize: 11.5 }}>
                          {fname}
                          {looksLikePrice && (
                            <span className="tag amber" style={{ marginLeft: 8, fontSize: 10 }}
                                  title="This looks like a price/valuation file, not an exchange CSV">
                              ⚠ price file?
                            </span>
                          )}
                        </td>
                        <td><span className="tag">{f.source_type || 'generic'}</span></td>
                        <td className="num">{f.row_count != null ? f.row_count.toLocaleString() : '—'}</td>
                        <td style={{ textAlign: 'right' }}>
                          <button className="btn ghost sm" onClick={() => handleRemoveCsv(f)}><Icon name="x" size={11}/></button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}

            {tab === 'bot' && (
              <div className="card-body">
                <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
                  <div className="kpi" style={{ flex: 1, padding: 12 }}>
                    <div className="kpi-label">BOT_DB path</div>
                    <div className="mono" style={{ fontSize: 12, marginTop: 6 }}>
                      {botStatus?.db_path || '~/.taxnor/bot/****.duckdb'}
                    </div>
                  </div>
                  <div className="kpi" style={{ flex: 1, padding: 12 }}>
                    <div className="kpi-label">Last sync</div>
                    <div className="kpi-value" style={{ fontSize: 14, marginTop: 4 }}>
                      {botStatus?.events_written != null ? `${botStatus.events_written} events` : '—'}
                    </div>
                    <div className="kpi-sub">
                      <span className={`dot ${botStatus ? 'green' : 'amber'}`}/>{' '}
                      {botStatus?.synced_at ? window.relAge(botStatus.synced_at) : 'unknown'} · {botStatus?.error_count ?? 0} errors
                    </div>
                  </div>
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', margin: '8px 0 6px' }}>
                  XRPL asset registry
                </div>
                <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
                  {xrplAssets.map(a => (
                    <span key={a} className="tag green">{a}</span>
                  ))}
                  {xrplAssets.length === 0 && (
                    <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>No XRPL assets registered</span>
                  )}
                  <button className="btn sm"><Icon name="plus" size={10}/> Add</button>
                </div>
              </div>
            )}
          </div>
        </div>

        <div style={{ gridColumn: 'span 4' }}>
          <div className="card">
            <div className="card-header"><div className="card-title">Upload CSV</div></div>
            <div className="card-body">
              {/* Hidden file input — triggered by dropzone click */}
              <input ref={fileInputRef} type="file" accept=".csv" style={{ display: 'none' }}
                onChange={e => { const f = e.target.files?.[0]; if (f) handleUpload(f); e.target.value = ''; }}/>
              <div
                className={`dz ${drag ? 'on' : ''} ${uploadState === 'uploading' ? 'on' : ''}`}
                style={{ cursor: 'pointer' }}
                onDragOver={e => { e.preventDefault(); setDrag(true); }}
                onDragLeave={() => setDrag(false)}
                onDrop={e => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) handleUpload(f); }}
                onClick={() => uploadState === 'idle' || uploadState === 'error' ? fileInputRef.current?.click() : null}
              >
                <Icon name="upload" size={20}/>
                <div className="dz-title" style={{ marginTop: 8 }}>
                  {uploadState === 'uploading' ? 'Uploading…'
                    : uploadState === 'done'     ? '✓ Uploaded'
                    : 'Drop CSV here'}
                </div>
                <div style={{ fontSize: 11.5 }}>
                  {uploadState === 'error' ? <span style={{ color: 'var(--red)' }}>{uploadError.slice(0, 80)}</span>
                    : 'or click to browse · auto-detects source'}
                </div>
              </div>
              <div style={{ marginTop: 14 }}>
                <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Source type</div>
                <select value={selectedSourceType} onChange={e => setSelectedSourceType(e.target.value)}
                        style={{ width: '100%', padding: 6, borderRadius: 7, background: 'var(--bg-deep)', border: '1px solid var(--border)', color: 'var(--text)' }}>
                  {Object.keys(SOURCE_TYPE_API).map(k => <option key={k}>{k}</option>)}
                </select>
              </div>
              <button className="btn ghost sm" style={{ marginTop: 12, width: '100%' }} onClick={downloadTemplate}>
                <Icon name="download" size={11}/> Download Generic Events template
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ── Supported chains ──────────────────────────────────────────────── */}
      <div className="card">
        <div className="card-header">
          <div className="card-title">
            Supported chains &amp; readers <span className="meta">· {CHAINS.length} chains</span>
          </div>
        </div>
        <div className="card-body">
          <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 10 }}>
            {CHAINS.map(c => (
              <div key={c.name} className="tile">
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div className="tile-icon">{c.sym.slice(0, 2)}</div>
                  <div style={{ flex: 1 }}>
                    <div className="tile-name">{c.name}</div>
                    <div className="tile-meta">{c.events}</div>
                  </div>
                </div>
                <div style={{ marginTop: 'auto', paddingTop: 8 }}>
                  {c.status === 'supported' && <span className="tag green"><span className="dot green"/> Live sync</span>}
                  {c.status === 'via CSV'   && <span className="tag blue"><span className="dot blue"/> Via CSV</span>}
                  {c.status === 'beta'      && <span className="tag blue"><span className="dot blue"/> Beta</span>}
                  {c.status === 'soon'      && <span className="tag">Coming soon</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
};


// ── Tax Center ────────────────────────────────────────────────────────────────
const TaxCenter = ({ juris, setJuris, year, addToast }) => {
  const [running, setRunning]         = React.useState(false);
  const [runStatus, setRunStatus]     = React.useState(null);
  const [runError, setRunError]       = React.useState(null);
  const [valuation, setValuation]     = React.useState('db');
  const [pipeline, setPipeline]       = React.useState(juris === 'NO' ? 'multi' : 'per-file');
  const [dryRun, setDryRun]           = React.useState(false);
  const [collecting, setCollecting]   = React.useState(false);
  const [downloadingJob, setDownloadingJob] = React.useState(null);
  // Local tax year — independent of the global year selector so Tax Center
  // can run a different year than the dashboard without affecting other pages.
  const [taxYear, setTaxYear]         = React.useState(year);
  React.useEffect(() => { setTaxYear(year); }, [year]);
  const _pollRef = React.useRef(null);

  const { data: jobs, reload: reloadJobs } = window.useApi(
    () => window.API.getJobs({ limit: 20 }), []
  );
  const { data: diags } = window.useApi(() => window.API.getDiagnostics(), []);

  const ccy = juris === 'NO' ? 'NOK' : 'GBP';

  // Stop any running poll when component unmounts.
  React.useEffect(() => () => { if (_pollRef.current) clearInterval(_pollRef.current); }, []);

  const runJob = async () => {
    setRunning(true); setRunStatus('pending'); setRunError(null);
    try {
      const job = await window.API.runWorkspace({
        tax_year:       taxYear,
        country:        juris === 'NO' ? 'norway' : 'uk',
        valuation_mode: valuation,
        pipeline_mode:  pipeline === 'multi' ? 'nor_multi' : 'per_file',
      });
      // Backend returns { job_id } or { id } depending on version
      const jobId = job?.job_id || job?.id;
      if (!jobId) throw new Error('No job_id in response');
      setRunStatus('pending');

      // Poll every 3 s until terminal state.
      _pollRef.current = setInterval(async () => {
        try {
          const j = await window.API.getJob(jobId);
          setRunStatus(j.status);
          if (j.status === 'completed') {
            clearInterval(_pollRef.current);
            setRunning(false);
            addToast({ title: 'Tax job completed', msg: `${juris} · ${year} · outputs ready` });
            reloadJobs();
          } else if (j.status === 'failed') {
            clearInterval(_pollRef.current);
            setRunning(false);
            setRunError(j.error || 'Job failed — check Audit Log for details.');
            addToast({ title: 'Job failed', msg: j.error?.slice(0, 120) || 'See Audit Log' });
            reloadJobs();
          }
        } catch { /* poll errors are transient — keep trying */ }
      }, 3000);

    } catch (e) {
      setRunning(false);
      setRunStatus('failed');
      setRunError(String(e.message || e));
      addToast({ title: 'Failed to start job', msg: String(e.message || e).slice(0, 120) });
    }
  };

  const collectPrices = async () => {
    setCollecting(true);
    try {
      await window.API.collectPrices(taxYear);
      addToast({ title: 'Price collection complete', msg: `${taxYear} NOK prices fetched` });
      reloadJobs();
    } catch (e) {
      addToast({ title: 'Price collection failed', msg: String(e.message || e).slice(0, 120), kind: 'error' });
    } finally {
      setCollecting(false);
    }
  };

  const downloadJob = async (jobId, jobCountry) => {
    setDownloadingJob(jobId);
    try {
      const files = await window.API.listJobFiles(jobId);
      // Download priority: HTML report → RF-1159 (NO) → gains CSV → log
      const priority = jobCountry === 'NO'
        ? ['report', 'rf1159', 'gains', 'wealth', 'log']
        : ['report', 'gains', 'wealth', 'log'];
      const kind = priority.find(k => files[k]);
      if (!kind) throw new Error('No output files available — run the job first');
      const ext = { report: 'html', rf1159: 'json', gains: 'csv', wealth: 'csv', log: 'txt' }[kind] || 'bin';
      await window.API.downloadJobFile(jobId, kind, `${jobId.slice(0, 8)}_${kind}.${ext}`);
    } catch (e) {
      addToast({ title: 'Download failed', msg: String(e.message || e).slice(0, 120), kind: 'error' });
    } finally {
      setDownloadingJob(null);
    }
  };

  const pricesStatus = diags?.prices?.prices_db?.exists
    ? `prices.db · ${diags.prices.prices_db.row_count?.toLocaleString() ?? '?'} rows`
    : diags?.prices?.combined_csvs?.length
    ? `CSV · ${diags.prices.combined_csvs.length} file(s)`
    : 'no price data';

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Tax Center</div>
          <div className="page-sub">
            Run jobs, download outputs, audit prior runs ·{' '}
            {juris === 'NO' ? 'Norway · RF-1159' : 'United Kingdom · SA100/SA108'}
          </div>
        </div>
      </div>

      <div className="grid grid-12" style={{ marginBottom: 16 }}>
        <div style={{ gridColumn: 'span 7' }}>
          <div className="card">
            <div className="card-header">
              <div className="card-title">Run a tax job</div>
              {dryRun && <span className="tag amber">Dry run</span>}
            </div>
            <div className="card-body">
              <div className="grid grid-2" style={{ gap: 12, marginBottom: 14 }}>
                <div>
                  <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Country</div>
                  <div className="row" style={{ gap: 6 }}>
                    <button className={`btn ${juris === 'NO' ? 'primary' : ''}`} style={{ flex: 1, justifyContent: 'center' }}
                      onClick={() => { setJuris('NO'); setPipeline('multi'); }}>🇳🇴 Norway</button>
                    <button className={`btn ${juris === 'UK' ? 'primary' : ''}`} style={{ flex: 1, justifyContent: 'center' }}
                      onClick={() => { setJuris('UK'); setPipeline('per-file'); }}>🇬🇧 United Kingdom</button>
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Tax year</div>
                  <select value={taxYear} onChange={e => setTaxYear(Number(e.target.value))}
                          style={{ width: '100%', padding: 6, borderRadius: 7, background: 'var(--bg-deep)', border: '1px solid var(--border)', color: 'var(--text)' }}>
                    {[year, year - 1, year - 2, year - 3].map(y => <option key={y} value={y}>{y}</option>)}
                  </select>
                </div>
              </div>

              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>Valuation mode</div>
                <div className="row" style={{ gap: 6 }}>
                  {['dummy', 'csv', 'db'].map(m => (
                    <button key={m} className={`btn ${valuation === m ? 'primary' : ''}`} onClick={() => setValuation(m)} style={{ flex: 1, justifyContent: 'center' }}>
                      {m === 'dummy' ? 'Dummy' : m === 'csv' ? 'Price Table' : 'prices.db'}
                    </button>
                  ))}
                </div>
                {valuation === 'db' && (
                  <div className="row" style={{ marginTop: 8, fontSize: 11.5, color: 'var(--text-muted)', gap: 6 }}>
                    <span className={`dot ${diags?.prices?.prices_db?.exists ? 'green' : 'amber'}`}/>
                    {pricesStatus}
                    <button className="btn ghost sm" style={{ marginLeft: 'auto' }} onClick={collectPrices} disabled={collecting}>
                      <Icon name="refresh" size={11}/> {collecting ? 'Fetching…' : 'Collect'}
                    </button>
                  </div>
                )}
              </div>

              {juris === 'NO' && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 11, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>
                    Pipeline mode <span className="tip" data-tip="NOR_MULTI batches all sources for single-pool FIFO"><Icon name="info" size={11}/></span>
                  </div>
                  <div className="row" style={{ gap: 6 }}>
                    <button className={`btn ${pipeline === 'per-file' ? 'primary' : ''}`} onClick={() => setPipeline('per-file')} style={{ flex: 1, justifyContent: 'center' }}>Per-file</button>
                    <button className={`btn ${pipeline === 'multi'    ? 'primary' : ''}`} onClick={() => setPipeline('multi')}    style={{ flex: 1, justifyContent: 'center' }}>NOR_MULTI</button>
                  </div>
                </div>
              )}

              <div className="row" style={{ justifyContent: 'space-between', borderTop: '1px solid var(--border)', paddingTop: 14 }}>
                <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5, cursor: 'pointer' }}>
                  <span className={`toggle ${dryRun ? 'on' : ''}`} onClick={() => setDryRun(!dryRun)}/>
                  Dry-run (no files written)
                </label>
                <button className="btn primary" onClick={runJob} disabled={running}>
                  <Icon name="play" size={12}/>{' '}
                  {runStatus === 'pending' ? 'Queued…' : running ? 'Running…' : 'Run job'}
                </button>
              </div>

              {(running || runStatus === 'completed' || runStatus === 'failed') && (
                <div style={{ marginTop: 14, padding: 12, background: 'var(--bg-deep)', borderRadius: 8, border: '1px solid var(--border)' }}>
                  <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
                    <span style={{ fontSize: 12, fontWeight: 500 }}>
                      {runStatus === 'completed' ? '✓ Completed'
                        : runStatus === 'failed'  ? '✗ Failed'
                        : 'Running pipeline…'}
                    </span>
                    <span className="num" style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'capitalize' }}>
                      {runStatus || 'starting'}
                    </span>
                  </div>
                  {(runStatus === 'pending' || runStatus === 'running') && (
                    <div className="bar"><div style={{ width: '100%', animation: 'pulse 1.5s ease-in-out infinite' }}/></div>
                  )}
                  {runStatus === 'completed' && (
                    <div style={{ marginTop: 10, fontSize: 12, color: 'var(--green)' }}>
                      Job complete · outputs visible in job history below
                    </div>
                  )}
                  {runStatus === 'failed' && runError && (
                    <div style={{ marginTop: 10, fontSize: 12, color: 'var(--red)' }}>{runError}</div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        <div style={{ gridColumn: 'span 5' }}>
          <div className="card">
            <div className="card-header"><div className="card-title">Job history</div></div>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Year</th>
                  <th>Country</th>
                  <th>Status</th>
                  <th>Run</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {!jobs && (
                  <tr><td colSpan={5} style={{ padding: '12px 16px', color: 'var(--text-faint)', fontSize: 12 }}>Loading…</td></tr>
                )}
                {jobs?.length === 0 && (
                  <tr><td colSpan={5} style={{ padding: '12px 16px', color: 'var(--text-faint)', fontSize: 12 }}>No jobs yet</td></tr>
                )}
                {jobs?.map(j => (
                  <tr key={j.id}>
                    <td className="num">{j.input?.tax_year}</td>
                    <td>{j.input?.country === 'NO' ? '🇳🇴 NO' : '🇬🇧 UK'}</td>
                    <td>
                      {j.status === 'completed' ? <span className="tag green">Completed</span>
                        : j.status === 'failed'  ? <span className="tag red">Failed</span>
                        : j.status === 'running' ? <span className="tag blue">Running</span>
                        : <span className="tag">{j.status}</span>}
                    </td>
                    <td className="muted" style={{ fontSize: 11 }}>
                      {j.created_at ? j.created_at.slice(0, 10) : '—'}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <button className="btn ghost sm"
                        disabled={downloadingJob === j.id}
                        onClick={() => downloadJob(j.id, j.input?.country)}
                        title="Download primary output (report → RF-1159 → gains CSV)">
                        <Icon name="download" size={11}/>
                        {downloadingJob === j.id ? ' …' : ''}
                      </button>
                    </td>
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


// ── Review Queue ──────────────────────────────────────────────────────────────
const ReviewQueue = ({ juris, year, addToast }) => {
  const [tab, setTab] = React.useState('unlinked');

  const { data: review, loading, reload } = window.useApi(
    () => window.API.getReviewSummary(year), [year]
  );

  const sevTag = (s) => {
    if (s === 'critical') return <span className="tag red">Critical</span>;
    if (s === 'high')     return <span className="tag amber">High</span>;
    return <span className="tag blue">Info</span>;
  };

  const unlinkedJobs   = review?.unlinked_transfer_jobs   || [];
  const missingAssets  = review?.missing_basis_detail     || [];
  const warnings       = review?.warnings                 || [];
  const missingCount   = review?.missing_basis_count      ?? 0;
  const unlinkedCount  = unlinkedJobs.length;
  const warningCount   = review?.total_warnings           ?? 0;
  const reviewTotal    = missingCount + unlinkedCount + warningCount;

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Review Queue</div>
          <div className="page-sub">
            Resolve before filing · {juris === 'NO' ? 'RF-1159' : 'SA108'} · tax year {year}
          </div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn" onClick={reload}><Icon name="refresh" size={12}/> Refresh</button>
          <button className="btn" onClick={() => addToast({ title: 'Marked reviewed', msg: 'All items acknowledged for this session · re-run a job to clear permanently' })}>
            <Icon name="check" size={12}/> Mark all reviewed
          </button>
        </div>
      </div>

      {reviewTotal > 0 && (
        <div style={{ padding: '12px 16px', background: 'oklch(0.92 0.10 75 / 0.4)', border: '1px solid oklch(0.78 0.12 75 / 0.4)', borderRadius: 10, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 10, color: 'oklch(0.42 0.14 75)' }}>
          <Icon name="alerts" size={18}/>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>{reviewTotal} item{reviewTotal !== 1 ? 's' : ''} need attention before filing</div>
            <div style={{ fontSize: 12, opacity: 0.8 }}>
              {unlinkedCount > 0 && `${unlinkedCount} unlinked transfer job(s) · `}
              {missingCount > 0  && `${missingCount} missing-basis asset(s) · `}
              {warningCount > 0  && `${warningCount} warning(s)`}
            </div>
          </div>
        </div>
      )}
      {review && reviewTotal === 0 && (
        <div style={{ padding: '12px 16px', background: 'oklch(0.32 0.10 145 / 0.8)', border: '1px solid oklch(0.45 0.14 145 / 0.6)', borderRadius: 10, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 10, color: 'oklch(0.92 0.06 145)' }}>
          <Icon name="check" size={18}/>
          <div style={{ fontWeight: 600, fontSize: 13 }}>All clear — no review items for {year}</div>
        </div>
      )}

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <KPI label="Unlinked transfers" value={loading ? '…' : String(unlinkedCount)} sub={unlinkedCount > 0 ? 'jobs need review' : 'all linked'} subKind={unlinkedCount > 0 ? 'neg' : 'pos'} sparkSeed={5}/>
        <KPI label="Missing basis" value={loading ? '…' : String(missingCount)}
          sub={missingCount > 0 ? (() => {
            const assets = review?.missing_basis_assets || [];
            const shown  = assets.slice(0, 3).map(a =>
              a.length > 14 ? a.slice(0, 6) + '…' + a.slice(-3) : a
            );
            return shown.join(', ') + (assets.length > 3 ? ` +${assets.length - 3}` : '');
          })() : 'all resolved'}
          subKind={missingCount > 0 ? 'neg' : 'pos'} sparkSeed={6}/>
        <KPI label="Warnings"           value={loading ? '…' : String(warningCount)}  sub={warningCount > 0 ? 'review required' : 'no warnings'}                                                   subKind={warningCount > 0 ? '' : 'pos'}    sparkSeed={7}/>
        <KPI label="Status"             value={loading ? '…' : (review?.clean ? 'Clean' : 'Action needed')} sub={review?.jobs_with_review != null ? `${review.jobs_with_review} job(s) scanned` : ''}  subKind={review?.clean ? 'pos' : ''} sparkSeed={8}/>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="tabs">
            <button className={tab === 'unlinked' ? 'active' : ''} onClick={() => setTab('unlinked')}>
              Unlinked transfers · {unlinkedCount}
            </button>
            <button className={tab === 'missing'  ? 'active' : ''} onClick={() => setTab('missing')}>
              Missing basis · {missingCount}
            </button>
            <button className={tab === 'warnings' ? 'active' : ''} onClick={() => setTab('warnings')}>
              Warnings · {warningCount}
            </button>
          </div>
        </div>

        {tab === 'unlinked' && (
          unlinkedJobs.length === 0
            ? <div style={{ padding: '20px 16px', color: 'var(--text-faint)', fontSize: 12 }}>No unlinked transfers for {year}</div>
            : <table className="tbl">
                <thead><tr><th>Job</th><th>Case</th><th></th></tr></thead>
                <tbody>
                  {unlinkedJobs.map((u, i) => (
                    <tr key={i}>
                      <td className="mono muted" style={{ fontSize: 11 }}>{u.job_id}</td>
                      <td style={{ fontWeight: 500 }}>{u.case_name}</td>
                      <td style={{ textAlign: 'right' }}>
                        <button className="btn sm" onClick={() => {
                          addToast({ title: 'Tip', msg: `Download job ${u.job_id.slice(0,8)}… from Tax Center to review unlinked transfers` });
                        }}>
                          <Icon name="link" size={11}/> Review
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
        )}

        {tab === 'missing' && (
          missingAssets.length === 0
            ? <div style={{ padding: '20px 16px', color: 'var(--text-faint)', fontSize: 12 }}>No missing-basis assets for {year}</div>
            : <table className="tbl">
                <thead><tr><th>Asset</th><th className="num">Missing lots</th><th className="num">Total lots</th><th></th></tr></thead>
                <tbody>
                  {missingAssets.map((m, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 500 }}>{m.asset}</td>
                      <td className="num">{m.missing_lots ?? '—'}</td>
                      <td className="num">{m.lot_count ?? '—'}</td>
                      <td style={{ textAlign: 'right' }}>
                        <button className="btn sm" onClick={() => addToast({
                          title: 'Cost basis',
                          msg: `Add a row to your Generic Events CSV with event_type=ACQUIRE and cost_basis for ${m.asset}`,
                        })}>Enter cost basis</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
        )}

        {tab === 'warnings' && (
          warnings.length === 0
            ? <div style={{ padding: '20px 16px', color: 'var(--text-faint)', fontSize: 12 }}>No warnings for {year}</div>
            : <div style={{ padding: 0 }}>
                {warnings.map((w, i) => (
                  <div key={i} style={{ padding: '12px 16px', borderBottom: i < warnings.length - 1 ? '1px solid var(--border)' : 'none' }}>
                    <div className="row" style={{ gap: 10 }}>
                      <span className="tag amber">Warning</span>
                      <span style={{ flex: 1, fontSize: 13, fontWeight: 500 }}>{w}</span>
                      <button className="btn sm" onClick={() => addToast({ title: 'Acknowledged', msg: w.slice(0, 60) })}>
                        Resolve
                      </button>
                    </div>
                  </div>
                ))}
              </div>
        )}
      </div>
    </>
  );
};

window.Sources     = Sources;
window.TaxCenter   = TaxCenter;
window.ReviewQueue = ReviewQueue;
