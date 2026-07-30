// frontend/src/components/MaintenanceDueBadge.test.tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MaintenanceDueBadge } from './MaintenanceDueBadge';

describe('MaintenanceDueBadge', () => {
  it('renders nothing when count is 0', () => {
    const { container } = render(<MaintenanceDueBadge count={0} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders a due count when > 0', () => {
    render(<MaintenanceDueBadge count={2} />);
    expect(screen.getByText(/2 due/i)).toBeInTheDocument();
  });

  it('singularizes the title tooltip for a count of 1', () => {
    render(<MaintenanceDueBadge count={1} />);
    const badge = screen.getByText(/1 due/i);
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveAttribute('title', '1 maintenance item due');
  });
});
