import { describe, expect, it } from 'vitest';
import { generateHeadline, generateWarnings, pooledGroups } from './projects';

describe('what a generate queued', () => {
  it('counts the pool groups and collects their warnings', () => {
    const result = { groups: [{ id: 1, pool: true }, { id: 2 }, { id: 3, pool: true, warning: 'no eligible printer' }] };
    expect(pooledGroups(result)).toBe(2);
    expect(generateWarnings(result)).toEqual(['no eligible printer']);
    expect(pooledGroups({})).toBe(0);
    expect(generateWarnings({})).toEqual([]);
  });

  it('says jobs alone when nothing was pooled', () => {
    expect(generateHeadline(1, 0, 'queued')).toBe('1 job queued');
    expect(generateHeadline(3, 0, 'added to queue')).toBe('3 jobs added to queue');
  });

  it('never reports a pooled project as "0 jobs"', () => {
    expect(generateHeadline(0, 1, 'queued')).toBe('1 print group queued: each free printer packs its own plate');
    expect(generateHeadline(0, 2, 'queued')).toBe('2 print groups queued: each free printer packs its own plate');
  });

  it('names both when a project has jobs and pools', () => {
    expect(generateHeadline(2, 1, 'queued')).toBe('2 jobs queued · 1 print group queued: each free printer packs its own plate');
  });
});
