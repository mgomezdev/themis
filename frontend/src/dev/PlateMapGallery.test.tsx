import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { PlateMapGallery } from './PlateMapGallery';
import { corpus } from './plateMapCorpus';

describe('PlateMapGallery', () => {
  it('renders one section per corpus fixture', () => {
    const { container } = render(<PlateMapGallery />);
    expect([...container.querySelectorAll('[data-fixture]')].map(s => s.getAttribute('data-fixture'))).toEqual(corpus.cases.map(c => c.name));
  });

  it('shows the last clicked label for a fixture', () => {
    const { container } = render(<PlateMapGallery />);
    const section = container.querySelector<HTMLElement>('[data-fixture="overlap"]')!;
    fireEvent.click(within(section).getByTitle('Twin A'));
    expect(screen.getByRole('status', { name: 'overlap last click' })).toHaveTextContent('Last click: Twin A');
  });
});
