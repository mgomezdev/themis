// frontend/src/components/DueMaintenanceHat.test.tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DueMaintenanceHat } from './DueMaintenanceHat';

describe('DueMaintenanceHat', () => {
  it('renders nothing when no items are due', () => {
    const { container } = render(<DueMaintenanceHat dueItemNames={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders the hat with a title listing due items for desktop hover', () => {
    render(<DueMaintenanceHat dueItemNames={['Wash build plate', 'Clean fans']} />);
    expect(screen.getByTitle('Wash build plate, Clean fans')).toBeInTheDocument();
  });

  it('toggles a visible tooltip popover on click, for mobile tap', () => {
    render(<DueMaintenanceHat dueItemNames={['Wash build plate']} />);
    expect(screen.queryByText('Wash build plate', { selector: 'div' })).toBeNull();
    fireEvent.click(screen.getByTitle('Wash build plate'));
    expect(screen.getByText('Wash build plate', { selector: 'div' })).toBeInTheDocument();
    fireEvent.click(screen.getByTitle('Wash build plate'));
    expect(screen.queryByText('Wash build plate', { selector: 'div' })).toBeNull();
  });

  it('stops click propagation so it does not trigger a parent card click', () => {
    const onCardClick = vi.fn();
    render(
      <div onClick={onCardClick}>
        <DueMaintenanceHat dueItemNames={['Wash build plate']} />
      </div>
    );
    fireEvent.click(screen.getByTitle('Wash build plate'));
    expect(onCardClick).not.toHaveBeenCalled();
  });
});
