import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { qrScanSupported, useQrScanner } from './useQrScanner';

function Probe({ active, onCode }: { active: boolean; onCode: (t: string) => void }) {
  const { videoRef, error } = useQrScanner(active, onCode);
  return <div><video ref={videoRef} data-testid="video" />{error && <span role="alert">{error}</span>}</div>;
}

const track = () => ({ stop: vi.fn() });

function stubCamera(codes: string[][]) {
  const t = track();
  const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [t] });
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });
  const frames = [...codes];
  window.BarcodeDetector = class { detect = vi.fn(async () => (frames.shift() ?? []).map(rawValue => ({ rawValue }))); } as never;
  HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  return { t, getUserMedia };
}

describe('useQrScanner', () => {
  afterEach(() => { vi.unstubAllGlobals(); delete window.BarcodeDetector; });

  it('is unsupported without BarcodeDetector and then reports a helpful error instead of opening the camera', () => {
    expect(qrScanSupported()).toBe(false);
    render(<Probe active onCode={vi.fn()} />);
    expect(screen.getByRole('alert').textContent).toMatch(/cannot scan QR codes/);
  });

  it('reports each distinct code once, and releases the camera when deactivated', async () => {
    const { t, getUserMedia } = stubCamera([['a'], ['a'], ['b'], []]);
    const onCode = vi.fn();
    const { rerender } = render(<Probe active onCode={onCode} />);

    await waitFor(() => expect(onCode).toHaveBeenCalledTimes(2), { timeout: 2000 });
    expect(onCode.mock.calls.map(c => c[0])).toEqual(['a', 'b']);
    expect(getUserMedia).toHaveBeenCalledWith({ video: { facingMode: 'environment' } });

    rerender(<Probe active={false} onCode={onCode} />);
    expect(t.stop).toHaveBeenCalled();
  });

  it('explains a denied camera permission', async () => {
    const err = Object.assign(new Error('no'), { name: 'NotAllowedError' });
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: vi.fn().mockRejectedValue(err) } });
    window.BarcodeDetector = class { detect = vi.fn(); } as never;
    render(<Probe active onCode={vi.fn()} />);

    expect((await screen.findByRole('alert')).textContent).toMatch(/permission was denied/);
  });
});
