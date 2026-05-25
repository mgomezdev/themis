import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusPill, Progress, Swatch, MaterialChip, Card, SectionHeader, Empty, Kv } from './ui';
import { Icons } from './icons';

describe('StatusPill', () => {
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
  it('renders without crashing', () => {
    const { container } = render(<Progress value={50} />);
    expect(container.querySelector('.progress')).toBeTruthy();
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
