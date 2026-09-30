import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { openLiveSocket } from './client';

/** Records every socket the code under test opens so a test can drive open/close/message by hand. */
class FakeWS {
  static instances: FakeWS[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeWS.instances.push(this); }
  close() { this.closed = true; }
  open() { this.onopen?.(); }
  drop() { this.onclose?.(); }
}
const last = () => FakeWS.instances[FakeWS.instances.length - 1];

beforeEach(() => {
  FakeWS.instances = [];
  localStorage.setItem('themis.apiKey', 'k e/y');
  vi.stubGlobal('WebSocket', FakeWS);
  vi.useFakeTimers();
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); localStorage.clear(); });

describe('openLiveSocket', () => {
  it('connects to /ws with the API key and hands messages to the listener', () => {
    const onMessage = vi.fn();
    openLiveSocket(onMessage);

    expect(FakeWS.instances).toHaveLength(1);
    expect(last().url).toMatch(/^wss?:\/\/[^/]+\/ws\?key=k%20e%2Fy$/);
    const frame = { data: '{"type":"x"}' } as MessageEvent;
    last().onmessage!(frame);
    expect(onMessage).toHaveBeenCalledWith(frame);
  });

  it('does not ask for a resync on the first connection', () => {
    const onReconnect = vi.fn();
    openLiveSocket(vi.fn(), onReconnect);

    last().open();

    expect(onReconnect).not.toHaveBeenCalled();
  });

  it('reconnects after the connection drops, resyncs once it is back, and keeps delivering messages', () => {
    const onMessage = vi.fn();
    const onReconnect = vi.fn();
    openLiveSocket(onMessage, onReconnect);
    last().open();

    last().drop();
    expect(FakeWS.instances).toHaveLength(1);                       // not immediately
    vi.advanceTimersByTime(999);
    expect(FakeWS.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeWS.instances).toHaveLength(2);
    expect(onReconnect).not.toHaveBeenCalled();                     // only once the new socket is actually open
    last().open();

    expect(onReconnect).toHaveBeenCalledTimes(1);
    const frame = { data: '{}' } as MessageEvent;
    last().onmessage!(frame);
    expect(onMessage).toHaveBeenCalledWith(frame);
  });

  it('also resyncs when the very first connection attempt failed and a retry gets through', () => {
    const onReconnect = vi.fn();
    openLiveSocket(vi.fn(), onReconnect);
    last().drop();                                                  // never opened
    vi.advanceTimersByTime(1000);

    last().open();

    expect(onReconnect).toHaveBeenCalledTimes(1);
  });

  it('backs off while the server stays away (1 s, 2 s, 4 s ... capped at 30 s) and starts over after a success', () => {
    openLiveSocket(vi.fn());
    const waits: number[] = [];
    for (let i = 0; i < 8; i++) {
      const before = FakeWS.instances.length;
      last().drop();
      let waited = 0;
      while (FakeWS.instances.length === before) { vi.advanceTimersByTime(500); waited += 500; }
      waits.push(waited);
    }
    expect(waits).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000]);

    last().open();                                                   // finally back
    last().drop();
    const before = FakeWS.instances.length;
    vi.advanceTimersByTime(1000);
    expect(FakeWS.instances.length).toBe(before + 1);                // next outage begins at 1 s again
  });

  it('stops for good when the caller closes it, even mid-backoff', () => {
    const close = openLiveSocket(vi.fn());
    last().open();
    last().drop();                                                   // a reconnect is now scheduled

    close();
    vi.advanceTimersByTime(60_000);

    expect(FakeWS.instances).toHaveLength(1);
  });

  it('closes the live socket when the caller closes it and does not reconnect from that close', () => {
    const onReconnect = vi.fn();
    const close = openLiveSocket(vi.fn(), onReconnect);
    const socket = last();
    socket.open();

    close();
    socket.drop();                                                   // browsers fire onclose after close()
    vi.advanceTimersByTime(60_000);

    expect(socket.closed).toBe(true);
    expect(FakeWS.instances).toHaveLength(1);
    expect(onReconnect).not.toHaveBeenCalled();
  });
});
