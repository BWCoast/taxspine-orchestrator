// ALTANA — demo data
window.DEMO = (() => {
  const ASSETS = [
    { sym: 'BTC', name: 'Bitcoin', chain: 'BTC', price: 642300, prevPrice: 615200, color: '#f7931a' },
    { sym: 'ETH', name: 'Ethereum', chain: 'ETH', price: 32480, prevPrice: 33920, color: '#627eea' },
    { sym: 'SOL', name: 'Solana', chain: 'SOL', price: 1850, prevPrice: 1620, color: '#14f195' },
    { sym: 'XRP', name: 'XRP', chain: 'XRPL', price: 5.42, prevPrice: 5.18, color: '#23292f' },
    { sym: 'HBAR', name: 'Hedera', chain: 'HBAR', price: 0.84, prevPrice: 0.91, color: '#000' },
    { sym: 'DAG', name: 'Constellation', chain: 'DAG', price: 0.32, prevPrice: 0.28, color: '#9333ea' },
    { sym: 'CSPR', name: 'Casper', chain: 'CSPR', price: 0.12, prevPrice: 0.14, color: '#ef4444' },
    { sym: 'FLR', name: 'Flare', chain: 'FLR', price: 0.21, prevPrice: 0.19, color: '#e62058' },
    { sym: 'OSMO', name: 'Osmosis', chain: 'OSMO', price: 4.18, prevPrice: 4.42, color: '#5e3ad3' },
    { sym: 'USDC', name: 'USD Coin', chain: 'ETH', price: 10.62, prevPrice: 10.61, color: '#2775ca' },
  ];

  const HOLDINGS = [
    { sym: 'BTC', lots: 7, qty: 1.4823, avgCost: 412800, missingBasis: false },
    { sym: 'ETH', lots: 14, qty: 18.4012, avgCost: 28900, missingBasis: false },
    { sym: 'SOL', lots: 9, qty: 412.55, avgCost: 1240, missingBasis: false },
    { sym: 'XRP', lots: 22, qty: 12480, avgCost: 4.18, missingBasis: false },
    { sym: 'HBAR', lots: 4, qty: 84200, avgCost: 0.72, missingBasis: false },
    { sym: 'DAG', lots: 3, qty: 18400, avgCost: null, missingBasis: true },
    { sym: 'CSPR', lots: 2, qty: 92000, avgCost: 0.18, missingBasis: false },
    { sym: 'FLR', lots: 5, qty: 24800, avgCost: 0.24, missingBasis: false },
    { sym: 'OSMO', lots: 6, qty: 1820, avgCost: 5.12, missingBasis: false },
    { sym: 'USDC', lots: 12, qty: 8420, avgCost: 10.6, missingBasis: false },
  ];

  // Build derived fields
  HOLDINGS.forEach(h => {
    const a = ASSETS.find(x => x.sym === h.sym);
    h.price = a.price;
    h.value = h.qty * a.price;
    h.cost = h.avgCost ? h.qty * h.avgCost : 0;
    h.unrealised = h.avgCost ? h.value - h.cost : 0;
    h.color = a.color;
    h.name = a.name;
  });

  const totalValue = HOLDINGS.reduce((s, h) => s + h.value, 0);
  const totalUnrealised = HOLDINGS.reduce((s, h) => s + h.unrealised, 0);

  const SOURCES = [
    { id: 'kraken', kind: 'API', name: 'Kraken', status: 'synced', last: '2m ago', count: 1842 },
    { id: 'coinbase', kind: 'API', name: 'Coinbase', status: 'syncing', last: 'syncing…', count: 642 },
    { id: 'firi', kind: 'API', name: 'Firi', status: 'synced', last: '14m ago', count: 218 },
    { id: 'nbx', kind: 'API', name: 'NBX', status: 'error', last: '3h ago', count: 0 },
    { id: 'uphold', kind: 'API', name: 'Uphold', status: 'synced', last: '1h ago', count: 84 },
    { id: 'bybit', kind: 'API', name: 'Bybit', status: 'synced', last: '6m ago', count: 412 },
    { id: 'xrpl-1', kind: 'Wallet', name: 'rN7n7…3KQF', status: 'synced', last: '4m ago', count: 218 },
    { id: 'xrpl-2', kind: 'Wallet', name: 'rPVMh…WqBp', status: 'synced', last: '12m ago', count: 92 },
    { id: 'bot', kind: 'Bot', name: 'BOT_DB (DuckDB)', status: 'synced', last: '8m ago', count: 4218 },
  ];

  const UPLOADS = [
    { name: 'kraken-2024-export.csv', source: 'Kraken', rows: 1842, time: '2024-04-12 09:14', dedup: '1842 / 0' },
    { name: 'coinbase-q1-2024.xlsx', source: 'Coinbase', rows: 412, time: '2024-04-08 18:22', dedup: '380 / 32' },
    { name: 'firi-2024-h1.csv', source: 'Firi', rows: 218, time: '2024-03-28 11:04', dedup: '218 / 0' },
    { name: 'manual-events-2024.csv', source: 'Generic Events', rows: 24, time: '2024-03-21 14:42', dedup: '24 / 0' },
  ];

  const JOBS = [
    { id: 'job-2024-no-04', year: 2024, country: 'NO', status: 'completed', date: '2024-04-12 09:42', dur: '4m 12s', mode: 'DB' },
    { id: 'job-2023-no-12', year: 2023, country: 'NO', status: 'completed', date: '2024-01-08 16:18', dur: '3m 51s', mode: 'Price Table' },
    { id: 'job-2023-uk-11', year: 2023, country: 'UK', status: 'completed', date: '2024-01-08 16:08', dur: '4m 02s', mode: 'DB' },
    { id: 'job-2024-no-03', year: 2024, country: 'NO', status: 'failed', date: '2024-04-11 22:12', dur: '0m 41s', mode: 'DB' },
    { id: 'job-2022-no-09', year: 2022, country: 'NO', status: 'completed', date: '2023-04-12 12:10', dur: '5m 14s', mode: 'Price Table' },
  ];

  const ACTIVITY = [
    { time: '09:42', who: 'job', text: 'Tax job NO-2024 completed · 18,420 events · 412 disposals' },
    { time: '09:38', who: 'sync', text: 'Kraken sync · 8 new fills' },
    { time: '09:30', who: 'price', text: 'Price collect · 142 symbols · 0 failed' },
    { time: '09:14', who: 'upload', text: 'Uploaded kraken-2024-export.csv (1842 rows)' },
    { time: '08:52', who: 'bot', text: 'BOT sync · 142 fills · 18 LP events' },
    { time: '08:21', who: 'review', text: '2 missing-basis lots resolved on DAG' },
  ];

  const REVIEW = {
    unlinked: [
      { time: '2024-03-12 11:42', asset: 'BTC', amount: 0.0824, src: 'Kraken (withdrawal)', dst: 'unknown' },
      { time: '2024-02-28 09:08', asset: 'ETH', amount: 2.4, src: 'Coinbase (withdrawal)', dst: 'unknown' },
      { time: '2024-02-14 14:22', asset: 'SOL', amount: 124.0, src: 'Bybit (withdrawal)', dst: 'unknown' },
    ],
    missing: [
      { asset: 'DAG', lots: 3, qty: 18400, oldest: '2022-08-14' },
      { asset: 'CSPR', lots: 1, qty: 4200, oldest: '2023-01-22' },
    ],
    warnings: [
      { sev: 'high', cat: 'LP event', msg: 'Osmosis LP add on 2024-02-04 — FMV confirmation required', detail: 'Pool: ATOM/OSMO · Estimated FMV: 4,218 NOK' },
      { sev: 'high', cat: 'Bot', msg: 'BOT fill clusters need FMV confirmation (12 events)', detail: 'XRPL DEX trades within 60s windows · grouped as single dispositions' },
      { sev: 'info', cat: 'Airdrop', msg: 'FLR airdrop on 2024-01-09 — confirm valuation', detail: 'Received 2,400 FLR · spot 0.18 NOK' },
    ],
  };

  const CHAINS = [
    { name: 'XRPL', sym: 'XRP', status: 'supported', events: 'trade · transfer · IOU' },
    { name: 'Bitcoin', sym: 'BTC', status: 'supported', events: 'transfer' },
    { name: 'Ethereum', sym: 'ETH', status: 'supported', events: 'trade · transfer · staking' },
    { name: 'Hedera', sym: 'HBAR', status: 'supported', events: 'transfer · staking' },
    { name: 'Constellation', sym: 'DAG', status: 'supported', events: 'transfer' },
    { name: 'Casper', sym: 'CSPR', status: 'supported', events: 'transfer · staking' },
    { name: 'Flare', sym: 'FLR', status: 'supported', events: 'transfer · airdrop' },
    { name: 'Osmosis', sym: 'OSMO', status: 'supported', events: 'trade · LP · staking' },
    { name: 'Solana', sym: 'SOL', status: 'beta', events: 'trade · transfer' },
    { name: 'Polkadot', sym: 'DOT', status: 'soon', events: '—' },
    { name: 'Cosmos', sym: 'ATOM', status: 'soon', events: '—' },
    { name: 'Avalanche', sym: 'AVAX', status: 'soon', events: '—' },
  ];

  const DIAGNOSTICS = [
    { name: 'Lot store', status: 'green', detail: '14,202 lots · 412 KB', last: '2m ago' },
    { name: 'Price cache', status: 'green', detail: '142 symbols · 28d', last: '12m ago' },
    { name: 'prices.db', status: 'green', detail: '184,200 rows · 4.2 MB', last: '12m ago' },
    { name: 'Active jobs', status: 'green', detail: '0 running · 1 queued', last: 'now' },
    { name: 'Dedup store', status: 'amber', detail: '32 collisions on Coinbase Q1', last: '14m ago' },
    { name: 'Workspace', status: 'green', detail: 'ola.nordmann · 7 wallets', last: '—' },
    { name: 'Disk', status: 'green', detail: '4.8 / 64 GB used', last: 'now' },
    { name: 'Bot last sync', status: 'green', detail: '4218 events · 0 errors', last: '8m ago' },
  ];

  const ALERTS = [
    { sev: 'critical', cat: 'Review', msg: '2 missing-basis assets before filing', when: '2m ago' },
    { sev: 'high', cat: 'Review', msg: '3 unlinked transfers awaiting resolution', when: '14m ago' },
    { sev: 'high', cat: 'Health', msg: 'Coinbase API rate limited — retrying in 4m', when: '21m ago' },
    { sev: 'info', cat: 'Lot quality', msg: 'BOT fills grouped — confirm FMV (12 events)', when: '38m ago' },
    { sev: 'info', cat: 'Health', msg: 'NBX API key expires in 14 days', when: '2h ago' },
  ];

  const AUDIT = [
    { time: '2024-04-12 09:42:18', type: 'job.complete', actor: 'orchestrator', summary: 'NO-2024 completed (412 disposals)', job: 'job-2024-no-04' },
    { time: '2024-04-12 09:38:02', type: 'sync.complete', actor: 'scheduler', summary: 'Kraken sync · 8 new fills', job: '—' },
    { time: '2024-04-12 09:30:14', type: 'prices.collect', actor: 'manual', summary: 'Collected 142 symbols', job: '—' },
    { time: '2024-04-12 09:14:42', type: 'upload', actor: 'manual', summary: 'kraken-2024-export.csv (1842 rows)', job: '—' },
    { time: '2024-04-12 08:52:18', type: 'bot.sync', actor: 'scheduler', summary: 'BOT_DB · 142 fills · 18 LP', job: '—' },
    { time: '2024-04-12 08:21:00', type: 'review.resolve', actor: 'manual', summary: 'Resolved 2 missing-basis lots on DAG', job: '—' },
    { time: '2024-04-11 22:12:08', type: 'job.failed', actor: 'orchestrator', summary: 'NO-2024 failed: missing prices for FLR', job: 'job-2024-no-03' },
    { time: '2024-04-11 18:04:21', type: 'wallet.add', actor: 'manual', summary: 'Added XRPL account rPVMh…WqBp', job: '—' },
  ];

  // Generate sparkline data (deterministic)
  function sparkData(seed, n = 24, vol = 0.05) {
    const out = [];
    let v = 100;
    let r = seed;
    for (let i = 0; i < n; i++) {
      r = (r * 9301 + 49297) % 233280;
      const noise = (r / 233280 - 0.5) * 2;
      v += v * vol * noise;
      out.push(v);
    }
    return out;
  }

  // Generate longer area-chart data for portfolio value over time
  function timeSeries(seed, n = 90, drift = 0.002, vol = 0.025) {
    const out = [];
    let v = 1800000;
    let r = seed;
    for (let i = 0; i < n; i++) {
      r = (r * 9301 + 49297) % 233280;
      const noise = (r / 233280 - 0.5) * 2;
      v = v * (1 + drift + vol * noise);
      out.push(Math.round(v));
    }
    return out;
  }

  return {
    ASSETS, HOLDINGS, SOURCES, UPLOADS, JOBS, ACTIVITY, REVIEW,
    CHAINS, DIAGNOSTICS, ALERTS, AUDIT,
    totalValue, totalUnrealised,
    sparkData, timeSeries,
  };
})();
