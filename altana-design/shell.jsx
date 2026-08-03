// ALTANA — Shell components

const NAV = [
  { id: 'dashboard', label: 'Dashboard', icon: 'dashboard' },
  { id: 'sources', label: 'Data Sources', icon: 'sources' },
  { id: 'tax', label: 'Tax Center', icon: 'tax' },
  { id: 'review', label: 'Review Queue', icon: 'review', badge: 5, badgeKind: 'amber' },
  { id: 'prices', label: 'Prices', icon: 'prices' },
  { id: 'portfolio', label: 'Portfolio', icon: 'portfolio' },
  { id: 'charts', label: 'Charts', icon: 'charts' },
  { id: 'audit', label: 'Audit Log', icon: 'audit' },
  { id: 'diagnostics', label: 'Diagnostics', icon: 'diagnostics' },
  { id: 'alerts', label: 'Alerts', icon: 'alerts', badge: 2, badgeKind: 'red' },
  { id: 'settings', label: 'Settings', icon: 'settings' },
];

const Sidebar = ({ route, setRoute, collapsed }) => (
  <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
    <div className="brand">
      <div className="brand-mark">T</div>
      <div className="brand-name">Tax<span>nor</span></div>
    </div>
    <div className="nav-section-label">Workspace</div>
    {NAV.slice(0, 7).map(item => (
      <a key={item.id} className={`nav-item ${route === item.id ? 'active' : ''}`}
         onClick={(e) => { e.preventDefault(); setRoute(item.id); }} href="#">
        <span className="nav-icon"><Icon name={item.icon}/></span>
        <span className="nav-label">{item.label}</span>
        {item.badge && <span className={`nav-badge ${item.badgeKind}`}>{item.badge}</span>}
      </a>
    ))}
    <div className="nav-section-label">System</div>
    {NAV.slice(7).map(item => (
      <a key={item.id} className={`nav-item ${route === item.id ? 'active' : ''}`}
         onClick={(e) => { e.preventDefault(); setRoute(item.id); }} href="#">
        <span className="nav-icon"><Icon name={item.icon}/></span>
        <span className="nav-label">{item.label}</span>
        {item.badge && <span className={`nav-badge ${item.badgeKind}`}>{item.badge}</span>}
      </a>
    ))}
    <div className="sidebar-footer">
      <div className="health-row"><span className="dot green"/> Lot store</div>
      <div className="health-row"><span className="dot green"/> Prices · 12m ago</div>
      <div className="health-row"><span className="dot amber"/> Dedup · 32 items</div>
      <div className="health-row"><span className="dot green"/> Bot · 8m ago</div>
    </div>
  </aside>
);

const Topbar = ({ route, juris, setJuris, theme, toggleTheme, openCmdK, openModal, collapsed, setCollapsed }) => {
  const titles = {
    dashboard: 'Dashboard', sources: 'Data Sources', tax: 'Tax Center',
    review: 'Review Queue', prices: 'Prices', portfolio: 'Portfolio',
    charts: 'Charts', audit: 'Audit Log', diagnostics: 'Diagnostics',
    alerts: 'Alerts Center', settings: 'Settings',
  };
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
        <button className="juris-pill tip" data-tip="Switch jurisdiction" onClick={() => setJuris(juris === 'NO' ? 'UK' : 'NO')}>
          <span className="flag">{juris === 'NO' ? '🇳🇴' : '🇬🇧'}</span>
          <span>{juris === 'NO' ? 'Norway' : 'United Kingdom'}</span>
          <span className="ccy">{juris === 'NO' ? 'NOK' : 'GBP'}</span>
        </button>
        <button className="icon-btn tip" data-tip="Theme" onClick={toggleTheme}>
          <Icon name={theme === 'dark' ? 'sun' : 'moon'}/>
        </button>
        <button className="icon-btn tip" data-tip="Notifications" onClick={() => openModal('alerts')}>
          <Icon name="bell"/>
          <span className="pip"/>
        </button>
        <button className="icon-btn tip" data-tip="Settings"><Icon name="settings"/></button>
        <div className="avatar">HJ</div>
      </div>
    </header>
  );
};

const CommandPalette = ({ onClose }) => (
  <div className="modal-backdrop" onClick={onClose}>
    <div className="modal" style={{width: 'min(620px, 92vw)'}} onClick={e => e.stopPropagation()}>
      <div style={{padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 10, alignItems: 'center'}}>
        <Icon name="search" size={18}/>
        <input autoFocus placeholder="Type to search jobs, assets, settings…"
               style={{flex: 1, background: 'transparent', border: 'none', outline: 'none', fontSize: 14}}/>
        <span className="cmdk-kbd">esc</span>
      </div>
      <div style={{padding: '10px 8px', minHeight: 240, color: 'var(--text-faint)'}}>
        <div style={{padding: '4px 12px', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em'}}>Suggested</div>
        {[
          { icon: 'play', label: 'Run new tax job', kbd: '⌘N' },
          { icon: 'upload', label: 'Upload CSV / XLSX', kbd: '⌘U' },
          { icon: 'refresh', label: 'Sync all sources', kbd: '⌘R' },
          { icon: 'review', label: 'Open Review Queue', kbd: '⌘R Q' },
          { icon: 'settings', label: 'Workspace settings', kbd: null },
        ].map((it, i) => (
          <div key={i} style={{display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', borderRadius: 6, color: 'var(--text)', cursor: 'pointer'}}
               onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-hover)'}
               onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
            <Icon name={it.icon}/>
            <span style={{flex: 1, fontSize: 13}}>{it.label}</span>
            {it.kbd && <span className="cmdk-kbd">{it.kbd}</span>}
          </div>
        ))}
      </div>
      <div style={{padding: '10px 14px', borderTop: '1px solid var(--border)', fontSize: 11, color: 'var(--text-faint)', display: 'flex', gap: 14}}>
        <span><span className="cmdk-kbd">↑↓</span> navigate</span>
        <span><span className="cmdk-kbd">↵</span> select</span>
        <span><span className="cmdk-kbd">esc</span> close</span>
      </div>
    </div>
  </div>
);

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
window.CommandPalette = CommandPalette;
window.Toast = Toast;
