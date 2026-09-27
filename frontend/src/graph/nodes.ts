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

export function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
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
