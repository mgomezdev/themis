/* global React, window */
const { useState } = React;

// =========================================================================
// Fleet dashboard — overview of all printers, intentionally calm.
// Click a tile to expand it inline with full detail + camera feed.
// =========================================================================

function FleetScreen({ tweaks }) {
  const layout = tweaks.printerCard || "grid";  // grid | list | focus
  const [expandedId, setExpandedId] = useState(null);

  const toggle = (id) => setExpandedId(expandedId === id ? null : id);

  return (
    <div className="col gap-5">
      <div className="row between">
        <div>
          <div className="tag-key" style={{ marginBottom: 2 }}>Workshop</div>
          <div className="row gap-3" style={{ alignItems: "baseline", whiteSpace: "nowrap" }}>
            <div style={{ fontSize: 22, fontWeight: 600, letterSpacing:"-0.02em" }}>3 printers online</div>
            <div className="muted small">2 printing · 1 idle</div>
          </div>
        </div>
        <div className="row gap-2">
          <button className="btn sm" style={{whiteSpace:"nowrap"}}>{window.Icons.refresh} Sync now</button>
        </div>
      </div>

      {layout === "list"  && <FleetList  expandedId={expandedId} onToggle={toggle} />}
      {layout === "focus" && <FleetFocus expandedId={expandedId} onToggle={toggle} />}
      {layout === "grid"  && <FleetGrid  expandedId={expandedId} onToggle={toggle} />}
    </div>
  );
}

// ----- GRID layout -----
// Calm 3-up tiles. The expanded tile spans the full row and shows full detail.
function FleetGrid({ expandedId, onToggle }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 16 }}>
      {window.PRINTERS.map(p => {
        const expanded = expandedId === p.id;
        return (
          <div key={p.id}
               style={{ gridColumn: expanded ? "1 / -1" : "auto" }}>
            {expanded
              ? <PrinterExpandedCard printer={p} onCollapse={() => onToggle(p.id)} />
              : <PrinterTile printer={p} onClick={() => onToggle(p.id)} />}
          </div>
        );
      })}
    </div>
  );
}

function PrinterTile({ printer: p, onClick }) {
  const isPrinting = p.status === "printing";
  const remaining = p.timeRemaining;
  return (
    <div className="card" onClick={onClick} style={{ cursor: "pointer", padding: 0, overflow: "hidden",
                                                     transition: "border-color 120ms ease" }}>
      {/* Header strip — quiet */}
      <div className="row between" style={{ padding: "12px 14px 8px" }}>
        <div className="row gap-2" style={{ alignItems: "baseline" }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>{p.nickname}</span>
          <span className="tiny muted">{p.badge}</span>
        </div>
        <window.StatusPill status={p.status} />
      </div>

      <div style={{ padding: "0 14px" }}>
        <window.VideoTile live={isPrinting} />
      </div>

      <div className="row between" style={{ padding: "12px 14px 10px" }}>
        <div className="row gap-2" style={{ alignItems: "center", minWidth: 0 }}>
          <window.Swatch color={p.material.color} />
          <div className="col" style={{ minWidth: 0 }}>
            <div className="small" style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {p.material.name}
            </div>
            <div className="tiny muted">{p.material.type}</div>
          </div>
        </div>
        {isPrinting ? (
          <div className="col" style={{ alignItems: "flex-end", whiteSpace: "nowrap" }}>
            <div className="num" style={{ fontSize: 16, fontWeight: 600, letterSpacing:"-0.01em", color:"var(--text-1)" }}>
              {window.fmtTime(remaining)}
            </div>
            <div className="tiny muted">remaining</div>
          </div>
        ) : (
          <div className="tiny muted" style={{ textAlign: "right", whiteSpace:"nowrap" }}>{p.note || "—"}</div>
        )}
      </div>

      {isPrinting && (
        <div style={{ padding: "0 14px 14px" }}>
          <window.Progress value={p.progress} />
          <div className="row between" style={{ marginTop: 6 }}>
            <span className="tiny muted">{p.currentJobId}</span>
            <span className="num tiny muted">{p.progress}%</span>
          </div>
        </div>
      )}
    </div>
  );
}

// The expanded tile — same width as the row, full detail inline.
function PrinterExpandedCard({ printer: p, onCollapse }) {
  const isPrinting = p.status === "printing";
  const job = p.currentJobId ? window.JOBS.find(j => j.id === p.currentJobId) : null;

  return (
    <div className="card" style={{
      padding: 0,
      overflow: "hidden",
      borderColor: "var(--border-3)",
      boxShadow: "0 0 0 1px var(--accent-glow), 0 18px 40px -20px rgba(0,0,0,0.6)",
    }}>
      {/* Header bar */}
      <div className="row between" style={{
        padding: "14px 18px",
        background: "var(--bg-3)",
        borderBottom: "1px solid var(--border-1)",
        gap: 16,
        alignItems: "center",
      }}>
        <div className="col" style={{ minWidth: 0, flex: 1 }}>
          <div className="row gap-2" style={{ alignItems: "baseline", whiteSpace: "nowrap" }}>
            <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: "-0.02em" }}>{p.nickname}</div>
            <div className="muted small" style={{ overflow:"hidden", textOverflow:"ellipsis", minWidth: 0 }}>{p.name}</div>
            <span className="mono tiny muted" style={{ flexShrink: 0 }}>· {p.id}</span>
          </div>
          <div className="tiny muted" style={{ marginTop: 2,
            whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis" }}>
            <span className="num">{p.buildVolume}</span> mm · {p.chamber ? "enclosed" : "open frame"} · capable: {p.capabilities.join(" · ")}
          </div>
        </div>
        <div className="row gap-2" style={{ flexShrink: 0 }}>
          <window.StatusPill status={p.status} />
          <button className="btn sm">{window.Icons.camera} Snapshot</button>
          <button className="btn icon sm">{window.Icons.more}</button>
          <button className="btn ghost icon sm" title="Collapse" onClick={onCollapse}>{window.Icons.x}</button>
        </div>
      </div>

      {/* Body — left: big feed + telemetry & job; right: material + temps + caps */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1.6fr) minmax(280px, 1fr)",
        gap: 18,
        padding: 18,
      }}>
        {/* LEFT */}
        <div className="col gap-4" style={{ minWidth: 0 }}>
          <window.VideoTile live={isPrinting} time={p.timeElapsed} />

          {/* Big telemetry numbers */}
          <div className="row gap-6" style={{ flexWrap: "wrap" }}>
            <window.Kv k="Progress"  v={<span className="num" style={{fontSize:22, fontWeight:600}}>{p.progress}%</span>} />
            <window.Kv k="Time left" v={<span className="num" style={{fontSize:22, fontWeight:600,
                                          color: isPrinting ? "var(--accent-hi)" : "var(--text-3)"}}>
                                          {isPrinting ? window.fmtTime(p.timeRemaining) : "—"}
                                       </span>} />
            <window.Kv k="Elapsed"   v={<span className="num" style={{fontSize:22}}>{isPrinting ? window.fmtTime(p.timeElapsed) : "—"}</span>} />
            {p.layer && <window.Kv k="Layer" v={<span className="num" style={{fontSize:22}}>{p.layer.now}<span className="muted" style={{fontSize:14}}> / {p.layer.total}</span></span>} />}
          </div>
          {isPrinting && <window.Progress value={p.progress} large />}

          {/* Action row */}
          <div className="row gap-2" style={{ marginTop: 2 }}>
            {isPrinting && <>
              <button className="btn">{window.Icons.pause} Pause</button>
              <button className="btn">{window.Icons.stop} Stop</button>
            </>}
            {!isPrinting && <button className="btn primary">{window.Icons.play} Claim next from queue</button>}
            <div style={{ flex: 1 }} />
            <button className="btn ghost sm">{window.Icons.external} Open in slicer</button>
          </div>

          {/* Current job */}
          {job && (
            <div className="card" style={{ padding: 14, background:"var(--bg-1)" }}>
              <div className="row between" style={{ marginBottom: 10, alignItems: "center" }}>
                <div className="col">
                  <span className="tag-key">Current job</span>
                  <div className="row gap-2" style={{ marginTop: 2 }}>
                    <span className="mono tiny muted">{job.id}</span>
                    <span style={{ fontSize: 13, fontWeight: 500 }}>{job.plateName}</span>
                  </div>
                </div>
                <button className="btn ghost sm">{window.Icons.arrowR} Open job</button>
              </div>
              <div className="col gap-2">
                {window.partsFromJob(job).map((p2, i) => (
                  <div key={i} className="row between" style={{
                    padding: "8px 12px",
                    background: "var(--bg-2)",
                    borderRadius: 8,
                    border: "1px solid var(--border-1)",
                  }}>
                    <div className="col" style={{ minWidth: 0 }}>
                      <div className="small" style={{ fontWeight: 500 }}>{p2.name}</div>
                      <div className="tiny muted">{p2.orderId} · {p2.material}</div>
                    </div>
                    <div className="num small" style={{flexShrink:0}}>×{p2.qty}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* RIGHT */}
        <div className="col gap-4">
          <div className="card" style={{ padding: 14, background:"var(--bg-1)" }}>
            <div className="tag-key">Loaded material</div>
            <div className="row gap-3" style={{ marginTop: 10 }}>
              <window.Swatch color={p.material.color} large />
              <div className="col" style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 500, whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis" }}>{p.material.name}</div>
                <div className="tiny muted">{p.material.type}</div>
              </div>
            </div>
            <button className="btn sm" style={{ marginTop: 12, width:"100%" }}>
              {window.Icons.external} Open in Spool Buddy
            </button>
          </div>

          <div className="card" style={{ padding: 14, background:"var(--bg-1)" }}>
            <div className="tag-key" style={{ marginBottom: 10 }}>Telemetry</div>
            <div className="col gap-3">
              <Telem label="Nozzle"  value={`${p.nozzleTemp}°C`} target={isPrinting ? "220°C" : "—"} tone={isPrinting ? "warn" : null} />
              <Telem label="Bed"     value={`${p.bedTemp}°C`}    target={isPrinting ? "60°C" : "—"} />
              {p.chamberTemp != null && <Telem label="Chamber" value={`${p.chamberTemp}°C`} target={p.chamber ? "60°C" : "—"} />}
            </div>
          </div>

          <div className="card" style={{ padding: 14, background:"var(--bg-1)" }}>
            <div className="row between" style={{ marginBottom: 10 }}>
              <span className="tag-key">Queue eligibility</span>
              <button className="btn ghost sm">Edit</button>
            </div>
            <div className="col gap-2 small">
              {p.capabilities.map(c => (
                <div key={c} className="row gap-2">
                  <span style={{color:"var(--ok)"}}>{window.Icons.check}</span>
                  <span>{c}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ----- LIST layout — with inline expand row -----
function FleetList({ expandedId, onToggle }) {
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <table className="tbl">
        <thead>
          <tr>
            <th style={{ width: 24 }}></th>
            <th style={{ width: 48 }}></th>
            <th>Printer</th>
            <th>Status</th>
            <th>Material</th>
            <th>Current job</th>
            <th style={{ width: 220 }}>Progress</th>
            <th style={{ width: 100, textAlign: "right" }}>Time left</th>
          </tr>
        </thead>
        <tbody>
          {window.PRINTERS.map(p => {
            const expanded = expandedId === p.id;
            return (
              <React.Fragment key={p.id}>
                <tr onClick={() => onToggle(p.id)}
                    style={{ background: expanded ? "var(--bg-3)" : undefined }}>
                  <td>
                    <span style={{
                      display: "inline-flex", color: "var(--text-3)",
                      transform: expanded ? "rotate(90deg)" : "none",
                      transition: "transform 120ms ease",
                    }}>{window.Icons.chevR}</span>
                  </td>
                  <td>
                    <div style={{ width: 36, height: 36, borderRadius: 6,
                                  background: `linear-gradient(135deg, ${p.accent}33, transparent)`,
                                  border:"1px solid var(--border-1)",
                                  display:"grid", placeItems:"center" }}>
                      <span className="mono tiny" style={{ color: p.accent }}>{p.badge}</span>
                    </div>
                  </td>
                  <td>
                    <div style={{ fontWeight: 500 }}>{p.nickname}</div>
                    <div className="tiny muted">{p.name}</div>
                  </td>
                  <td><window.StatusPill status={p.status} /></td>
                  <td><window.MaterialChip material={p.material.name} color={p.material.color} /></td>
                  <td>{p.currentJobId ? <span className="mono tiny">{p.currentJobId}</span> : <span className="muted tiny">—</span>}</td>
                  <td>
                    {p.status === "printing"
                      ? <window.Progress value={p.progress} />
                      : <span className="tiny muted">{p.note || "—"}</span>}
                  </td>
                  <td className="num" style={{ textAlign: "right",
                                               color: p.status === "printing" ? "var(--text-1)" : "var(--text-3)" }}>
                    {p.status === "printing" ? window.fmtTime(p.timeRemaining) : "—"}
                  </td>
                </tr>
                {expanded && (
                  <tr>
                    <td colSpan={8} style={{ padding: 0, background: "var(--bg-1)", cursor: "default" }}
                        onClick={e => e.stopPropagation()}>
                      <div style={{ padding: 18 }}>
                        <PrinterExpandedCard printer={p} onCollapse={() => onToggle(p.id)} />
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ----- FOCUS layout — one big tile + thumbnail rail (already an "expanded view") -----
function FleetFocus({ expandedId, onToggle }) {
  const [focusId, setFocusId] = useState(window.PRINTERS[0].id);
  const hero = window.getPrinter(focusId);
  return (
    <div className="screen-grid" style={{ gridTemplateColumns: "1fr 320px", gap: 18 }}>
      <PrinterExpandedCard printer={hero} onCollapse={() => {}} />
      <div className="col gap-3">
        {window.PRINTERS.map(p => (
          <PrinterStripCard key={p.id} printer={p}
                            active={p.id === focusId}
                            onClick={() => setFocusId(p.id)} />
        ))}
      </div>
    </div>
  );
}

function PrinterStripCard({ printer: p, active, onClick }) {
  const isPrinting = p.status === "printing";
  return (
    <div onClick={onClick}
         className="card"
         style={{
           padding: 10,
           cursor: "pointer",
           borderColor: active ? "var(--accent)" : undefined,
           background: active ? "var(--bg-3)" : "var(--bg-2)",
         }}>
      <div className="row gap-3">
        <div style={{ width: 72, height: 48, borderRadius: 6, overflow:"hidden", flexShrink:0 }}>
          <window.VideoTile live={isPrinting} />
        </div>
        <div className="col" style={{ flex:1, minWidth: 0 }}>
          <div className="row between" style={{ alignItems:"center" }}>
            <div className="small" style={{ fontWeight: 500 }}>{p.nickname}</div>
            <window.StatusPill status={p.status} />
          </div>
          <div className="row gap-2" style={{ marginTop: 4 }}>
            <window.Swatch color={p.material.color} />
            <div className="tiny muted" style={{ whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis" }}>
              {p.material.name}
            </div>
          </div>
          {isPrinting && (
            <div className="row between" style={{ marginTop: 4 }}>
              <span className="num tiny muted">{p.progress}%</span>
              <span className="num tiny" style={{color:"var(--accent-hi)"}}>{window.fmtTime(p.timeRemaining)}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Telem({ label, value, target, tone }) {
  return (
    <div className="row between">
      <div className="small muted">{label}</div>
      <div className="row gap-2" style={{ alignItems:"baseline" }}>
        <span className="num" style={{ fontSize: 14, fontWeight: 600, color: tone === "warn" ? "var(--warn)" : "var(--text-1)" }}>{value}</span>
        <span className="num tiny muted">/ {target}</span>
      </div>
    </div>
  );
}

Object.assign(window, { FleetScreen });
