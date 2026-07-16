import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { StatusPill, Progress, Card, Empty, Kv, VideoTile } from './ui';
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

  it('hides img when onError fires', () => {
    render(<VideoTile live={true} printerId="42" />);
    fireEvent.error(document.querySelector('img')!);
    expect(document.querySelector('img')).toBeNull();
  });
});
