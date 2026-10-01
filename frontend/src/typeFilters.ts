// Alt-click solo semantics for legend toggles (R43): alt-clicking a type
// hides everything else; alt-clicking while soloed restores all. Pure so
// the set algebra unit-tests without a browser.

export function soloHidden(
  allLabels: string[],
  label: string,
  hidden: Set<string>,
): Set<string> {
  const soloed = allLabels.every((l) => l === label || hidden.has(l));
  if (soloed) return new Set();
  return new Set(allLabels.filter((l) => l !== label));
}
