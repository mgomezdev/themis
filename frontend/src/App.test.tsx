import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import App from './App';

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal('fetch', vi.fn(async () => new Response('[]', { status: 200 })));
  class FakeWS { onmessage = null; onopen = null; onclose = null; close() {} send() {} }
  vi.stubGlobal('WebSocket', FakeWS as unknown as typeof WebSocket);
});

describe('App nav', () => {
  it('has no Filaments link', () => {
    render(<App />);
    expect(screen.queryByRole('link', { name: /Filaments/i })).toBeNull();
    expect(screen.queryByText(/Filament library/i)).toBeNull();
  });
});
