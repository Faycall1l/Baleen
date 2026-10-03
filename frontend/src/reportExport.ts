import type { Report } from './types';

// Markdown export for briefs and digests (R51): the evidence leaves the
// terminal as a shareable document — title, body, sources, provenance.
// Pure string building so the formatting unit-tests without a browser.

export function reportToMarkdown(report: Report): string {
  const lines = [`# ${report.title}`, ''];
  if (report.period) lines.push(`Period: ${report.period}`, '');
  lines.push(report.body);
  const refs = (report.graph_refs ?? []).filter((r) => r.length > 0);
  if (refs.length > 0) {
    lines.push('', '## Entities', '');
    for (const ref of refs) lines.push(`- ${ref}`);
  }
  lines.push('', `— Baleen brief #${report.id}`);
  return lines.join('\n');
}

export function reportFilename(report: Report): string {
  const slug = report.title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40);
  return `baleen-${report.kind}-${report.id}-${slug || 'report'}.md`;
}

export function downloadMarkdown(filename: string, text: string): void {
  const blob = new Blob([text], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
