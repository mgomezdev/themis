import { useEffect, useRef, useState } from 'react';

interface DetectedCode { rawValue: string }
interface BarcodeDetectorLike { detect(source: CanvasImageSource): Promise<DetectedCode[]> }

declare global {
  interface Window { BarcodeDetector?: new (opts?: { formats: string[] }) => BarcodeDetectorLike }
}

/** True where the browser can decode QR codes from the camera (Chromium/Android; not Firefox or older Safari). */
export const qrScanSupported = () =>
  typeof window !== 'undefined' && !!window.BarcodeDetector && !!navigator.mediaDevices?.getUserMedia;

/**
 * Scans QR codes from the rear camera into `videoRef` while `active`; calls `onCode` for each distinct
 * code (the same code isn't repeated until a different one is seen). Camera errors land in `error`.
 */
export function useQrScanner(active: boolean, onCode: (text: string) => void) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);
  const onCodeRef = useRef(onCode);
  onCodeRef.current = onCode;

  useEffect(() => {
    if (!active) return;
    if (!qrScanSupported()) { setError('This browser cannot scan QR codes — type the code instead.'); return; }
    let stream: MediaStream | null = null;
    let timer: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;
    let last = '';
    setError(null);

    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
        if (cancelled) { stream.getTracks().forEach(t => t.stop()); return; }
        const video = videoRef.current;
        if (!video) return;
        video.srcObject = stream;
        await video.play().catch(() => {});
        const detector = new window.BarcodeDetector!({ formats: ['qr_code'] });
        timer = setInterval(async () => {
          try {
            const [hit] = await detector.detect(video);
            if (hit && hit.rawValue !== last) { last = hit.rawValue; onCodeRef.current(hit.rawValue); }
          } catch { /* a frame that can't be decoded yet */ }
        }, 300);
      } catch (e) {
        setError(e instanceof Error && e.name === 'NotAllowedError'
          ? 'Camera permission was denied — allow it in the browser, or type the code instead.'
          : 'Could not start the camera — type the code instead.');
      }
    })();

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
      stream?.getTracks().forEach(t => t.stop());
    };
  }, [active]);

  return { videoRef, error };
}
