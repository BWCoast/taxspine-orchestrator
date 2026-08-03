// ALTANA — main app shell + router

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "density": "compact"
}/*EDITMODE-END*/;

const App = () => {
  const [route, setRoute] = React.useState('dashboard');
  const [theme, setTheme] = React.useState('dark');
  const [juris, setJuris] = React.useState('NO');
  const [year, setYear] = React.useState(new Date().getFullYear() - 1); // default to most recent full tax year
  const [collapsed, setCollapsed] = React.useState(false);
  const [cmdK, setCmdK] = React.useState(false);
  const [toasts, setToasts] = React.useState([]);
  const [tweaks, setTweak] = window.useTweaks ? window.useTweaks(TWEAK_DEFAULTS) : [TWEAK_DEFAULTS, () => {}];
  // Per-workspace user display name — stored locally in localStorage.
  // Independent per workspace (default workspace vs named workspaces).
  const [userName, setUserNameState] = React.useState(() => window.API.getUserName() || '');
  const setUserName = (name) => { window.API.setUserName(name); setUserNameState(name || ''); };

  // Workspace display name — used in Topbar avatar + Dashboard greeting.
  // Only set when a *named* workspace is explicitly active (non-null workspace_id).
  // Default workspace (null) intentionally returns null so Dashboard shows "Dashboard".
  const { data: workspaces } = window.useApi(() => window.API.getWorkspaces(), []);
  const activeWsId = window.API.getWorkspaceId();
  const wsName = activeWsId
    ? (workspaces?.find(w => w.workspace_id === activeWsId)?.display_name ?? activeWsId)
    : null;

  // ── Shared signals — 10 s polling, one fetch for the whole app ─────────────
  // Pages that need these receive them as props. They do NOT fetch independently.
  // Because every component reads the same object, a poll tick updates every
  // visible signal simultaneously — no staggered freshness.
  const POLL = 10_000;
  const { data: sharedDiags,     error: diagsErr,   reload: reloadDiags    } =
    window.useApi(() => window.API.getDiagnostics(),                           [],     { pollInterval: POLL });
  const { data: sharedWorkspace, error: wsErr,      reload: reloadWorkspace } =
    window.useApi(() => window.API.getWorkspace(),                             [],     { pollInterval: POLL });
  const { data: sharedJobs,      error: jobsErr,    reload: reloadJobs     } =
    window.useApi(() => window.API.getJobs({ limit: 50 }),                    [],     { pollInterval: POLL });
  const { data: sharedRunning,   error: runErr                              } =
    window.useApi(() => window.API.getJobs({ status: 'running', limit: 50 }), [],     { pollInterval: POLL });
  const { data: sharedReview,    error: reviewErr,  reload: reloadReview   } =
    window.useApi(() => window.API.getReviewSummary(year),                    [year], { pollInterval: POLL });

  // Bell red dot: any shared signal currently failing to refresh.
  const pollError    = !!(diagsErr || wsErr || jobsErr || runErr || reviewErr);
  // Alerts badge for Topbar — failed jobs in this workspace.
  const alertsCount  = sharedJobs ? sharedJobs.filter(j => j.status === 'failed').length : 0;

  React.useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);
  React.useEffect(() => {
    document.documentElement.setAttribute('data-density', tweaks.density || 'compact');
  }, [tweaks.density]);

  React.useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); setCmdK(true); }
      if (e.key === 'Escape') setCmdK(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const addToast = (t) => {
    const id = Math.random().toString(36).slice(2);
    setToasts(prev => [...prev, { id, ...t }]);
    setTimeout(() => setToasts(prev => prev.filter(x => x.id !== id)), 4000);
  };
  const dismiss = (id) => setToasts(prev => prev.filter(x => x.id !== id));

  const pages = {
    dashboard:   <Dashboard juris={juris} year={year} setYear={setYear} addToast={addToast} wsName={wsName} userName={userName} setRoute={setRoute}
                            diags={sharedDiags} wsConfig={sharedWorkspace} recentJobs={sharedJobs} runningJobs={sharedRunning} review={sharedReview}/>,
    sources:     <Sources addToast={addToast} setRoute={setRoute} reloadWorkspace={reloadWorkspace}/>,
    tax:         <TaxCenter juris={juris} setJuris={setJuris} year={year} addToast={addToast}
                            diags={sharedDiags} sharedJobs={sharedJobs} reloadJobs={reloadJobs} reloadDiags={reloadDiags}/>,
    review:      <ReviewQueue juris={juris} year={year} addToast={addToast} setRoute={setRoute}
                              review={sharedReview} reloadReview={reloadReview}/>,
    prices:      <Prices juris={juris} year={year} addToast={addToast}
                         diags={sharedDiags} wsConfig={sharedWorkspace} reloadDiags={reloadDiags} reloadWorkspace={reloadWorkspace}/>,
    portfolio:   <Portfolio juris={juris} year={year} addToast={addToast}/>,
    charts:      <Charts juris={juris} diags={sharedDiags}/>,
    audit:       <Audit jobs={sharedJobs}/>,
    diagnostics: <Diagnostics diags={sharedDiags} diagsErr={diagsErr} reloadDiags={reloadDiags} pollError={pollError} sharedWorkspace={sharedWorkspace}/>,
    alerts:      <Alerts addToast={addToast} sharedJobs={sharedJobs}/>,
    settings:    <Settings juris={juris} setJuris={setJuris} userName={userName} setUserName={setUserName}
                           wsConfig={sharedWorkspace} reloadWorkspace={reloadWorkspace}/>,
  };

  return (
    <>
      <div className="app" data-screen-label={`ALTANA · ${route}`}>
        <Sidebar route={route} setRoute={setRoute} collapsed={collapsed}
                 diags={sharedDiags} wsJobs={sharedJobs} review={sharedReview}/>
        <div className="main">
          <Topbar route={route} setRoute={setRoute} juris={juris} setJuris={setJuris}
                  theme={theme} toggleTheme={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
                  openCmdK={() => setCmdK(true)}
                  collapsed={collapsed} setCollapsed={setCollapsed}
                  wsName={wsName} userName={userName}
                  pollError={pollError} alertsCount={alertsCount}/>
          <main className="content">{pages[route]}</main>
        </div>
      </div>
      {cmdK && <CommandPalette onClose={() => setCmdK(false)} setRoute={setRoute}/>}
      <Toast items={toasts} dismiss={dismiss}/>
      {window.TweaksPanel && (
        <window.TweaksPanel title="Tweaks">
          <window.TweakSection title="Display">
            <window.TweakRadio label="Table density" value={tweaks.density || 'compact'}
                               onChange={(v) => setTweak('density', v)}
                               options={[
                                 { value: 'comfortable', label: 'Comfortable' },
                                 { value: 'compact', label: 'Compact' },
                                 { value: 'ultra', label: 'Ultra' },
                               ]}/>
          </window.TweakSection>
        </window.TweaksPanel>
      )}
    </>
  );
};

ReactDOM.createRoot(document.getElementById('root')).render(<App/>);
