import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '../styles/app.css';
import { PlateMapGallery } from './PlateMapGallery';

createRoot(document.getElementById('root')!).render(<StrictMode><PlateMapGallery /></StrictMode>);
