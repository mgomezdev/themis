import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { Icons } from './icons';

describe('Icons', () => {
  it('renders queue icon as SVG', () => {
    const { container } = render(<>{Icons.queue}</>);
    expect(container.querySelector('svg')).toBeTruthy();
  });
  it('has all required icons', () => {
    const required = ['queue', 'fleet', 'printer', 'orders', 'files', 'settings', 'plus', 'x'];
    required.forEach(k => expect(Icons).toHaveProperty(k));
  });
});
