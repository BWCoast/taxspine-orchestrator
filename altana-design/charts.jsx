// ALTANA — chart primitives (hand-rolled SVG)

const Sparkline = ({ data, width = 80, height = 24, color = 'var(--accent)', fill = true, strokeWidth = 1.4 }) => {
  if (!data || !data.length) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const step = width / (data.length - 1);
  const points = data.map((v, i) => [i * step, height - ((v - min) / range) * (height - 4) - 2]);
  const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  const dArea = `${d} L ${width} ${height} L 0 ${height} Z`;
  const last = points[points.length - 1];
  const trendUp = data[data.length - 1] >= data[0];
  const c = color === 'auto' ? (trendUp ? 'var(--green)' : 'var(--red)') : color;
  const id = `sg-${Math.random().toString(36).slice(2, 8)}`;
  return (
    <svg width={width} height={height} style={{display: 'block'}}>
      {fill && (
        <>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={c} stopOpacity="0.25"/>
              <stop offset="100%" stopColor={c} stopOpacity="0"/>
            </linearGradient>
          </defs>
          <path d={dArea} fill={`url(#${id})`}/>
        </>
      )}
      <path d={d} stroke={c} strokeWidth={strokeWidth} fill="none" strokeLinejoin="round" strokeLinecap="round"/>
      <circle cx={last[0]} cy={last[1]} r="1.6" fill={c}/>
    </svg>
  );
};

const AreaChart = ({ data, width = 800, height = 240, color = 'var(--accent)', showAxis = true, currency = 'NOK', onHover }) => {
  if (!data || !data.length) return null;
  const padL = showAxis ? 56 : 8;
  const padR = 8;
  const padT = 16;
  const padB = showAxis ? 28 : 8;
  const w = width - padL - padR;
  const h = height - padT - padB;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const step = w / (data.length - 1);
  const points = data.map((v, i) => [padL + i * step, padT + h - ((v - min) / range) * h]);
  const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
  const dArea = `${d} L ${padL + w} ${padT + h} L ${padL} ${padT + h} Z`;
  const id = `ar-${Math.random().toString(36).slice(2, 8)}`;
  const yTicks = 4;
  const ticks = Array.from({length: yTicks + 1}, (_, i) => min + (range * i / yTicks));
  const fmt = v => v >= 1e6 ? (v / 1e6).toFixed(2) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(0) + 'k' : v.toFixed(0);
  const xLabels = ['90d', '60d', '30d', 'Now'];

  const [hover, setHover] = React.useState(null);
  const svgRef = React.useRef(null);

  const handleMove = (e) => {
    const rect = svgRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const ratio = (x - padL) / w;
    const idx = Math.max(0, Math.min(data.length - 1, Math.round(ratio * (data.length - 1))));
    setHover({ idx, x: points[idx][0], y: points[idx][1], v: data[idx] });
  };

  return (
    <svg ref={svgRef} width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"
         onMouseMove={handleMove} onMouseLeave={() => setHover(null)} style={{display:'block'}}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.32"/>
          <stop offset="100%" stopColor={color} stopOpacity="0"/>
        </linearGradient>
      </defs>
      {showAxis && ticks.map((t, i) => {
        const y = padT + h - (i / yTicks) * h;
        return (
          <g key={i}>
            <line x1={padL} y1={y} x2={padL + w} y2={y} stroke="var(--border)" strokeDasharray="2 4"/>
            <text x={padL - 8} y={y + 3} textAnchor="end" fontSize="10" fill="var(--text-faint)" fontFamily="JetBrains Mono">{fmt(t)}</text>
          </g>
        );
      })}
      {showAxis && xLabels.map((lbl, i) => (
        <text key={lbl} x={padL + (i / (xLabels.length - 1)) * w} y={height - 8} textAnchor={i === 0 ? 'start' : i === xLabels.length - 1 ? 'end' : 'middle'} fontSize="10" fill="var(--text-faint)" fontFamily="JetBrains Mono">{lbl}</text>
      ))}
      <path d={dArea} fill={`url(#${id})`}/>
      <path d={d} stroke={color} strokeWidth="1.6" fill="none" strokeLinejoin="round"/>
      {hover && (
        <g>
          <line x1={hover.x} y1={padT} x2={hover.x} y2={padT + h} stroke="var(--text-faint)" strokeDasharray="2 3" opacity="0.5"/>
          <circle cx={hover.x} cy={hover.y} r="3.5" fill={color} stroke="var(--bg)" strokeWidth="2"/>
          <g transform={`translate(${Math.min(hover.x + 8, width - 110)}, ${Math.max(hover.y - 30, padT)})`}>
            <rect x="0" y="0" width="100" height="22" rx="5" fill="var(--surface-solid)" stroke="var(--border)"/>
            <text x="8" y="14" fontSize="11" fill="var(--text)" fontFamily="JetBrains Mono">{currency} {fmt(hover.v)}</text>
          </g>
        </g>
      )}
    </svg>
  );
};

const Donut = ({ data, size = 140, thickness = 18 }) => {
  // data: [{label, value, color}]
  const total = data.reduce((s, d) => s + d.value, 0);
  const r = size / 2 - thickness / 2;
  const cx = size / 2, cy = size / 2;
  const C = 2 * Math.PI * r;
  let offset = 0;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle cx={cx} cy={cy} r={r} stroke="var(--border)" strokeWidth={thickness} fill="none" opacity="0.4"/>
      {data.map((d, i) => {
        const frac = d.value / total;
        const len = frac * C;
        const dash = `${len} ${C - len}`;
        const el = (
          <circle key={i} cx={cx} cy={cy} r={r}
                  stroke={d.color} strokeWidth={thickness} fill="none"
                  strokeDasharray={dash}
                  strokeDashoffset={-offset}
                  transform={`rotate(-90 ${cx} ${cy})`}
                  strokeLinecap="butt"/>
        );
        offset += len;
        return el;
      })}
    </svg>
  );
};

const Waterfall = ({ data, width = 700, height = 220 }) => {
  // data: [{label, value}] — pos green, neg red
  const padL = 36, padB = 28, padT = 12, padR = 12;
  const w = width - padL - padR;
  const h = height - padT - padB;
  const max = Math.max(...data.map(d => Math.abs(d.value)));
  const bw = w / data.length * 0.62;
  const gap = w / data.length * 0.38;
  const zero = padT + h * 0.6;
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{display:'block'}}>
      <line x1={padL} y1={zero} x2={padL + w} y2={zero} stroke="var(--border)" strokeDasharray="2 3"/>
      {data.map((d, i) => {
        const x = padL + i * (bw + gap) + gap / 2;
        const isPos = d.value >= 0;
        const barH = (Math.abs(d.value) / max) * (isPos ? h * 0.55 : h * 0.35);
        const y = isPos ? zero - barH : zero;
        return (
          <g key={d.label}>
            <rect x={x} y={y} width={bw} height={barH} rx="2"
                  fill={isPos ? 'var(--green)' : 'var(--red)'} opacity="0.85"/>
            <text x={x + bw/2} y={height - 12} textAnchor="middle" fontSize="10" fill="var(--text-muted)" fontFamily="JetBrains Mono">{d.label}</text>
            <text x={x + bw/2} y={isPos ? y - 4 : y + barH + 11} textAnchor="middle" fontSize="9.5" fill={isPos ? 'var(--green)' : 'var(--red)'} fontFamily="JetBrains Mono">
              {isPos ? '+' : ''}{d.value > 1000 ? (d.value / 1000).toFixed(0) + 'k' : d.value.toFixed(0)}
            </text>
          </g>
        );
      })}
    </svg>
  );
};

const Candlestick = ({ data, width = 700, height = 240 }) => {
  // data: [{o, h, l, c}]
  const padL = 8, padR = 48, padT = 12, padB = 24;
  const w = width - padL - padR;
  const h = height - padT - padB;
  const allVals = data.flatMap(d => [d.h, d.l]);
  const min = Math.min(...allVals);
  const max = Math.max(...allVals);
  const range = max - min || 1;
  const cw = w / data.length;
  const bw = cw * 0.6;
  const last = data[data.length - 1];
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{display:'block'}}>
      {[0, 0.25, 0.5, 0.75, 1].map((t, i) => {
        const y = padT + h * (1 - t);
        const v = min + range * t;
        return (
          <g key={i}>
            <line x1={padL} y1={y} x2={padL + w} y2={y} stroke="var(--border)" strokeDasharray="2 4" opacity="0.6"/>
            <text x={width - padR + 6} y={y + 3} fontSize="10" fill="var(--text-faint)" fontFamily="JetBrains Mono">
              {v >= 1000 ? (v/1000).toFixed(1) + 'k' : v.toFixed(0)}
            </text>
          </g>
        );
      })}
      {data.map((d, i) => {
        const x = padL + i * cw + cw/2;
        const yH = padT + h - ((d.h - min) / range) * h;
        const yL = padT + h - ((d.l - min) / range) * h;
        const yO = padT + h - ((d.o - min) / range) * h;
        const yC = padT + h - ((d.c - min) / range) * h;
        const up = d.c >= d.o;
        const color = up ? 'var(--green)' : 'var(--red)';
        return (
          <g key={i}>
            <line x1={x} y1={yH} x2={x} y2={yL} stroke={color} strokeWidth="1"/>
            <rect x={x - bw/2} y={Math.min(yO, yC)} width={bw} height={Math.max(1, Math.abs(yC - yO))} fill={color} opacity="0.85"/>
          </g>
        );
      })}
      <line x1={padL} y1={padT + h - ((last.c - min)/range)*h} x2={width - padR} y2={padT + h - ((last.c - min)/range)*h}
            stroke="var(--accent)" strokeDasharray="3 3" opacity="0.6"/>
    </svg>
  );
};

window.Sparkline = Sparkline;
window.AreaChart = AreaChart;
window.Donut = Donut;
window.Waterfall = Waterfall;
window.Candlestick = Candlestick;
