// TAXNOR — Icon set (16x16, line, simple, currentColor)
const Icon = ({ name, size = 16 }) => {
  const paths = {
    dashboard: <><rect x="2" y="2" width="5.5" height="5.5" rx="1"/><rect x="8.5" y="2" width="5.5" height="5.5" rx="1"/><rect x="2" y="8.5" width="5.5" height="5.5" rx="1"/><rect x="8.5" y="8.5" width="5.5" height="5.5" rx="1"/></>,
    sources: <><path d="M2 4 L8 1.5 L14 4 L8 6.5 Z"/><path d="M2 8 L8 10.5 L14 8"/><path d="M2 12 L8 14.5 L14 12"/></>,
    tax: <><rect x="2.5" y="2" width="11" height="12" rx="1"/><path d="M5 5h6M5 8h6M5 11h4"/></>,
    review: <><circle cx="8" cy="8" r="6"/><path d="M8 5v3.5M8 11v0.01" strokeLinecap="round"/></>,
    prices: <><path d="M2 13 L5 9 L8 11 L11 5 L14 7"/><circle cx="14" cy="7" r="0.5" fill="currentColor"/></>,
    portfolio: <><circle cx="8" cy="8" r="6"/><path d="M8 2 A6 6 0 0 1 13.5 9.5 L8 8 Z" fill="currentColor" opacity="0.3" stroke="none"/></>,
    charts: <><path d="M2 14 V2"/><path d="M2 14 H14"/><rect x="4" y="9" width="2" height="5" fill="currentColor" opacity="0.3"/><rect x="7.5" y="6" width="2" height="8" fill="currentColor" opacity="0.5"/><rect x="11" y="3" width="2" height="11" fill="currentColor" opacity="0.7"/></>,
    audit: <><path d="M3 2h7l3 3v9a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z"/><path d="M5 7h6M5 10h6M5 13h4"/></>,
    diagnostics: <><circle cx="8" cy="8" r="6"/><path d="M5 8 L7 10 L11 6"/></>,
    alerts: <><path d="M8 1.5 L8 1.5 a4.5 4.5 0 0 1 4.5 4.5 v3.5 L14 11.5 H2 L3.5 9.5 V6 A4.5 4.5 0 0 1 8 1.5 z"/><path d="M6.5 13.5 a1.5 1.5 0 0 0 3 0"/></>,
    settings: <><circle cx="8" cy="8" r="2"/><path d="M8 1v2M8 13v2M3.05 3.05l1.4 1.4M11.55 11.55l1.4 1.4M1 8h2M13 8h2M3.05 12.95l1.4-1.4M11.55 4.45l1.4-1.4"/></>,
    search: <><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5 L14 14"/></>,
    bell: <><path d="M8 1.5 a4.5 4.5 0 0 1 4.5 4.5 v3.5 L14 11.5 H2 L3.5 9.5 V6 A4.5 4.5 0 0 1 8 1.5 z"/><path d="M6.5 13.5 a1.5 1.5 0 0 0 3 0"/></>,
    sun: <><circle cx="8" cy="8" r="3"/><path d="M8 1v1.5M8 13.5V15M2.5 2.5l1 1M12.5 12.5l1 1M1 8h1.5M13.5 8H15M2.5 13.5l1-1M12.5 3.5l1-1"/></>,
    moon: <><path d="M13 9.5A5.5 5.5 0 1 1 6.5 3 A4.5 4.5 0 0 0 13 9.5z" fill="currentColor" opacity="0.15"/><path d="M13 9.5A5.5 5.5 0 1 1 6.5 3 A4.5 4.5 0 0 0 13 9.5z"/></>,
    chevron_left: <path d="M9.5 4 L5.5 8 L9.5 12"/>,
    chevron_right: <path d="M6 4 L10 8 L6 12"/>,
    chevron_down: <path d="M4 6 L8 10 L12 6"/>,
    chevron_up: <path d="M4 10 L8 6 L12 10"/>,
    plus: <><path d="M8 3v10M3 8h10"/></>,
    upload: <><path d="M8 1.5 V10 M4.5 5 L8 1.5 L11.5 5"/><path d="M2 11 V13 a1.5 1.5 0 0 0 1.5 1.5 H12.5 A1.5 1.5 0 0 0 14 13 V11"/></>,
    download: <><path d="M8 12 V2 M4.5 8.5 L8 12 L11.5 8.5"/><path d="M2 11 V13 a1.5 1.5 0 0 0 1.5 1.5 H12.5 A1.5 1.5 0 0 0 14 13 V11"/></>,
    refresh: <><path d="M13.5 3.5 V6.5 H10.5"/><path d="M13.5 6.5 A5.5 5.5 0 1 0 13 11"/></>,
    play: <path d="M5 3 L5 13 L13 8 Z"/>,
    check: <path d="M3 8.5 L6.5 12 L13 4.5"/>,
    x: <><path d="M3.5 3.5 L12.5 12.5 M12.5 3.5 L3.5 12.5"/></>,
    info: <><circle cx="8" cy="8" r="6"/><path d="M8 7v4M8 5v0.01"/></>,
    flag_no: <text x="0" y="13" fontSize="13">🇳🇴</text>,
    flag_uk: <text x="0" y="13" fontSize="13">🇬🇧</text>,
    dot: <circle cx="8" cy="8" r="3" fill="currentColor"/>,
    more: <><circle cx="3.5" cy="8" r="1" fill="currentColor"/><circle cx="8" cy="8" r="1" fill="currentColor"/><circle cx="12.5" cy="8" r="1" fill="currentColor"/></>,
    expand: <><path d="M2 5 V2 H5 M11 2 H14 V5 M14 11 V14 H11 M5 14 H2 V11"/></>,
    link: <><path d="M6.5 9.5 L9.5 6.5"/><path d="M9 4.5 L10.5 3 a2.12 2.12 0 0 1 3 3 L12 7.5"/><path d="M7 8.5 L5.5 10 a2.12 2.12 0 0 1 -3 -3 L4 5.5"/></>,
    file: <><path d="M3 1.5 H9 L13 5.5 V14 a0.5 0.5 0 0 1 -0.5 0.5 H3 a0.5 0.5 0 0 1 -0.5 -0.5 V2 a0.5 0.5 0 0 1 0.5 -0.5z"/><path d="M9 1.5 V5.5 H13"/></>,
    wallet: <><rect x="2" y="4" width="12" height="9" rx="1.5"/><path d="M2 7 H14"/><circle cx="11" cy="10" r="0.8" fill="currentColor"/></>,
    cmd: <><path d="M5.5 3 H10.5 a2.5 2.5 0 0 1 0 5 H5.5 a2.5 2.5 0 0 1 0 -5z M5.5 8 H10.5 a2.5 2.5 0 0 1 0 5 H5.5 a2.5 2.5 0 0 1 0 -5z"/></>,
    workspace: <><rect x="1.5" y="2" width="13" height="3.5" rx="1"/><rect x="1.5" y="7" width="5.5" height="3.5" rx="1"/><rect x="9" y="7" width="5.5" height="3.5" rx="1"/><rect x="1.5" y="12" width="13" height="2" rx="1"/></>,
  };
  const p = paths[name];
  if (!p) return null;
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      {p}
    </svg>
  );
};

window.Icon = Icon;
