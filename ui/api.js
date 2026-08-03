// ALTANA — live API client for taxspine-orchestrator
//
// Config (set before this script loads):
//   window.ORCHESTRATOR_URL  — base URL (default: current origin)
//   window.ORCHESTRATOR_KEY  — API key (falls back to localStorage 'ORCHESTRATOR_KEY')
//
// All endpoints return Promises.  Use window.useApi() in React components.

window.API = (() => {
  const base = () => (window.ORCHESTRATOR_URL || '').replace(/\/$/, '');

  const apiKey = () =>
    window.ORCHESTRATOR_KEY ||
    (typeof localStorage !== 'undefined' ? localStorage.getItem('ORCHESTRATOR_KEY') : '') ||
    '';

  // ── Workspace context ──────────────────────────────────────────────────────
  // null   = default workspace (omit workspace_id from all calls)
  // string = named workspace id (appended as ?workspace_id=X)
  const _WS_KEY        = 'ALTANA_WORKSPACE';
  const _WS_KEY_LEGACY = 'TAXNOR_WORKSPACE';   // renamed — migrate on first read
  const getWorkspaceId = () => {
    if (typeof localStorage === 'undefined') return null;
    let id = localStorage.getItem(_WS_KEY);
    if (!id) {
      const legacy = localStorage.getItem(_WS_KEY_LEGACY);
      if (legacy) {
        localStorage.setItem(_WS_KEY, legacy);
        localStorage.removeItem(_WS_KEY_LEGACY);
        return legacy;
      }
    }
    return id || null;
  };
  const setWorkspaceId = (id) => {
    if (typeof localStorage === 'undefined') return;
    if (id == null) localStorage.removeItem(_WS_KEY);  // null or undefined both clear
    else localStorage.setItem(_WS_KEY, id);
  };
  // Append ?workspace_id=X to calls when a named workspace is active.
  // Pass extra='key=val' for additional params to merge in.
  const _wsParam = (extra = '') => {
    const id = getWorkspaceId();
    const ws = id ? `workspace_id=${encodeURIComponent(id)}` : '';
    const both = [ws, extra].filter(Boolean).join('&');
    return both ? `?${both}` : '';
  };

  async function apiFetch(path, options = {}) {
    const k = apiKey();
    const headers = { ...(options.headers || {}) };
    if (k) headers['X-Api-Key'] = k;
    if (options.body && !headers['Content-Type'])
      headers['Content-Type'] = 'application/json';
    const res = await fetch(base() + path, { ...options, headers });
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(`[${res.status}] ${path}: ${text.slice(0, 200)}`);
    }
    return res.json();
  }

  const get  = (path)       => apiFetch(path, { method: 'GET' });
  const post = (path, body) => apiFetch(path, { method: 'POST', body: JSON.stringify(body) });

  // ── Boundary rule ─────────────────────────────────────────────────────────
  // GLOBAL  — market data, system health, server config. No workspace param.
  //           Same response regardless of who is logged in or which workspace
  //           is active. NOK/GBP, XRP/NOK, XRP/USD, USD/NOK, prices.db,
  //           combined CSVs, diagnostics, health, dedup sources — all global.
  //
  // WORKSPACE-SCOPED — user data. Always includes _wsParam().
  //           Jobs, lots, portfolio, review, workspace config, wallets,
  //           uploaded CSVs, sidecar. Private to the active workspace.
  //
  // ALERTS are fetched globally (health/price alerts are server-wide) and
  //   filtered client-side to the active workspace using sharedJobs cross-ref.
  //   Never send a workspace param here — it would suppress global health alerts
  //   (e.g. "prices.db missing", "NOK rates stale") for users in named workspaces.
  // ─────────────────────────────────────────────────────────────────────────────

  return {
    // ═══════════════════════════════════════════════════════════════════════════
    // GLOBAL — no workspace param
    // ═══════════════════════════════════════════════════════════════════════════

    // ── System health & diagnostics ──────────────────────────────────────────
    getHealth:      () => get('/health'),
    getDiagnostics: () => get('/diagnostics'),

    // ── Alerts — ALWAYS global fetch, workspace isolation done client-side ───
    // Health alerts (prices.db missing, NOK rates stale, etc.) are server-wide
    // and must surface regardless of active workspace. Workspace filtering of
    // review/job alerts is handled in the Alerts component via sharedJobs.
    getAlerts: (limit = 20) => get(`/alerts?limit=${limit}`),

    // ── Market prices & exchange rates ───────────────────────────────────────
    // NOK/GBP, XRP/NOK, XRP/USD, XRP/GBP, USD/NOK — shared across all workspaces.
    getSpotPrices: () => get('/prices/spot'),
    getPriceFiles: () => get('/prices'),
    // POST /prices/fetch: generate combined_nok_{year}.csv via all price tiers.
    // POST /prices/collect: ingest daily prices.db (no year param needed).
    // Neither is scoped to a workspace — results land in global PRICES_DIR.
    collectPrices: (year) => post('/prices/fetch', { year }),

    // ── Dedup — global source registry ──────────────────────────────────────
    getDedupSources:  ()       => get('/dedup/sources'),
    getDedupSummary:  (source) => get(`/dedup/${source}/summary`),

    // ── Bot sync status ──────────────────────────────────────────────────────
    getBotStatus: () => get('/bot/sync/status'),

    // ── Workspace directory ──────────────────────────────────────────────────
    getWorkspaces:   ()     => get('/workspaces'),
    createWorkspace: (body) => post('/workspaces', body),
    deleteWorkspace: (id, deleteData = false) =>
      apiFetch(`/workspaces/${encodeURIComponent(id)}?delete_data=${deleteData}`, { method: 'DELETE' }),

    // ═══════════════════════════════════════════════════════════════════════════
    // WORKSPACE-SCOPED — always includes _wsParam()
    // ═══════════════════════════════════════════════════════════════════════════

    // ── Jobs ──────────────────────────────────────────────────────────────────
    getJobs: (params = {}) => {
      // Always scope to the active workspace so audit log is workspace-isolated.
      const wsId = getWorkspaceId();
      const workspaceParam = wsId ? wsId : '__default__';
      const qs = new URLSearchParams({ limit: 50, workspace: workspaceParam, ...params }).toString();
      return get(`/jobs?${qs}`);
    },
    getJob:       (id)   => get(`/jobs/${id}`),
    startJob:     (id)   => post(`/jobs/${id}/start`, {}),
    runWorkspace: (body) => post(`/workspace/run${_wsParam()}`, body),

    // ── Lots & portfolio ──────────────────────────────────────────────────────
    getLotYears:  ()           => get(`/lots/years${_wsParam()}`),
    getLotYear:   (year)       => get(`/lots/${year}${_wsParam()}`),
    getPortfolio: (year, live) => get(
      `/lots/${year}/portfolio${_wsParam(`include_prices=true&price_type=${live ? 'current' : 'year_end'}`)}`
    ),

    // ── Review ────────────────────────────────────────────────────────────────
    getReviewSummary: (year) => get(`/review/summary${_wsParam(`year=${year}`)}`),
    getReviewJobs:    (year) => get(`/review/jobs${_wsParam(`year=${year}`)}`),

    // ── Workspace config ──────────────────────────────────────────────────────
    getWorkspace: () => get(`/workspace${_wsParam()}`),

    // ── Dashboard sidecar (gains/losses summary per workspace) ────────────────
    getSidecar: (year) => get(`/sidecar/${year}${_wsParam()}`),

    // ── XRPL account management ───────────────────────────────────────────────
    addXrplAccount: (account) =>
      post(`/workspace/accounts${_wsParam()}`, { account }),
    removeXrplAccount: (account) =>
      apiFetch(`/workspace/accounts/${encodeURIComponent(account)}${_wsParam()}`, { method: 'DELETE' }),
    // XRPL asset registry: custom IOU token specs ("SYMBOL.rIssuer") — workspace-private.
    // Note: the price data fetched FOR these assets lands in global PRICES_DIR,
    // but the registry of which assets belong to which workspace is scoped.
    removeXrplAsset: (spec) =>
      apiFetch(`/workspace/xrpl-assets/${encodeURIComponent(spec)}${_wsParam()}`, { method: 'DELETE' }),
    setWalletLabel: (account, label) =>
      apiFetch(`/workspace/labels/wallet/${encodeURIComponent(account)}${_wsParam()}`, {
        method: 'PUT', body: JSON.stringify({ label }),
      }),
    removeWalletLabel: (account) =>
      apiFetch(`/workspace/labels/wallet/${encodeURIComponent(account)}${_wsParam()}`, { method: 'DELETE' }),

    // ── CSV uploads ────────────────────────────────────────────────────────────
    // Multipart POST — cannot use apiFetch (browser must set boundary).
    uploadCsv: async (file, sourceType = 'generic_events') => {
      const k = apiKey();
      const wsId = getWorkspaceId();
      const qs = new URLSearchParams({ source_type: sourceType, register: 'true' });
      if (wsId) qs.set('workspace_id', wsId);
      const form = new FormData();
      form.append('file', file);
      const headers = {};
      if (k) headers['X-Api-Key'] = k;
      const res = await fetch(`${base()}/uploads/csv?${qs}`, { method: 'POST', headers, body: form });
      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText);
        throw new Error(`[${res.status}] /uploads/csv: ${text.slice(0, 200)}`);
      }
      return res.json();
    },
    // Remove a CSV from the workspace (unregisters it; does not delete the upload file).
    removeCsv: (path, sourceType = 'generic_events') =>
      apiFetch(`/workspace/csv${_wsParam()}`, {
        method: 'DELETE',
        body: JSON.stringify({ path, source_type: sourceType }),
      }),

    // ── Job file downloads ─────────────────────────────────────────────────────
    listJobFiles: (jobId) => get(`/jobs/${encodeURIComponent(jobId)}/files`),
    downloadJobFile: async (jobId, kind, filename) => {
      const k = apiKey();
      const headers = {};
      if (k) headers['X-Api-Key'] = k;
      const res = await fetch(
        `${base()}/jobs/${encodeURIComponent(jobId)}/files/${kind}`,
        { headers },
      );
      if (!res.ok) throw new Error(`[${res.status}] Could not download ${kind}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      // Sanitise filename: strip any characters that are not alphanumeric, dot, dash, or
      // underscore. This prevents path traversal sequences in server-controlled filenames.
      const rawName = filename || `${jobId.slice(0, 8)}_${kind}`;
      a.href = url; a.download = rawName.replace(/[^a-zA-Z0-9._-]/g, '_');
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    },

    // ═══════════════════════════════════════════════════════════════════════════
    // CLIENT-SIDE ONLY — no API call
    // ═══════════════════════════════════════════════════════════════════════════

    // ── Local per-workspace username ──────────────────────────────────────────
    // Stored in localStorage as ALTANA_USER_<wsId|'default'>. Never sent to server.
    getUserName: () => {
      if (typeof localStorage === 'undefined') return null;
      const id = getWorkspaceId();
      return localStorage.getItem(`ALTANA_USER_${id || 'default'}`) || null;
    },
    setUserName: (name) => {
      if (typeof localStorage === 'undefined') return;
      const id = getWorkspaceId();
      const k = `ALTANA_USER_${id || 'default'}`;
      if (name) localStorage.setItem(k, name);
      else localStorage.removeItem(k);
    },

    // Expose workspace switcher so UI components can read/set the active workspace.
    getWorkspaceId,
    setWorkspaceId,
  };
})();


// ── useApi hook ───────────────────────────────────────────────────────────────
// Call inside any React function component (hook rules apply).
//
//   const { data, loading, error, reload } = window.useApi(
//     () => window.API.getDiagnostics(), []
//   );
//
// - fn   is called on mount and whenever deps change.
// - data is null until the first successful response.
// - error is the string message from any thrown Error.
// - reload() re-runs fn immediately (e.g. on a Refresh button).

window.useApi = function useApi(fn, deps, options) {
  const pollInterval = options && options.pollInterval;
  const [state, setState] = React.useState({ data: null, loading: true, error: null });

  // Keep fn reference fresh without re-triggering the effect.
  const fnRef = React.useRef(fn);
  React.useLayoutEffect(() => { fnRef.current = fn; });

  // Explicit reload — sets loading:true so callers see a visible refresh.
  const reload = React.useCallback(() => {
    setState(s => ({ ...s, loading: true, error: null }));
    fnRef.current()
      .then(data => setState({ data, loading: false, error: null }))
      .catch(err  => setState(s => ({ ...s, loading: false, error: String(err.message || err) })));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  React.useEffect(() => { reload(); }, [reload]);

  // Silent background poll — never sets loading:true so there is no visible flash on each tick.
  // fnRef.current always points to the latest fn (updated via useLayoutEffect), so the poll
  // naturally picks up new deps (e.g. a changed year) on the next tick without restarting.
  React.useEffect(() => {
    if (!pollInterval) return;
    const id = setInterval(() => {
      fnRef.current()
        .then(data => setState(s => ({ ...s, data, error: null })))
        .catch(err  => setState(s => ({ ...s, error: String(err.message || err) })));
    }, pollInterval);
    return () => clearInterval(id);
  }, [pollInterval]);

  return { ...state, reload };
};
