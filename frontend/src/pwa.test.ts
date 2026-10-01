/// <reference types="vite/client" />
// Home-screen install (BIZ-156): index.html must point at a manifest whose icons exist and cover the sizes
// browsers require for installability.
import { describe, expect, it } from 'vitest';
import html from '../index.html?raw';
import manifestRaw from '../public/manifest.webmanifest?raw';

const manifest = JSON.parse(manifestRaw) as {
  name: string; start_url: string; display: string; icons: { src: string; sizes: string; type: string; purpose?: string }[];
};
const iconFiles = Object.keys(import.meta.glob('../public/icons/*.png', { query: '?url', eager: false }));
const onDisk = (src: string) => iconFiles.includes(`../public${src}`);

describe('PWA manifest', () => {
  it('is linked from index.html along with an iOS home-screen icon', () => {
    expect(html).toContain('<link rel="manifest" href="/manifest.webmanifest"');
    expect(html).toContain('rel="apple-touch-icon" href="/icons/apple-touch-icon.png"');
    expect(onDisk('/icons/apple-touch-icon.png')).toBe(true);
  });

  it('installs as a standalone app starting at the root', () => {
    expect(manifest.name).toBe('Themis');
    expect(manifest.start_url).toBe('/');
    expect(manifest.display).toBe('standalone');
  });

  it('ships 192px, 512px and a maskable 512px icon, all present on disk', () => {
    const bySize = (size: string, purpose?: string) =>
      manifest.icons.find(i => i.sizes === size && i.purpose === purpose);
    expect(bySize('192x192')).toBeTruthy();
    expect(bySize('512x512')).toBeTruthy();
    expect(bySize('512x512', 'maskable')).toBeTruthy();
    for (const icon of manifest.icons) {
      expect(icon.type).toBe('image/png');
      expect(onDisk(icon.src), icon.src).toBe(true);
    }
  });
});
