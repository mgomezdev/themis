import { useEffect, useRef } from 'react';

interface Props {
  fileId: number;
  size?: number;
}

export function StlPreview({ fileId, size = 80 }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let alive = true;
    fetch(`/api/v1/files/${fileId}/download`)
      .then(r => r.arrayBuffer())
      .then(buf => { if (alive && ref.current) renderStl(ref.current, buf, size); })
      .catch(() => {});
    return () => { alive = false; };
  }, [fileId, size]);

  return <canvas ref={ref} width={size} height={size} style={{ display: 'block' }} />;
}

function renderStl(canvas: HTMLCanvasElement, buf: ArrayBuffer, size: number) {
  const view = new DataView(buf);
  if (buf.byteLength < 84) return;
  const count = view.getUint32(80, true);
  if (count === 0 || count > 300000 || 84 + count * 50 > buf.byteLength) return;

  // Parse triangles
  const verts: [number, number, number][][] = [];
  const normals: [number, number, number][] = [];
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, minZ = Infinity, maxZ = -Infinity;

  for (let i = 0; i < count; i++) {
    const b = 84 + i * 50;
    normals.push([view.getFloat32(b, true), view.getFloat32(b + 4, true), view.getFloat32(b + 8, true)]);
    const tv: [number, number, number][] = [];
    for (let v = 0; v < 3; v++) {
      const vb = b + 12 + v * 12;
      const x = view.getFloat32(vb, true), y = view.getFloat32(vb + 4, true), z = view.getFloat32(vb + 8, true);
      tv.push([x, y, z]);
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
      if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
    }
    verts.push(tv);
  }

  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2, cz = (minZ + maxZ) / 2;
  const span = Math.max(maxX - minX, maxY - minY, maxZ - minZ, 0.001);
  const sc = 2 / span;

  // Isometric: 45° yaw, 30° pitch
  const ay = Math.PI / 4, ax = Math.PI / 6;
  const cosY = Math.cos(ay), sinY = Math.sin(ay), cosX = Math.cos(ax), sinX = Math.sin(ax);

  function rot(x: number, y: number, z: number): [number, number, number] {
    const rx = x * cosY + z * sinY, rz = -x * sinY + z * cosY;
    return [rx, y * cosX - rz * sinX, y * sinX + rz * cosX];
  }

  const margin = 5;
  const half = size / 2;
  // Light from upper-left-front, normalised
  const [lx, ly, lz] = [-0.4, 0.8, 0.4].map(v => v / Math.sqrt(0.16 + 0.64 + 0.16)) as [number, number, number];

  type Tri = { px: number[]; py: number[]; depth: number; shade: number };
  const tris: Tri[] = verts.map((tv, i) => {
    const rv = tv.map(([x, y, z]) => rot((x - cx) * sc, (y - cy) * sc, (z - cz) * sc));
    const depth = (rv[0][2] + rv[1][2] + rv[2][2]) / 3;
    const [nx, ny, nz] = rot(...normals[i]);
    const dot = nx * lx + ny * ly + nz * lz;
    const shade = Math.max(0.08, dot);
    return {
      px: rv.map(v => half + v[0] * (half - margin)),
      py: rv.map(v => half - v[1] * (half - margin)),
      depth, shade,
    };
  });

  tris.sort((a, b) => a.depth - b.depth);

  const ctx = canvas.getContext('2d')!;
  ctx.clearRect(0, 0, size, size);
  ctx.fillStyle = '#111';
  ctx.fillRect(0, 0, size, size);

  for (const t of tris) {
    ctx.beginPath();
    ctx.moveTo(t.px[0], t.py[0]);
    ctx.lineTo(t.px[1], t.py[1]);
    ctx.lineTo(t.px[2], t.py[2]);
    ctx.closePath();
    // Slight blue-cyan tint matching the app's accent palette
    const v = Math.round(t.shade * 220);
    ctx.fillStyle = `rgb(${Math.round(v * 0.75)},${Math.round(v * 0.92)},${v})`;
    ctx.fill();
  }
}
