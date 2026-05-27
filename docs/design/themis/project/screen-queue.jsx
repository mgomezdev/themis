/* global React, window */
const { useState, useMemo } = React;

// =========================================================================
// Job Queue screen — the user's primary workflow
// =========================================================================

function QueueScreen({ tweaks, setRoute, setDrawer }) {
  const [filter, setFilter] = useState("all");      // all | queued | active | done
  const [selected, setSelected] = useState(null);   // job id
  const cardStyle = tweaks.queueCard || "compact";  // compact | rich | strip

  const jobs = useMemo(() => {
    return [...window.JOBS].sort((a,b) => {
      const order = { printing: 0, claiming: 0, slicing: 0, queued: 1, complete: 2 };
      const sa = order[a.status] ?? 9;
      const sb = order[b.status] ?? 9;
      if (sa !== sb) return sa - sb;
      return a.priority - b.priority;
    });
  }, []);

  const filtered = jobs.filter(j => {
    if (filter === "all") return true;
    if (filter === "active") return ["printing","claiming","slicing","paused"].includes(j.status);
    if (filter === "queued") return j.status === "queued";
    if (filter === "done")   return j.status === "complete";
    return true;
  });

  const totals = {
    active:  jobs.filter(j => j.status === "printing").length,
    queued:  jobs.filter(j => j.status === "queued").length,
    timeLeft: jobs.filter(j => j.status === "queued").reduce((a,b)=>a + b.estTime, 0),
  };

  const selectedJob = selected ? jobs.find(j => j.id === selected) : null;

  return (
    <div className="screen-grid" style={{ gridTemplateColumns: selectedJob ? "1fr 360px" : "1fr", gap: 18 }}>
      <div>
        {/* Summary strip */}
        <div className="row gap-3" style={{ marginBottom: 16, flexWrap:"wrap" }}>
          <SummaryStat label="In progress"  value={totals.active}   sub="2 of 3 printers" />
          <SummaryStat label="Queued"       value={totals.queued}   sub="ready when free" />
          <SummaryStat label="Queue time"   value={window.fmtTime(totals.timeLeft)} mono sub="serial est." />
          <SummaryStat label="Slicing on claim" value="auto" pillTone="accent" />
        </div>

        {/* Filter + actions */}
        <div className="row between" style={{ marginBottom: 14 }}>
          <div className="row gap-2">
            <FilterChip active={filter === "all"}    onClick={() => setFilter("all")}>All <span className="num muted" style={{marginLeft:4}}>{jobs.length}</span></FilterChip>
            <FilterChip active={filter === "active"} onClick={() => setFilter("active")}>Active <span className="num muted" style={{marginLeft:4}}>{totals.active}</span></FilterChip>
            <FilterChip active={filter === "queued"} onClick={() => setFilter("queued")}>Queued <span className="num muted" style={{marginLeft:4}}>{totals.queued}</span></FilterChip>
            <FilterChip active={filter === "done"}   onClick={() => setFilter("done")}>Done</FilterChip>
          </div>
          <div className="row gap-2">
            <button className="btn sm"><span style={{display:"inline-flex"}}>{window.Icons.sort}</span> Sort</button>
            <button className="btn sm"><span style={{display:"inline-flex"}}>{window.Icons.filter}</span> Material</button>
            <button className="btn primary sm" onClick={() => setRoute && setRoute("new-job")}>{window.Icons.plus} New job</button>
          </div>
        </div>

        {/* Job list */}
        <div className="col" style={{ gap: cardStyle === "strip" ? 1 : 8 }}>
          {filtered.map((job, i) => (
            <JobCard key={job.id}
                     job={job}
                     variant={cardStyle}
                     position={i + 1}
                     selected={selected === job.id}
                     onClick={() => setSelected(job.id === selected ? null : job.id)} />
          ))}
          {filtered.length === 0 && (
            <window.Empty title="Nothing here" sub="Try a different filter." icon={window.Icons.queue}/>
          )}
        </div>
      </div>

      {selectedJob && <JobDetailPanel job={selectedJob} onClose={() => setSelected(null)} />}
    </div>
  );
}

function FilterChip({ active, children, onClick }) {
  return (
    <button onClick={onClick} className={`btn sm ${active ? "primary" : ""}`}
            style={ active ? null : { background:"transparent", borderColor:"var(--border-1)" } }>
      {children}
    </button>
  );
}

function SummaryStat({ label, value, sub, mono, pillTone }) {
  return (
    <div className="card" style={{ minWidth: 180, padding: "14px 16px" }}>
      <div className="tag-key">{label}</div>
      <div className="row gap-2" style={{ marginTop: 6, alignItems: "baseline", whiteSpace: "nowrap" }}>
        <div className={mono ? "num" : ""}
             style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.02em", whiteSpace: "nowrap" }}>
          {value}
        </div>
        {pillTone && <span className={`pill ${pillTone}`} style={{fontSize:10}}><span className="dot"/>live</span>}
      </div>
      {sub && <div className="tiny muted" style={{ marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

// ----- JobCard variants -----
function JobCard({ job, variant, position, selected, onClick }) {
  if (variant === "rich")   return <JobCardRich    {...{job, position, selected, onClick}} />;
  if (variant === "strip")  return <JobCardStrip   {...{job, position, selected, onClick}} />;
  return                          <JobCardCompact {...{job, position, selected, onClick}} />;
}

// Variant 1: compact — default. Material color rail, position number, eligibility, time.
function JobCardCompact({ job, position, selected, onClick }) {
  const isActive = job.status === "printing";
  const parts = window.partsFromJob(job);
  return (
    <div className={`card ${selected ? "raised" : ""}`}
         onClick={onClick}
         style={{
           padding: "14px 16px 14px 20px",
           cursor: "pointer",
           borderColor: selected ? "var(--accent)" : undefined,
           boxShadow: selected ? "0 0 0 1px var(--accent), 0 8px 24px -8px var(--accent-glow)" : undefined,
           position: "relative",
           overflow: "hidden"
         }}>
      {/* material rail */}
      <div style={{
        position:"absolute", left:0, top:0, bottom:0, width:3,
        background: matColor(job.material),
      }} />
      <div className="row gap-4" style={{ alignItems: "center" }}>
        {/* position */}
        <div className="num" style={{
          width: 32, textAlign: "center", flexShrink: 0,
          fontSize: 13, fontWeight: 600,
          color: isActive ? "var(--accent-hi)" : "var(--text-3)"
        }}>
          {isActive ? <span className="live-dot" /> : `#${position}`}
        </div>
        {/* name + parts */}
        <div className="col" style={{ flex: "1 1 0", minWidth: 0, gap: 3 }}>
          <div className="row gap-2" style={{ alignItems: "baseline", minWidth: 0 }}>
            <div style={{
              fontSize: 14, fontWeight: 500, color: "var(--text-1)",
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              maxWidth: "100%",
            }}>{job.plateName}</div>
            <span className="mono tiny muted" style={{ flexShrink: 0 }}>{job.id}</span>
            {!job.sliced && <span className="elig" style={{color:"var(--warn)", borderColor:"rgba(245,158,11,0.25)", flexShrink: 0}}>unsliced</span>}
          </div>
          <div className="tiny muted" style={{
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            display: "block"
          }}>
            {parts.map(p => `${p.name} ×${p.qty}`).join("  ·  ")}
          </div>
        </div>
        {/* material */}
        <div style={{ width: 130, flexShrink: 0 }}>
          <window.MaterialChip material={job.material} color={matColor(job.material)} />
        </div>
        {/* eligibility */}
        <div style={{ width: 110, flexShrink: 0 }}>
          <window.EligibilityChips ids={job.eligiblePrinters} />
        </div>
        {/* time / progress */}
        <div className="col" style={{ width: 150, alignItems:"flex-end", flexShrink: 0 }}>
          {isActive ? (
            <>
              <div className="num small" style={{color:"var(--text-2)", whiteSpace:"nowrap"}}>
                {job.progress}% · <span style={{color:"var(--accent-hi)"}}>{window.fmtTime(job.estTime - job.elapsed)}</span> left
              </div>
              <div style={{ width: "100%", marginTop: 6 }}><window.Progress value={job.progress}/></div>
            </>
          ) : job.status === "complete" ? (
            <window.StatusPill status="complete" />
          ) : (
            <>
              <div className="small muted" style={{whiteSpace:"nowrap"}}>est. <span className="num" style={{color:"var(--text-2)"}}>{window.fmtTime(job.estTime)}</span></div>
              <div className="tiny muted" style={{ marginTop: 2 }}>slice on claim</div>
            </>
          )}
        </div>
        <button className="btn ghost icon sm" style={{flexShrink:0}} onClick={(e)=>{e.stopPropagation();}}>
          {window.Icons.more}
        </button>
      </div>
    </div>
  );
}

// Variant 2: rich — adds plate thumbnail + per-printer eligibility, larger card
function JobCardRich({ job, position, selected, onClick }) {
  const isActive = job.status === "printing";
  const parts = window.partsFromJob(job);
  return (
    <div className="card"
         onClick={onClick}
         style={{
           padding: 0,
           cursor: "pointer",
           overflow:"hidden",
           borderColor: selected ? "var(--accent)" : undefined,
           boxShadow: selected ? "0 0 0 1px var(--accent)" : undefined,
         }}>
      <div className="row gap-4" style={{ padding: 14 }}>
        {/* plate */}
        <div style={{
          width: 96, height: 96, flexShrink: 0,
          background: `linear-gradient(135deg, ${matColor(job.material)}, ${matColorDeep(job.material)})`,
          borderRadius: 8,
          border: "1px solid var(--border-1)",
          display: "grid", placeItems: "center",
          position:"relative",
          overflow:"hidden",
        }}>
          <div style={{
            width: "70%", height: "70%",
            border: "1px dashed rgba(255,255,255,0.18)",
            borderRadius: 4,
            background: "rgba(255,255,255,0.04)",
            display:"grid", placeItems:"center",
            color: "rgba(255,255,255,0.5)",
            fontFamily:"var(--font-mono)", fontSize:11
          }}>plate {position}</div>
        </div>
        {/* content */}
        <div className="col" style={{ flex: 1, minWidth: 0, gap: 8 }}>
          <div className="row between">
            <div>
              <div className="row gap-2" style={{ alignItems:"baseline" }}>
                <span className="mono tiny muted">{job.id}</span>
                <span className="num tiny" style={{color:"var(--text-3)"}}>#{position}</span>
                <div style={{ fontSize: 15, fontWeight: 500 }}>{job.plateName}</div>
              </div>
              <div className="tiny muted" style={{ marginTop: 4 }}>
                {parts.map(p => `${p.name} ×${p.qty}`).join("  ·  ")}
              </div>
            </div>
            <div className="row gap-2"><window.StatusPill status={job.status} /></div>
          </div>

          <div className="row gap-5" style={{ marginTop: 4 }}>
            <Kv k="Material" v={<window.MaterialChip material={job.material} color={matColor(job.material)} />} />
            <Kv k="Eligible" v={<window.EligibilityChips ids={job.eligiblePrinters} />} />
            <Kv k="Est. print" v={<span className="num">{window.fmtTime(job.estTime)}</span>} />
            {isActive
              ? <Kv k="Remaining" v={<span className="num" style={{color:"var(--accent-hi)"}}>{window.fmtTime(job.estTime - job.elapsed)}</span>} />
              : <Kv k="Slicing" v={job.sliced ? "ready" : "on claim"} />
            }
          </div>

          {isActive && (
            <div style={{ marginTop: 4 }}>
              <window.Progress value={job.progress} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Variant 3: strip — tight single-line table-ish row
function JobCardStrip({ job, position, selected, onClick }) {
  const isActive = job.status === "printing";
  return (
    <div onClick={onClick}
         className="row gap-4"
         style={{
           padding: "10px 14px",
           background: selected ? "var(--bg-3)" : "var(--bg-2)",
           borderLeft: `3px solid ${matColor(job.material)}`,
           borderBottom: "1px solid var(--border-1)",
           cursor: "pointer",
           alignItems: "center"
         }}>
      <div className="num" style={{ width: 28, color: "var(--text-3)", fontSize: 12 }}>#{position}</div>
      <div className="mono tiny muted" style={{ width: 56 }}>{job.id}</div>
      <div style={{ flex: 1, fontSize: 13.5, minWidth: 0 }}>{job.plateName}</div>
      <div style={{ width: 100 }}><window.MaterialChip material={job.material} color={matColor(job.material)} /></div>
      <div style={{ width: 110 }}><window.EligibilityChips ids={job.eligiblePrinters} /></div>
      <div className="num small muted" style={{ width: 70, textAlign:"right" }}>{window.fmtTime(job.estTime)}</div>
      <div style={{ width: 110, textAlign: "right" }}>
        {isActive ? <span className="num small" style={{color:"var(--accent-hi)"}}>{job.progress}%</span>
                  : <window.StatusPill status={job.status} />}
      </div>
    </div>
  );
}

function Kv({ k, v }) {
  return (
    <div className="col">
      <div className="tag-key">{k}</div>
      <div style={{ marginTop: 4 }}>{v}</div>
    </div>
  );
}

// ----- Detail panel (right-side drawer) -----
function JobDetailPanel({ job, onClose }) {
  const parts = window.partsFromJob(job);
  const eligPrinters = job.eligiblePrinters.map(window.getPrinter).filter(Boolean);
  const isActive = job.status === "printing";

  return (
    <div className="card" style={{ position: "sticky", top: 0, padding: 18, height: "fit-content" }}>
      <div className="row between" style={{ marginBottom: 10 }}>
        <div className="mono tiny muted">{job.id}</div>
        <button className="btn ghost icon sm" onClick={onClose}>{window.Icons.x}</button>
      </div>
      <div style={{ fontSize: 16, fontWeight: 500 }}>{job.plateName}</div>
      <div className="row gap-2" style={{ marginTop: 8 }}>
        <window.StatusPill status={job.status} />
        <window.MaterialChip material={job.material} color={matColor(job.material)} />
      </div>

      {isActive && (
        <div style={{ marginTop: 14 }}>
          <window.Progress value={job.progress} large />
          <div className="row between" style={{ marginTop: 6 }}>
            <span className="tiny muted">layer <span className="num" style={{color:"var(--text-2)"}}>{job.layer?.now}/{job.layer?.total}</span></span>
            <span className="tiny muted">{job.progress}% · <span className="num">{window.fmtTime(job.estTime - job.elapsed)}</span> left</span>
          </div>
        </div>
      )}

      <div className="divider" />

      <div className="tag-key">Parts on plate</div>
      <div className="col gap-2" style={{ marginTop: 8 }}>
        {parts.map((p, i) => (
          <div key={i} className="row between" style={{ padding: "6px 0" }}>
            <div className="col">
              <div className="small">{p.name}</div>
              <div className="tiny muted">{p.orderId} · {p.material}</div>
            </div>
            <div className="num small">×{p.qty}</div>
          </div>
        ))}
      </div>

      <div className="divider" />

      <div className="tag-key">Eligible printers</div>
      <div className="col gap-2" style={{ marginTop: 8 }}>
        {eligPrinters.map(p => (
          <div key={p.id} className="row between" style={{ padding: "8px 10px", background:"var(--bg-1)", borderRadius:8, border:"1px solid var(--border-1)" }}>
            <div className="col">
              <div className="small">{p.nickname} <span className="muted tiny">— {p.name}</span></div>
              <div className="tiny muted">profile to be selected at claim</div>
            </div>
            <window.StatusPill status={p.status} />
          </div>
        ))}
      </div>

      <div className="divider" />

      <div className="col gap-2">
        <button className="btn primary">{window.Icons.play} {isActive ? "Open printer" : "Claim & slice now"}</button>
        <div className="row gap-2">
          <button className="btn sm" style={{flex:1}}>{window.Icons.chevU} Bump priority</button>
          <button className="btn sm" style={{flex:1}}>{window.Icons.pause} Hold</button>
        </div>
        <button className="btn ghost sm" style={{color:"var(--err)"}}>{window.Icons.trash} Remove from queue</button>
      </div>
    </div>
  );
}

// material color lookup (rough)
function matColor(material) {
  const m = (material || "").toLowerCase();
  if (m.includes("pa-cf") || m.includes("pacf")) return "#1f2937";
  if (m.includes("petg")) return "#67e8f9";
  if (m.includes("abs"))  return "#a78bfa";
  if (m.includes("tpu"))  return "#f87171";
  if (m.includes("4-color")) return "#f472b6";
  if (m.includes("pla"))  return "#60a5fa";
  return "#8da2c0";
}
function matColorDeep(material) {
  const m = (material || "").toLowerCase();
  if (m.includes("pa-cf")) return "#0f172a";
  if (m.includes("petg")) return "#155e75";
  if (m.includes("abs"))  return "#3730a3";
  if (m.includes("tpu"))  return "#7f1d1d";
  if (m.includes("4-color")) return "#9d174d";
  if (m.includes("pla"))  return "#1e3a8a";
  return "#1c2a48";
}

Object.assign(window, { QueueScreen, matColor, matColorDeep, Kv });
