import { describe, it, expect, vi } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { StatusPill, Progress, Card, Empty, Kv, VideoTile } from './ui';
import { Icons } from './icons';

describe('StatusPill', () => {
  it.each(['claiming', 'slicing', 'uploading'] as const)('shows %s in the info tone, not the warn-identical accent tone', (status) => {
    const { container } = render(<StatusPill status={status} />);
    const pill = container.querySelector('.pill')!;
    expect(pill.classList.contains('info')).toBe(true);
    expect(pill.classList.contains('accent')).toBe(false);
  });
  it('renders printing status', () => {
    render(<StatusPill status="printing" />);
    expect(screen.getByText('Printing')).toBeTruthy();
  });
  it('renders custom label', () => {
    render(<StatusPill status="queued" label="Custom" />);
    expect(screen.getByText('Custom')).toBeTruthy();
  });
});

describe('Progress', () => {
  const bar = (c: HTMLElement) => c.querySelector('.bar') as HTMLElement;
  const width = (c: HTMLElement) => bar(c).style.getPropertyValue('--p');

  it('fills the bar to the given percentage', () => {
    const { container } = render(<Progress value={50} />);
    expect(width(container)).toBe('50%');
  });

  it('is empty by default and clamps out-of-range values to 0-100%', () => {
    expect(width(render(<Progress />).container)).toBe('0%');
    expect(width(render(<Progress value={150} />).container)).toBe('100%');
    expect(width(render(<Progress value={-20} />).container)).toBe('0%');
  });

  it('adds the warn / err / large modifiers, and none for other tones', () => {
    const cls = (ui: React.ReactElement) => (render(ui).container.querySelector('.progress') as HTMLElement).className.split(/\s+/).filter(Boolean);
    expect(cls(<Progress value={1} />)).toEqual(['progress']);
    expect(cls(<Progress value={1} tone="warn" />)).toEqual(['progress', 'warn']);
    expect(cls(<Progress value={1} tone="err" />)).toEqual(['progress', 'err']);
    expect(cls(<Progress value={1} large />)).toEqual(['progress', 'lg']);
    expect(cls(<Progress value={1} tone="ok" />)).toEqual(['progress']);       // "ok" is not a styled tone (callers pass it anyway)
  });
});

describe('Kv', () => {
  it('renders key and value', () => {
    render(<Kv k="Due" v={<span>2026-05-28</span>} />);
    expect(screen.getByText('DUE')).toBeTruthy();
    expect(screen.getByText('2026-05-28')).toBeTruthy();
  });
});

describe('Card', () => {
  it('renders children', () => {
    render(<Card>hello</Card>);
    expect(screen.getByText('hello')).toBeTruthy();
  });
});

describe('Empty', () => {
  it('renders title and sub', () => {
    render(<Empty title="No files" sub="Try again" icon={Icons.files} />);
    expect(screen.getByText('No files')).toBeTruthy();
    expect(screen.getByText('Try again')).toBeTruthy();
  });
});

describe('VideoTile', () => {
  it('renders img with snapshot URL when printerId is set and live is true', () => {
    render(<VideoTile live={true} printerId="42" />);
    const img = document.querySelector('img');
    expect(img).toBeTruthy();
    expect(img!.getAttribute('src')).toContain('/api/v1/printers/42/snapshot');
  });

  it('does not render img when live is false even with printerId', () => {
    render(<VideoTile live={false} printerId="42" />);
    expect(document.querySelector('img')).toBeNull();
  });

  it('does not render img when printerId is absent', () => {
    render(<VideoTile live={true} />);
    expect(document.querySelector('img')).toBeNull();
  });

  it('retries a failed first snapshot and retains the last frame until its replacement loads', () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<VideoTile printerId="42" intervalMs={100} />);
      const frame = (tick: number) => container.querySelector<HTMLImageElement>(`img[src*="t=${tick}"]`)!;
      fireEvent.error(frame(0));
      expect(frame(0)).toBeNull();
      act(() => vi.advanceTimersByTime(100));
      const recovered = frame(1);
      expect(recovered.style.visibility).toBe('hidden');
      act(() => vi.advanceTimersByTime(500));
      expect(frame(2)).toBeNull();
      fireEvent.load(recovered);
      expect(frame(1)).toBe(recovered);
      expect(recovered.style.visibility).toBe('');
      act(() => vi.advanceTimersByTime(100));
      fireEvent.error(frame(2));
      expect(frame(1)).toBe(recovered);
      act(() => vi.advanceTimersByTime(100));
      fireEvent.load(frame(3));
      expect(frame(1)).toBeNull();
      expect(frame(3).style.visibility).toBe('');
    } finally {
      vi.useRealTimers();
    }
  });

  it('falls back to snapshots if the stream fails and tries live again when reopened', () => {
    const { container, rerender } = render(<VideoTile printerId="42" stream />);
    const image = () => container.querySelector('img')!;
    expect(image().getAttribute('src')).toContain('/42/camera');
    fireEvent.error(image());
    expect(image().getAttribute('src')).toContain('/42/snapshot');
    expect(image()).not.toHaveAttribute('data-live-camera');
    rerender(<VideoTile printerId="42" stream live={false} />);
    rerender(<VideoTile printerId="42" stream />);
    expect(image().getAttribute('src')).toContain('/42/camera');
  });

  it('leaves idle cameras alone when idle polling is disabled', () => {
    const { container } = render(<VideoTile printerId="42" status="idle" stream noSnapshotsWhileIdle />);
    expect(container.querySelector('img')).toBeNull();
  });
});
