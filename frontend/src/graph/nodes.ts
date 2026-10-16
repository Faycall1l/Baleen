import { theme } from '../theme';

export function nodeColor(label: string): string {
  return theme.nodeColors[label as keyof typeof theme.nodeColors] ?? theme.accent;
}

export function shade(hex: string, factor: number): string {
  const n = parseInt(hex.slice(1), 16);
  const mix = (c: number) => Math.round(c * factor);
  const v = (mix((n >> 16) & 255) << 16) | (mix((n >> 8) & 255) << 8) | mix(n & 255);
  return `#${v.toString(16).padStart(6, '0')}`;
}

export function truncate(s: string | null | undefined, n: number): string {
  if (typeof s !== 'string' || s === '') return '—';
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

// Second label line for the on-canvas table cards: the fields that
// make a node self-describing without opening the inspector. Anything
// without a meaningful second line returns null (single-line label).
export function nodeSubline(label: string, props: Record<string, unknown>): string | null {
  if (label === 'Event') {
    if (props.type === 'options_flow') {
      const volume = typeof props.volume === 'number' ? props.volume : null;
      if (volume === null) return null;
      const oi = typeof props.open_interest === 'number' && props.open_interest > 0
        ? props.open_interest
        : 1;
      const bias = typeof props.sentiment === 'string' ? props.sentiment.toUpperCase() : '';
      return `VOL ${volume.toLocaleString('en-US')} · ${(volume / oi).toFixed(1)}x OI · ${bias}`.trim().replace(/ ·$/, '');
    }
    if (props.type === 'congress_trade' || props.type === 'insider_trade') {
      const side = typeof props.transaction_type === 'string' ? props.transaction_type.toUpperCase() : '';
      const amount = typeof props.amount_range === 'string' ? props.amount_range : '';
      const date = typeof props.transaction_date === 'string' ? props.transaction_date.slice(0, 10) : '';
      const parts = [side, amount, date].filter(Boolean);
      return parts.length > 0 ? parts.join(' · ') : null;
    }
    return null;
  }
  if (label === 'Politician') {
    const chamber = typeof props.chamber === 'string' ? props.chamber : '';
    const party = typeof props.party === 'string' ? props.party : '';
    const parts = [chamber, party].filter(Boolean);
    return parts.length > 0 ? parts.join(' · ') : null;
  }
  if (label === 'Insider') {
    return typeof props.role === 'string' && props.role ? truncate(props.role, 24) : null;
  }
  return null;
}

// Event nodes arrive titled with the bare ticker ("SE"), so dozens read
// identically. Compose what happened from the props instead.
export function eventTitle(props: Record<string, unknown>): string {
  const ticker = typeof props.ticker === 'string' ? props.ticker : '';
  const type = props.type;
  if (type === 'options_flow') {
    const contract = props.contract_type === 'put' ? 'put' : 'call';
    const strike = typeof props.strike === 'number' ? ` ${props.strike}` : '';
    return ticker ? `${ticker} ${contract}${strike}` : 'Options flow';
  }
  if (type === 'congress_trade' || type === 'insider_trade') {
    const side = props.transaction_type === 'sell' ? 'sold' : 'bought';
    return ticker ? `${ticker} ${side}` : 'Trade';
  }
  return 'Unparsed';
}

export function nodeSize(label: string, degree: number): number {
  if (label === 'Event') return 2.4 + Math.min(Math.sqrt(degree) * 1.0, 3);
  return 3.2 + Math.min(Math.sqrt(degree) * 1.6, 7);
}
