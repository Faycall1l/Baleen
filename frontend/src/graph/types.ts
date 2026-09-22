// View-model types for the canvas graph: plain data in, plain data out.
// Everything here is framework-free so it unit-tests without a browser.

export type GraphMode = 'instances' | 'schema';

export interface Camera {
  x: number;
  y: number;
  ratio: number;
}

export interface ViewNode {
  id: string;
  kind: string;
  /** Display text (names, composed event titles, schema titles). */
  label: string;
  title: string;
  x: number;
  y: number;
  size: number;
  color: string;
  borderColor: string;
  dimmed: boolean;
  selected: boolean;
  props: Record<string, unknown>;
  tweet_url?: string | null;
}

export interface ViewEdge {
  key: string;
  source: string;
  target: string;
  type: string;
  label: string;
  size: number;
  color: string;
  dimmed: boolean;
}

export interface ViewModel {
  nodes: ViewNode[];
  edges: ViewEdge[];
}
