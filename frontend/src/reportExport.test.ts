import { describe, expect, it } from 'vitest';

import { reportFilename, reportToMarkdown } from './reportExport';
import type { Report } from './types';

const REPORT: Report = {
  id: 8,
  kind: 'brief',
  title: 'NVDA unusual calls, Oct 1',
  body: 'Sweep detected.',
  graph_refs: ['NVDA', 'AMD'],
};

describe('reportExport', () => {
  it('renders title, body, entities, and provenance', () => {
    const md = reportToMarkdown(REPORT);
    expect(md).toContain('# NVDA unusual calls, Oct 1');
    expect(md).toContain('Sweep detected.');
    expect(md).toContain('- NVDA');
    expect(md).toContain('— Baleen brief #8');
  });

  it('omits empty sections and slugifies filenames', () => {
    const bare: Report = { id: 3, kind: 'digest', title: 'Daily!', body: 'Nothing.' };
    const md = reportToMarkdown(bare);
    expect(md).not.toContain('## Entities');
    expect(reportFilename(bare)).toBe('baleen-digest-3-daily.md');
    expect(reportFilename(REPORT)).toBe('baleen-brief-8-nvda-unusual-calls-oct-1.md');
  });
});

describe('reportFilename null titles', () => {
  it('falls back to a bare slug instead of crashing', async () => {
    const { reportFilename: fn } = await import('./reportExport');
    expect(fn({ id: 9, kind: 'brief', title: null as unknown as string, body: 'x' })).toBe(
      'baleen-brief-9-report.md',
    );
  });

  it('renders fallbacks for null title, body, and refs', () => {
    const md = reportToMarkdown({
      id: 10,
      kind: 'brief',
      title: null as unknown as string,
      body: null as unknown as string,
      graph_refs: [null, '', 'NVDA'] as unknown as string[],
    });
    expect(md).toContain('# Untitled report');
    expect(md).not.toContain('null');
    expect(md).toContain('- NVDA');
  });
});
