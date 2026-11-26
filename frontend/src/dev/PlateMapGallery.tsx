import { useState } from 'react';
import { PlateMap } from '../components/plateMap/PlateMap';
import { corpus } from './plateMapCorpus';
import type { CorpusCase } from './plateMapCorpus';

function Fixture({ c }: { c: CorpusCase }) {
  const [clicked, setClicked] = useState<string | null>(null);
  return (
    <section className="card plate-gallery-case" id={c.name} data-fixture={c.name}>
      <h2 className="plate-gallery-title mono">{c.name}</h2>
      <p className="small muted">{c.covers}</p>
      <PlateMap plate={c.plate} onObjectClick={setClicked} />
      <p className="small" role="status" aria-label={`${c.name} last click`}>
        Last click: <span className="mono">{clicked ?? '—'}</span>
      </p>
    </section>
  );
}

/** Dev-only page (frontend/plate-map.html, served by `npm run dev` at /plate-map.html): every corpus fixture, by eye. */
export function PlateMapGallery() {
  return (
    <main className="content plate-gallery">
      <h1>Plate map fixtures</h1>
      <p className="small muted">contracts/plate-map-cases.json — the same cases the view and snapshot tests check.</p>
      {corpus.cases.map(c => <Fixture key={c.name} c={c} />)}
    </main>
  );
}
