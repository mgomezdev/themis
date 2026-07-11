import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Sidebar } from './Sidebar';

// Renders on /fleet so the Job Queue nav item is NOT active, giving unambiguous badge colors.
function renderOnFleet(
  active: number, pending: number, blocked: number,
  operatorName: string | null = null, printerCount = 0,
) {
  return render(
    <MemoryRouter initialEntries={['/fleet']}>
      <Sidebar queueCounts={{ active, pending, blocked }}
               operatorName={operatorName} printerCount={printerCount} />
    </MemoryRouter>
  );
}

// Renders on /queue so the Job Queue nav item IS active (accent-color override in CSS).
function renderOnQueue(
  active: number, pending: number, blocked: number,
  operatorName: string | null = null, printerCount = 0,
) {
  return render(
    <MemoryRouter initialEntries={['/queue']}>
      <Sidebar queueCounts={{ active, pending, blocked }}
               operatorName={operatorName} printerCount={printerCount} />
    </MemoryRouter>
  );
}

// ─── Nav structure ────────────────────────────────────────────────────────────

describe('Sidebar nav items', () => {
  it('renders all nav labels', () => {
    renderOnFleet(0, 0, 0);
    expect(screen.getByText('Job queue')).toBeTruthy();
    expect(screen.getByText('Fleet')).toBeTruthy();
    expect(screen.getByText('Projects')).toBeTruthy();
    expect(screen.getByText('Files')).toBeTruthy();
    expect(screen.getByText('Settings')).toBeTruthy();
  });
});

// ─── Queue badges — empty queue ───────────────────────────────────────────────

describe('Queue badges — empty queue', () => {
  it('shows no badges when all counts are zero', () => {
    renderOnFleet(0, 0, 0);
    expect(screen.queryByTestId('badge-active')).toBeNull();
    expect(screen.queryByTestId('badge-pending')).toBeNull();
    expect(screen.queryByTestId('badge-blocked')).toBeNull();
  });

  it('never renders a badge with text "0"', () => {
    renderOnFleet(0, 0, 0);
    expect(screen.queryByText('0')).toBeNull();
  });
});

// ─── Queue badges — individual types ─────────────────────────────────────────

describe('Queue badges — individual types', () => {
  it('shows active badge (green) when jobs are in progress', () => {
    renderOnFleet(3, 0, 0);
    const badge = screen.getByTestId('badge-active');
    expect(badge.textContent).toBe('3');
    expect(badge.style.color).toBe('var(--ok)');
    expect(badge.style.background).toBe('var(--ok-bg)');
    expect(screen.queryByTestId('badge-pending')).toBeNull();
    expect(screen.queryByTestId('badge-blocked')).toBeNull();
  });

  it('shows pending badge (neutral) when jobs are queued', () => {
    renderOnFleet(0, 5, 0);
    const badge = screen.getByTestId('badge-pending');
    expect(badge.textContent).toBe('5');
    // Pending uses the default .count class styling (no inline color override)
    expect(badge.style.color).toBe('');
    expect(screen.queryByTestId('badge-active')).toBeNull();
    expect(screen.queryByTestId('badge-blocked')).toBeNull();
  });

  it('shows blocked badge (red) when jobs are blocked', () => {
    renderOnFleet(0, 0, 2);
    const badge = screen.getByTestId('badge-blocked');
    expect(badge.textContent).toBe('2');
    expect(badge.style.color).toBe('var(--err)');
    expect(screen.queryByTestId('badge-active')).toBeNull();
    expect(screen.queryByTestId('badge-pending')).toBeNull();
  });
});

// ─── Queue badges — combinations ──────────────────────────────────────────────

describe('Queue badges — combinations', () => {
  it('shows all three badges simultaneously with correct counts', () => {
    renderOnFleet(2, 5, 1);
    expect(screen.getByTestId('badge-active').textContent).toBe('2');
    expect(screen.getByTestId('badge-pending').textContent).toBe('5');
    expect(screen.getByTestId('badge-blocked').textContent).toBe('1');
  });

  it('shows only active and pending when blocked is zero', () => {
    renderOnFleet(1, 4, 0);
    expect(screen.getByTestId('badge-active').textContent).toBe('1');
    expect(screen.getByTestId('badge-pending').textContent).toBe('4');
    expect(screen.queryByTestId('badge-blocked')).toBeNull();
  });

  it('shows only active and blocked when pending is zero', () => {
    renderOnFleet(1, 0, 3);
    expect(screen.getByTestId('badge-active').textContent).toBe('1');
    expect(screen.queryByTestId('badge-pending')).toBeNull();
    expect(screen.getByTestId('badge-blocked').textContent).toBe('3');
  });

  it('shows only pending and blocked when active is zero', () => {
    renderOnFleet(0, 7, 2);
    expect(screen.queryByTestId('badge-active')).toBeNull();
    expect(screen.getByTestId('badge-pending').textContent).toBe('7');
    expect(screen.getByTestId('badge-blocked').textContent).toBe('2');
  });
});

// ─── Queue badges — active nav item ───────────────────────────────────────────

describe('Queue badges — active nav item (/queue page)', () => {
  it('still renders all three badges when on the queue page', () => {
    renderOnQueue(2, 3, 1);
    expect(screen.getByTestId('badge-active').textContent).toBe('2');
    expect(screen.getByTestId('badge-pending').textContent).toBe('3');
    expect(screen.getByTestId('badge-blocked').textContent).toBe('1');
  });

  it('shows no badges when queue is empty, even on the queue page', () => {
    renderOnQueue(0, 0, 0);
    expect(screen.queryByTestId('badge-active')).toBeNull();
    expect(screen.queryByTestId('badge-pending')).toBeNull();
    expect(screen.queryByTestId('badge-blocked')).toBeNull();
  });
});

// ─── Status semantics ─────────────────────────────────────────────────────────

describe('Queue badge status semantics', () => {
  it('counts active correctly for all in-progress statuses', () => {
    // Active covers: printing, paused, slicing, uploading — verified by App.tsx.
    // Sidebar only receives the pre-computed count; verify count display accuracy.
    renderOnFleet(4, 0, 0);
    expect(screen.getByTestId('badge-active').textContent).toBe('4');
  });

  it('blocked is distinct from failed — blocked jobs stay in queue, failed are removed', () => {
    // A blocked job (filament mismatch / slice error at grab) stays visible with count 1.
    // A failed job (all retries exhausted) is stripped from the queue and never counted.
    renderOnFleet(0, 0, 1);
    expect(screen.getByTestId('badge-blocked').textContent).toBe('1');
    // No active or pending badge — confirms blocked is its own category
    expect(screen.queryByTestId('badge-active')).toBeNull();
    expect(screen.queryByTestId('badge-pending')).toBeNull();
  });
});

// ─── Sidebar identity + live printer count ───────────────────────────────────

describe('Sidebar identity + printer count', () => {
  it('hides the identity row when operatorName is null', () => {
    const { container } = renderOnFleet(0, 0, 0, null, 3);
    expect(container.querySelector('.user-chip')).toBeNull();
  });

  it('still renders the printer count line when operatorName is null', () => {
    renderOnFleet(0, 0, 0, null, 3);
    expect(screen.getByText('3 printers')).toBeTruthy();
  });

  it('shows the identity row with single-word initials', () => {
    renderOnFleet(0, 0, 0, 'Maria', 1);
    expect(screen.getByText('Maria')).toBeTruthy();
    expect(screen.getByText('M')).toBeTruthy();
  });

  it('shows the identity row with two-word initials', () => {
    renderOnFleet(0, 0, 0, 'Maria Gomez', 1);
    expect(screen.getByText('Maria Gomez')).toBeTruthy();
    expect(screen.getByText('MG')).toBeTruthy();
  });

  it('uses singular "printer" for a count of 1', () => {
    renderOnFleet(0, 0, 0, null, 1);
    expect(screen.getByText('1 printer')).toBeTruthy();
  });

  it('uses plural "printers" for a count other than 1', () => {
    renderOnFleet(0, 0, 0, null, 0);
    expect(screen.getByText('0 printers')).toBeTruthy();
  });
});
