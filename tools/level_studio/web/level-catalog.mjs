// Catalogue sections follow native resource roles. A custom gameplay clone can
// keep a cinematic display family without becoming a movie resource.
export function catalogSection(level) {
  const key = String(level?.nativeKey || '').replaceAll('\\', '/').toLowerCase();
  if (key.startsWith('movies/') || key.startsWith('scenes/')) return 'cinematics';
  if (key.startsWith('levels/') || level?.custom) return 'levels';
  return ['cinematic', 'cinematics'].includes(level?.family)
    || ['cinematic', 'cinematics'].includes(level?.group) ? 'cinematics' : 'levels';
}

export function catalogEntries(levels, section) {
  return (Array.isArray(levels) ? levels : []).filter(level => level
    && typeof level.id === 'string' && catalogSection(level) === section);
}

export function catalogView(levels, section = 'levels', preferredId = null) {
  const all = Array.isArray(levels) ? levels : [];
  const preferred = all.find(level => level?.id === preferredId);
  const selectedSection = preferred ? catalogSection(preferred)
    : section === 'cinematics' ? 'cinematics' : 'levels';
  const entries = catalogEntries(all, selectedSection);
  return { section: selectedSection, entries,
    selectedId: preferred?.id ?? entries[0]?.id ?? null,
    counts: { levels: catalogEntries(all, 'levels').length,
      cinematics: catalogEntries(all, 'cinematics').length } };
}
