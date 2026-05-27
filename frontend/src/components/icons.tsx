import React from 'react';

interface IconProps {
  d?: string;
  paths?: string[];
  children?: React.ReactNode;
  size?: number;
  stroke?: number;
  [key: string]: unknown;
}

export function Icon({ d, paths, children, size = 18, stroke = 1.6, ...rest }: IconProps) {
  return (
    <svg className="ico" width={size} height={size} viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth={stroke}
         strokeLinecap="round" strokeLinejoin="round" {...rest}>
      {d && <path d={d} />}
      {paths && paths.map((p, i) => <path key={i} d={p} />)}
      {children}
    </svg>
  );
}

export const Icons = {
  queue:    <Icon paths={["M3 6h14","M3 12h14","M3 18h10","M21 6l-2 2 2 2","M21 18l-2-2 2-2"]} />,
  fleet:    <Icon paths={["M4 4h7v7H4z","M13 4h7v7h-7z","M4 13h7v7H4z","M13 13h7v7h-7z"]} />,
  printer:  <Icon paths={["M6 9V3h12v6","M6 18H4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2","M6 14h12v8H6z"]} />,
  orders:   <Icon paths={["M8 2v4","M16 2v4","M3 8h18","M5 6h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z","M9 14l2 2 4-4"]} />,
  files:    <Icon paths={["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z","M14 2v6h6","M9 13h6","M9 17h4"]} />,
  settings: <Icon paths={["M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z","M19.4 14.6l1.6.9-2 3.4-1.8-.6a8 8 0 0 1-1.6.9l-.3 1.9h-4l-.3-1.9a8 8 0 0 1-1.6-.9l-1.8.6-2-3.4 1.6-.9a8 8 0 0 1 0-1.8L4.6 12l2-3.4 1.8.6a8 8 0 0 1 1.6-.9l.3-1.9h4l.3 1.9c.6.2 1.1.5 1.6.9l1.8-.6 2 3.4-1.6.9c.1.6.1 1.2 0 1.8z"]} />,
  search:   <Icon paths={["M21 21l-4.3-4.3","M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z"]} />,
  plus:     <Icon paths={["M12 5v14","M5 12h14"]} />,
  play:     <Icon paths={["M6 4l14 8-14 8V4z"]} />,
  pause:    <Icon paths={["M7 4h3v16H7z","M14 4h3v16h-3z"]} />,
  stop:     <Icon paths={["M5 5h14v14H5z"]} />,
  alert:    <Icon paths={["M12 9v4","M12 17h.01","M10.3 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"]} />,
  check:    <Icon paths={["M20 6 9 17l-5-5"]} />,
  x:        <Icon paths={["M18 6 6 18","M6 6l12 12"]} />,
  chevR:    <Icon paths={["M9 6l6 6-6 6"]} />,
  chevD:    <Icon paths={["M6 9l6 6 6-6"]} />,
  chevU:    <Icon paths={["M6 15l6-6 6 6"]} />,
  chevL:    <Icon paths={["M15 6l-6 6 6 6"]} />,
  drag:     <Icon paths={["M9 5h.01","M15 5h.01","M9 12h.01","M15 12h.01","M9 19h.01","M15 19h.01"]} stroke={3} />,
  more:     <Icon paths={["M12 12h.01","M19 12h.01","M5 12h.01"]} stroke={3} />,
  clock:    <Icon paths={["M12 6v6l4 2","M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z"]} />,
  layers:   <Icon paths={["M12 2 2 7l10 5 10-5-10-5z","M2 17l10 5 10-5","M2 12l10 5 10-5"]} />,
  filter:   <Icon paths={["M22 3H2l8 9.5V19l4 2v-8.5L22 3z"]} />,
  sort:     <Icon paths={["M3 6h13","M3 12h9","M3 18h5","M17 9l3-3 3 3","M20 6v12","M17 15l3 3 3-3"]} />,
  link:     <Icon paths={["M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7","M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"]} />,
  thermo:   <Icon paths={["M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4 4 0 1 0 5 0z"]} />,
  spool:    <Icon paths={["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z","M12 18a6 6 0 1 0 0-12 6 6 0 0 0 0 12z","M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z"]} />,
  camera:   <Icon paths={["M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2v11z","M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"]} />,
  refresh:  <Icon paths={["M21 12a9 9 0 0 1-15.36 6.36L3 16","M3 12a9 9 0 0 1 15.36-6.36L21 8","M21 3v5h-5","M3 21v-5h5"]} />,
  bell:     <Icon paths={["M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9","M13.7 21a2 2 0 0 1-3.4 0"]} />,
  external: <Icon paths={["M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6","M15 3h6v6","M10 14L21 3"]} />,
  arrowR:   <Icon paths={["M5 12h14","M12 5l7 7-7 7"]} />,
  user:     <Icon paths={["M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2","M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"]} />,
  upload:   <Icon paths={["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4","M17 8l-5-5-5 5","M12 3v12"]} />,
  copy:     <Icon paths={["M9 9h11a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2v-1","M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"]} />,
  trash:    <Icon paths={["M3 6h18","M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2","M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6","M10 11v6","M14 11v6"]} />,
  panel:    <Icon paths={["M3 3h18v18H3z","M9 3v18"]} />,
  wrench:   <Icon paths={["M14.7 6.3a4 4 0 1 1 5.66 5.66l-1.41 1.41-5.66-5.66 1.41-1.41z","M14.7 6.3 3.5 17.5a2 2 0 1 0 2.83 2.83l11.2-11.2"]} />,
} as const;

export type IconKey = keyof typeof Icons;
