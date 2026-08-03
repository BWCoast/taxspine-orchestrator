// ALTANA — main app shell + router

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "density": "compact"
}/*EDITMODE-END*/;

const App = () => {
  const [route, setRoute] = React.useState('dashboard');
  const [theme, setTheme] = React.useState('dark');
  const [juris, setJuris] = React.useState('NO');
  const [year, setYear] = React.useState(2024);
  const [collapsed, setCollapsed] = React.useState(false);
  const [cmdK, setCmdK] = React.useState(false);
  const [toasts, setToasts] = React.useState([]);
  const [tweaks, setTweak] = window.useTweaks ? window.useTweaks(TWEAK_DEFAULTS) : [TWEAK_DEFAULTS, () => {}];

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
    dashboard: <Dashboard juris={juris} year={year} setYear={setYear} addToast={addToast}/>,
    sources: <Sources addToast={addToast}/>,
    tax: <TaxCenter juris={juris} year={year} addToast={addToast}/>,
    review: <ReviewQueue juris={juris} addToast={addToast}/>,
    prices: <Prices juris={juris} addToast={addToast}/>,
    portfolio: <Portfolio juris={juris} year={year}/>,
    charts: <Charts juris={juris}/>,
    audit: <Audit/>,
    diagnostics: <Diagnostics/>,
    alerts: <Alerts addToast={addToast}/>,
    settings: <Settings juris={juris} setJuris={setJuris}/>,
  };

  return (
    <>
      <div className="app" data-screen-label={`ALTANA · ${route}`}>
        <Sidebar route={route} setRoute={setRoute} collapsed={collapsed}/>
        <div className="main">
          <Topbar route={route} juris={juris} setJuris={setJuris}
                  theme={theme} toggleTheme={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
                  openCmdK={() => setCmdK(true)} openModal={() => {}}
                  collapsed={collapsed} setCollapsed={setCollapsed}/>
          <main className="content">{pages[route]}</main>
        </div>
      </div>
      {cmdK && <CommandPalette onClose={() => setCmdK(false)}/>}
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
