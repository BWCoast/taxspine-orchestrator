// ALTANA — Shell components

// Nav structure — badges injected dynamically in Sidebar from live data
const NAV = [
  { id: 'dashboard', label: 'Dashboard',    icon: 'dashboard'   },
  { id: 'sources',   label: 'Data Sources', icon: 'sources'     },
  { id: 'tax',       label: 'Tax Center',   icon: 'tax'         },
  { id: 'review',    label: 'Review Queue', icon: 'review'      },  // badge from API
  { id: 'prices',    label: 'Prices',       icon: 'prices'      },
  { id: 'portfolio', label: 'Portfolio',    icon: 'portfolio'   },
  { id: 'charts',    label: 'Charts',       icon: 'charts'      },
  { id: 'audit',     label: 'Audit Log',    icon: 'audit'       },
  { id: 'diagnostics', label: 'Diagnostics', icon: 'diagnostics' },
  { id: 'alerts',    label: 'Alerts',       icon: 'alerts'      },  // badge from API
  { id: 'settings',  label: 'Settings',     icon: 'settings'    },
];

const Sidebar = ({ route, setRoute, collapsed, diags, wsJobs, review }) => {
  // diags, wsJobs, review — all shared from App (10 s polling).
  // No local fetches here — every signal updates at the same instant as the rest of the UI.

  // ── Health dots ──────────────────────────────────────────────────────────
  const lotStatus   = diags?.lots?.db_exists ? 'green' : (diags ? 'red'   : 'green');
  const priceStatus = (diags?.prices?.combined_csvs?.length ?? 0) > 0 || diags?.prices?.prices_db?.exists
                      ? 'green' : (diags ? 'amber' : 'green');
  const dedupStatus = (diags?.dedup?.total_skips ?? 0) > 500 ? 'amber' : 'green';
  // Global running count from diagnostics + workspace-scoped running from wsJobs.
  // wsJobs updates within 3 s of a job dispatch (TaxCenter calls reloadJobs immediately),
  // while diags polls every 10 s — combining both gives fastest visual feedback.
  const jobsRunning    = diags?.jobs?.running ?? 0;
  const wsJobsRunning  = wsJobs ? wsJobs.filter(j => j.status === 'running').length : 0;
  const wsFailed       = wsJobs ? wsJobs.filter(j => j.status === 'failed').length  : 0;
  const anyRunning     = jobsRunning + wsJobsRunning;
  const jobStatus      = anyRunning > 0 ? 'amber' : wsFailed > 0 ? 'amber' : 'green';
  const jobsLabel      = anyRunning  > 0 ? `${anyRunning} running`
                       : wsFailed    > 0 ? `${wsFailed} failed`
                       : `${jobsRunning} running`;

  const priceAge   = diags?.prices?.combined_csvs?.[0]?.age_hours != null
    ? `${diags.prices.combined_csvs[0].age_hours}h ago` : '—';
  const dedupSkips = diags?.dedup?.total_skips ?? 0;

  // ── Dynamic nav badges ───────────────────────────────────────────────────
  // Review: missing basis assets + unlinked transfer jobs (amber — needs attention before filing)
  const reviewIssues = review
    ? (review.missing_basis_count || 0) + (review.has_unlinked_transfers ? (review.unlinked_transfer_jobs?.length || 1) : 0)
    : null;
  const reviewBadge = reviewIssues > 0 ? Math.min(reviewIssues, 99) : null;

  // Alerts: failed jobs in THIS workspace only (red — requires action).
  // Use wsJobs (workspace-scoped) so the badge matches what Alerts page shows.
  const failedJobs  = wsJobs ? wsJobs.filter(j => j.status === 'failed').length : 0;
  const alertsBadge = failedJobs > 0 ? Math.min(failedJobs, 99) : null;

  const badgeFor = id => {
    if (id === 'review') return reviewBadge ? { badge: reviewBadge, badgeKind: 'amber' } : {};
    if (id === 'alerts') return alertsBadge ? { badge: alertsBadge, badgeKind: 'red'   } : {};
    return {};
  };

  const NavItem = ({ item }) => {
    const { badge, badgeKind } = badgeFor(item.id);
    return (
      <a className={`nav-item ${route === item.id ? 'active' : ''}`}
         onClick={(e) => { e.preventDefault(); setRoute(item.id); }} href="#">
        <span className="nav-icon"><Icon name={item.icon}/></span>
        <span className="nav-label">{item.label}</span>
        {badge && <span className={`nav-badge ${badgeKind}`}>{badge}</span>}
      </a>
    );
  };

  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="brand">
        <div className="brand-mark">
          {/* Ledger mark — official geometry: 100×100 grid, 5:7 bar ratio */}
          <svg viewBox="0 0 100 100" width="22" height="22" aria-hidden="true" style={{color: 'var(--text)'}}>
            <rect x="22" y="36" width="40" height="10" rx="2" fill="currentColor"/>
            <rect x="22" y="54" width="56" height="10" rx="2" fill="currentColor"/>
          </svg>
        </div>
        <div className="brand-name">ALTANA</div>
      </div>
      <div className="nav-section-label">Workspace</div>
      {NAV.slice(0, 7).map(item => <NavItem key={item.id} item={item}/>)}
      <div className="nav-section-label">System</div>
      {NAV.slice(7).map(item => <NavItem key={item.id} item={item}/>)}
      <div className="sidebar-footer">
        <div className="health-row"><span className={`dot ${lotStatus}`}/> Lot store</div>
        <div className="health-row"><span className={`dot ${priceStatus}`}/> Prices · {priceAge}</div>
        <div className="health-row"><span className={`dot ${dedupStatus}`}/> Dedup · {dedupSkips} skips</div>
        <div className="health-row"><span className={`dot ${jobStatus}`}/> Jobs · {jobsLabel}</div>
      </div>
    </aside>
  );
};

const WorkspaceSwitcher = () => {
  const [open, setOpen]             = React.useState(false);
  const [dropPos, setDropPos]       = React.useState({ top: 0, right: 0 });
  const [creating, setCreating]     = React.useState(false);
  const [newId, setNewId]           = React.useState('');
  const [newName, setNewName]       = React.useState('');
  const [error, setError]           = React.useState('');
  const btnRef = React.useRef(null);
  const { data: workspaces, reload } = window.useApi(() => window.API.getWorkspaces(), []);
  const activeId   = window.API.getWorkspaceId();
  const activeName = activeId
    ? (workspaces?.find(w => w.workspace_id === activeId)?.display_name ?? activeId)
    : 'My workspace';

  const handleToggle = () => {
    if (!open && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect();
      setDropPos({ top: r.bottom + 6, right: window.innerWidth - r.right });
    }
    setOpen(o => !o);
  };

  const switchTo = (id) => {
    window.API.setWorkspaceId(id);
    window.location.reload();
  };

  const handleCreate = async () => {
    setError('');
    if (!newId.match(/^[a-z][a-z0-9_-]{0,37}$/)) {
      setError('ID: lowercase letters, digits, hyphens. Start with a letter.');
      return;
    }
    try {
      await window.API.createWorkspace({ workspace_id: newId, display_name: newName || newId });
      reload();
      setCreating(false);
      setNewId(''); setNewName('');
      window.API.setWorkspaceId(newId);
      window.location.reload();
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  return (
    <div style={{position: 'relative'}}>
      <button
        ref={btnRef}
        type="button"
        className="juris-pill tip"
        data-tip="Switch workspace"
        onClick={handleToggle}
        style={{gap: 6}}
      >
        <Icon name="workspace" size={13}/>
        <span>{activeName}</span>
        <Icon name={open ? 'chevron_up' : 'chevron_down'} size={10}/>
      </button>

      {open && (
        <div
          style={{
            position: 'fixed', top: dropPos.top, right: dropPos.right,
            background: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: 8, minWidth: 220, zIndex: 9999,
            boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
            padding: '6px 0',
          }}
          onClick={e => e.stopPropagation()}
        >
          {/* Default workspace */}
          <div
            className={`nav-item ${activeId === null ? 'active' : ''}`}
            style={{padding: '7px 14px', cursor: 'pointer', fontSize: 13}}
            onClick={() => switchTo(null)}
          >
            <div style={{display: 'flex', alignItems: 'center', gap: 6}}>
              My workspace
              {activeId === null && <span className="tag green" style={{fontSize: 10}}>active</span>}
            </div>
            <div style={{fontSize: 10, color: 'var(--text-faint)', marginTop: 1}}>Default — all data lives here</div>
          </div>

          {/* Named workspaces */}
          {(workspaces ?? []).map(ws => (
            <div
              key={ws.workspace_id}
              className={`nav-item ${activeId === ws.workspace_id ? 'active' : ''}`}
              style={{padding: '7px 14px', cursor: 'pointer', fontSize: 13}}
              onClick={() => switchTo(ws.workspace_id)}
            >
              {ws.display_name}
              {activeId === ws.workspace_id && (
                <span className="tag green" style={{marginLeft: 8, fontSize: 10}}>active</span>
              )}
            </div>
          ))}

          <div style={{height: 1, background: 'var(--border)', margin: '6px 0'}}/>

          {/* Create new workspace */}
          {!creating ? (
            <div
              style={{padding: '7px 14px', cursor: 'pointer', fontSize: 13, color: 'var(--accent)'}}
              onClick={() => setCreating(true)}
            >
              <Icon name="plus" size={11}/> New workspace
            </div>
          ) : (
            <div style={{padding: '8px 14px', display: 'flex', flexDirection: 'column', gap: 6}}>
              <input
                autoFocus
                placeholder="id (e.g. alice)"
                value={newId}
                onChange={e => setNewId(e.target.value.toLowerCase())}
                style={{
                  background: 'var(--surface-hover)', border: '1px solid var(--border)',
                  borderRadius: 4, padding: '4px 8px', fontSize: 12, color: 'var(--text)',
                  outline: 'none', width: '100%',
                }}
              />
              <input
                placeholder="Display name (e.g. Alice)"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                style={{
                  background: 'var(--surface-hover)', border: '1px solid var(--border)',
                  borderRadius: 4, padding: '4px 8px', fontSize: 12, color: 'var(--text)',
                  outline: 'none', width: '100%',
                }}
              />
              {error && <div style={{fontSize: 11, color: 'var(--red)'}}>{error}</div>}
              <div style={{display: 'flex', gap: 6}}>
                <button type="button" className="btn primary sm" onClick={handleCreate}>Create</button>
                <button type="button" className="btn ghost sm" onClick={() => { setCreating(false); setNewId(''); setNewName(''); setError(''); }}>Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Click-outside overlay */}
      {open && (
        <div
          style={{position: 'fixed', inset: 0, zIndex: 9998}}
          onClick={() => setOpen(false)}
        />
      )}
    </div>
  );
};

const Topbar = ({ route, setRoute, juris, setJuris, theme, toggleTheme, openCmdK, collapsed, setCollapsed, wsName, userName, pollError, alertsCount }) => {
  const titles = {
    dashboard: 'Dashboard', sources: 'Data Sources', tax: 'Tax Center',
    review: 'Review Queue', prices: 'Prices', portfolio: 'Portfolio',
    charts: 'Charts', audit: 'Audit Log', diagnostics: 'Diagnostics',
    alerts: 'Alerts Center', settings: 'Settings',
  };
  // Avatar: prefer per-workspace userName, fall back to wsName
  const displayName = userName || wsName;
  const initials = displayName
    ? displayName.trim().split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase()
    : '—';

  return (
    <header className="topbar">
      <button className="topbar-collapse" onClick={() => setCollapsed(!collapsed)} aria-label="Toggle sidebar">
        <Icon name={collapsed ? 'chevron_right' : 'chevron_left'}/>
      </button>
      <div className="crumbs">
        <span>Workspace</span>
        <span className="sep">/</span>
        <strong>{titles[route] || route}</strong>
      </div>
      <button className="cmdk" onClick={openCmdK}>
        <Icon name="search"/>
        <span>Jump to anything…</span>
        <span className="cmdk-kbd">⌘K</span>
      </button>
      <div className="topbar-right">
        <WorkspaceSwitcher/>
        <button className="juris-pill tip" data-tip="Switch jurisdiction" onClick={() => setJuris(juris === 'NO' ? 'UK' : 'NO')}>
          <span className="flag">{juris === 'NO' ? '🇳🇴' : '🇬🇧'}</span>
          <span>{juris === 'NO' ? 'Norway' : 'United Kingdom'}</span>
          <span className="ccy">{juris === 'NO' ? 'NOK' : 'GBP'}</span>
        </button>
        <button className="icon-btn tip" data-tip="Toggle theme" onClick={toggleTheme}>
          <Icon name={theme === 'dark' ? 'sun' : 'moon'}/>
        </button>
        <button className="icon-btn tip"
          data-tip={pollError
            ? 'Backend connectivity issue — check Diagnostics'
            : alertsCount > 0
              ? `${alertsCount} failed job${alertsCount !== 1 ? 's' : ''}`
              : 'Alerts'}
          onClick={() => setRoute(pollError ? 'diagnostics' : 'alerts')}>
          <Icon name="bell"/>
          {(pollError || alertsCount > 0) && (
            <span className="pip" style={pollError ? {background: 'var(--red)'} : {}}/>
          )}
        </button>
        <div className="avatar" title={displayName || 'Workspace'}>{initials}</div>
      </div>
    </header>
  );
};

const CMDK_ACTIONS = [
  { icon: 'play',        label: 'Run new tax job',       route: 'tax'         },
  { icon: 'upload',      label: 'Upload CSV / XLSX',      route: 'sources'     },
  { icon: 'refresh',     label: 'Sync all sources',       route: 'sources'     },
  { icon: 'review',      label: 'Open Review Queue',      route: 'review'      },
  { icon: 'settings',    label: 'Workspace settings',     route: 'settings'    },
  { icon: 'dashboard',   label: 'Dashboard',              route: 'dashboard'   },
  { icon: 'tax',         label: 'Tax Center',             route: 'tax'         },
  { icon: 'prices',      label: 'Prices & Valuation',     route: 'prices'      },
  { icon: 'portfolio',   label: 'Portfolio',              route: 'portfolio'   },
  { icon: 'charts',      label: 'Charts',                 route: 'charts'      },
  { icon: 'audit',       label: 'Audit Log',              route: 'audit'       },
  { icon: 'diagnostics', label: 'Diagnostics',            route: 'diagnostics' },
  { icon: 'alerts',      label: 'Alerts Center',          route: 'alerts'      },
];

const CommandPalette = ({ onClose, setRoute }) => {
  const [query, setQuery]   = React.useState('');
  const [sel, setSel]       = React.useState(0);

  const filtered = query.trim()
    ? CMDK_ACTIONS.filter(a => a.label.toLowerCase().includes(query.toLowerCase()))
    : CMDK_ACTIONS.slice(0, 5);

  const navigate = (route) => { setRoute(route); onClose(); };

  const onKey = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setSel(s => Math.min(s + 1, filtered.length - 1)); }
    if (e.key === 'ArrowUp')   { e.preventDefault(); setSel(s => Math.max(s - 1, 0)); }
    if (e.key === 'Enter' && filtered[sel]) navigate(filtered[sel].route);
    if (e.key === 'Escape') onClose();
  };

  // Reset selection when results change
  React.useEffect(() => setSel(0), [query]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" style={{width: 'min(620px, 92vw)'}} onClick={e => e.stopPropagation()}>
        <div style={{padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 10, alignItems: 'center'}}>
          <Icon name="search" size={18}/>
          <input
            autoFocus
            placeholder="Search pages, actions…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={onKey}
            style={{flex: 1, background: 'transparent', border: 'none', outline: 'none', fontSize: 14, color: 'var(--text)'}}
          />
          <span className="cmdk-kbd" style={{cursor: 'pointer'}} onClick={onClose}>esc</span>
        </div>
        <div style={{padding: '10px 8px', minHeight: 200, color: 'var(--text-faint)'}}>
          <div style={{padding: '4px 12px', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2}}>
            {query.trim() ? `${filtered.length} result${filtered.length !== 1 ? 's' : ''}` : 'Suggested'}
          </div>
          {filtered.length === 0 && (
            <div style={{padding: '24px 12px', textAlign: 'center', fontSize: 12}}>No results for "{query}"</div>
          )}
          {filtered.map((it, i) => (
            <div
              key={i}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '9px 12px', borderRadius: 6, cursor: 'pointer',
                color: 'var(--text)',
                background: i === sel ? 'var(--surface-hover)' : 'transparent',
              }}
              onMouseEnter={() => setSel(i)}
              onClick={() => navigate(it.route)}
            >
              <Icon name={it.icon}/>
              <span style={{flex: 1, fontSize: 13}}>{it.label}</span>
            </div>
          ))}
        </div>
        <div style={{padding: '10px 14px', borderTop: '1px solid var(--border)', fontSize: 11, color: 'var(--text-faint)', display: 'flex', gap: 14}}>
          <span><span className="cmdk-kbd">↑↓</span> navigate</span>
          <span><span className="cmdk-kbd">↵</span> open</span>
          <span><span className="cmdk-kbd">esc</span> close</span>
        </div>
      </div>
    </div>
  );
};

const Toast = ({ items, dismiss }) => (
  <div className="toast-rail">
    {items.map(t => (
      <div key={t.id} className={`toast ${t.kind || ''}`}>
        <div style={{flex: 1}}>
          <div className="toast-title">{t.title}</div>
          {t.msg && <div className="toast-msg">{t.msg}</div>}
        </div>
        <button className="icon-btn" style={{width: 24, height: 24}} onClick={() => dismiss(t.id)}><Icon name="x" size={12}/></button>
      </div>
    ))}
  </div>
);

window.Sidebar = Sidebar;
window.Topbar = Topbar;
window.WorkspaceSwitcher = WorkspaceSwitcher;
window.CommandPalette = CommandPalette;
window.Toast = Toast;
