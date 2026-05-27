/* global React, window */
const { useState, useMemo } = React;

// =========================================================================
// Files screen, Printers manage screen, New order intake screen
// =========================================================================

// ----------- Files / model library -----------
// Folder tree built from `folder` paths on each file.
function buildFolderTree(files) {
  const root = { name: "All files", path: "", count: 0, children: {} };
  for (const f of files) {
    root.count++;
    const parts = (f.folder || "").split("/").filter(Boolean);
    let node = root;
    let path = "";
    for (const p of parts) {
      path += "/" + p;
      if (!node.children[p]) node.children[p] = { name: p, path, count: 0, children: {} };
      node = node.children[p];
      node.count++;
    }
  }
  return root;
}

function FolderTreeNode({ node, depth, openSet, toggle, current, setCurrent }) {
  const childKeys = Object.keys(node.children);
  const hasChildren = childKeys.length > 0;
  const isOpen = openSet.has(node.path) || depth === 0;
  const isActive = current === node.path;

  return (
    <div>
      <button
        onClick={() => { setCurrent(node.path); if (hasChildren) toggle(node.path); }}
        className="row gap-2"
        style={{
          width: "100%",
          padding: "6px 8px 6px " + (8 + depth * 14) + "px",
          background: isActive ? "var(--bg-3)" : "transparent",
          border: "none",
          borderRadius: 6,
          color: isActive ? "var(--text-1)" : "var(--text-2)",
          cursor: "pointer",
          textAlign: "left",
          fontSize: 13,
          alignItems: "center",
        }}>
        <span style={{
          width: 14, height: 14, display: "inline-flex",
          color: "var(--text-4)",
          transform: hasChildren && isOpen ? "rotate(90deg)" : "none",
          transition: "transform 120ms ease",
        }}>
          {hasChildren ? window.Icons.chevR : null}
        </span>
        <span style={{
          width: 14, height: 14, display: "inline-flex",
          color: isActive ? "var(--accent-hi)" : "var(--text-3)"
        }}>
          {depth === 0 ? window.Icons.files : (hasChildren ? <FolderIcon open={isOpen}/> : window.Icons.files)}
        </span>
        <span style={{ flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{node.name}</span>
        <span className="num tiny muted" style={{ flexShrink: 0 }}>{node.count}</span>
      </button>
      {hasChildren && isOpen && (
        <div>
          {childKeys.sort().map(k => (
            <FolderTreeNode key={k} node={node.children[k]} depth={depth + 1}
                            openSet={openSet} toggle={toggle}
                            current={current} setCurrent={setCurrent} />
          ))}
        </div>
      )}
    </div>
  );
}

function FolderIcon({ open }) {
  return (
    <svg className="ico" width="14" height="14" viewBox="0 0 24 24"
         fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      {open
        ? <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1H3V7zM3 10h19l-2 8a2 2 0 0 1-2 1H5a2 2 0 0 1-2-2v-7z"/>
        : <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>
      }
    </svg>
  );
}

function FilesScreen() {
  const [currentFolder, setCurrentFolder] = useState("");   // "" = all
  const [activeTags, setActiveTags] = useState([]);
  const [openFolders, setOpenFolders] = useState(new Set(["/Customers", "/Internal", "/Internal/R&D"]));
  const [sort, setSort] = useState("updated");
  const [folderExpanded, setFolderExpanded] = useState(true);
  const [filterExpanded, setFilterExpanded] = useState(true);

  const tree = useMemo(() => buildFolderTree(window.FILES), []);

  // Files in current folder (or subfolders)
  const inFolder = window.FILES.filter(f =>
    currentFolder === "" || f.folder === currentFolder || f.folder.startsWith(currentFolder + "/")
  );

  // Tag facets — only tags present in current-folder results
  const tagCounts = {};
  for (const f of inFolder) {
    for (const t of f.tags) tagCounts[t] = (tagCounts[t] || 0) + 1;
  }

  // Apply tag filter — file must include EVERY active tag (AND-filter, Amazon-style)
  const filtered = inFolder.filter(f =>
    activeTags.every(t => f.tags.includes(t))
  );

  // Sort
  const sorted = [...filtered].sort((a, b) => {
    if (sort === "name") return a.name.localeCompare(b.name);
    if (sort === "size") return parseFloat(b.size) - parseFloat(a.size);
    return 0; // updated — already roughly chronological in source
  });

  const toggleFolder = (path) => {
    const next = new Set(openFolders);
    if (next.has(path)) next.delete(path); else next.add(path);
    setOpenFolders(next);
  };

  const toggleTag = (tag) => {
    setActiveTags(activeTags.includes(tag)
      ? activeTags.filter(t => t !== tag)
      : [...activeTags, tag]);
  };

  const breadcrumb = currentFolder === "" ? "All files" : currentFolder.split("/").filter(Boolean).join(" / ");

  // Group tags by category for nicer display
  const TAG_GROUPS = [
    { label: "Material", tags: ["PLA","PETG","PA-CF","ABS","TPU"] },
    { label: "Purpose",  tags: ["structural","fixture","cosmetic","mechanism","damper","figurine","enclosure","display"] },
    { label: "Stage",    tags: ["prototype","production","reusable","archived","multi-color"] },
  ];

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: folderExpanded ? "240px 1fr" : "auto 1fr",
      gap: 18,
      alignItems: "flex-start",
      transition: "grid-template-columns 200ms ease",
    }}>
      {/* LEFT: folder card (collapsible) */}
      <FolderCard
        expanded={folderExpanded}
        onToggle={() => setFolderExpanded(!folderExpanded)}
        currentFolder={currentFolder}
        setCurrentFolder={setCurrentFolder}
        breadcrumb={breadcrumb}
        tree={tree}
        openFolders={openFolders}
        toggleFolder={toggleFolder}
      />

      {/* RIGHT: filters + grid */}
      <div className="col gap-4" style={{ minWidth: 0 }}>
        {/* Header — sort */}
        <div className="row between">
          <div className="col" style={{ minWidth: 0, flex: 1 }}>
            <span className="tag-key">{currentFolder === "" ? "Workshop" : "Folder"}</span>
            <div style={{ fontSize: 20, fontWeight: 600, letterSpacing: "-0.02em", marginTop: 2 }}>{breadcrumb}</div>
            <div className="tiny muted" style={{ marginTop: 2, whiteSpace: "nowrap" }}>
              {sorted.length} {sorted.length === 1 ? "file" : "files"}
              {activeTags.length > 0 && <span> matching {activeTags.length} tag{activeTags.length > 1 ? "s" : ""}</span>}
            </div>
          </div>
          <div className="row gap-2" style={{ flexShrink: 0 }}>
            <select className="select" style={{ width: "auto", paddingRight: 32 }}
                    value={sort} onChange={e => setSort(e.target.value)}>
              <option value="updated">Recently updated</option>
              <option value="name">Name (A–Z)</option>
              <option value="size">Largest first</option>
            </select>
          </div>
        </div>

        {/* Tag facets card (collapsible) */}
        <FilterCard
          expanded={filterExpanded}
          onToggle={() => setFilterExpanded(!filterExpanded)}
          activeTags={activeTags}
          setActiveTags={setActiveTags}
          toggleTag={toggleTag}
          tagCounts={tagCounts}
          tagGroups={TAG_GROUPS}
        />

        {/* File grid */}
        {sorted.length === 0 ? (
          <window.Empty title="No files match" sub="Try removing a tag or picking a different folder." icon={window.Icons.files} />
        ) : (
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
            gap: 12,
          }}>
            {sorted.map(f => (
              <div key={f.id} className="card" style={{ padding: 10, cursor: "pointer" }}>
                <div style={{
                  width: "100%", aspectRatio: "1/1",
                  background: `linear-gradient(135deg, ${f.thumbColor}, ${shade(f.thumbColor, -25)})`,
                  borderRadius: 6,
                  border: "1px solid var(--border-1)",
                  position: "relative",
                  overflow: "hidden",
                }}>
                  <div style={{
                    position: "absolute", inset: "20%",
                    border: "1px dashed rgba(255,255,255,0.18)",
                    borderRadius: 4,
                  }} />
                  <div style={{
                    position: "absolute", bottom: 6, right: 6,
                    fontFamily: "var(--font-mono)", fontSize: 10,
                    color: "rgba(255,255,255,0.6)",
                    background: "rgba(0,0,0,0.4)",
                    padding: "1px 5px", borderRadius: 3,
                  }}>{f.parts}p</div>
                </div>
                <div style={{
                  fontSize: 12, marginTop: 8, fontWeight: 500,
                  whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis"
                }}>
                  {f.name}
                </div>
                <div className="row between" style={{ marginTop: 3 }}>
                  <span className="tiny muted" style={{ whiteSpace: "nowrap" }}>{f.size}</span>
                  <span className="tiny muted" style={{ whiteSpace: "nowrap" }}>{f.updated} ago</span>
                </div>
                {/* Inline tag chips on file card */}
                <div className="row gap-2" style={{ flexWrap: "wrap", marginTop: 8 }}>
                  {f.tags.slice(0, 3).map(t => (
                    <span key={t} className="elig" style={{
                      fontSize: 9.5, padding: "1px 5px",
                      background: activeTags.includes(t) ? "rgba(59,130,246,0.12)" : "var(--bg-1)",
                      color: activeTags.includes(t) ? "var(--accent-hi)" : "var(--text-3)",
                    }}>{t}</span>
                  ))}
                  {f.tags.length > 3 && <span className="elig" style={{ fontSize: 9.5, padding: "1px 5px" }}>+{f.tags.length - 3}</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ----- Collapsible folder card -----
function FolderCard({ expanded, onToggle, currentFolder, setCurrentFolder, breadcrumb, tree, openFolders, toggleFolder }) {
  // Collapsed mode: thin rail with active folder name visible vertically/horizontally
  if (!expanded) {
    return (
      <div className="card" style={{
        padding: 6,
        position: "sticky",
        top: 0,
        width: 44,
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 8,
      }}
      onClick={onToggle}
      title={`Folders — current: ${breadcrumb}`}>
        <button className="btn ghost icon sm" onClick={(e)=>{e.stopPropagation(); onToggle();}}>
          {window.Icons.panel}
        </button>
        <div style={{
          writingMode: "vertical-rl",
          transform: "rotate(180deg)",
          padding: "8px 4px",
          color: "var(--text-2)",
          fontSize: 11,
          fontWeight: 500,
          letterSpacing: "0.05em",
          whiteSpace: "nowrap",
        }}>
          {breadcrumb}
        </div>
        <div className="tag-key" style={{
          writingMode: "vertical-rl",
          transform: "rotate(180deg)",
          color: "var(--text-4)",
          padding: "4px 0",
        }}>Folders</div>
      </div>
    );
  }

  return (
    <div className="card" style={{ padding: 10, position: "sticky", top: 0 }}>
      <div className="row between" style={{ padding: "4px 6px 8px", alignItems: "center" }}>
        <span className="tag-key">Folders</span>
        <div className="row gap-2">
          <button className="btn ghost icon sm" title="New folder">{window.Icons.plus}</button>
          <button className="btn ghost icon sm" title="Collapse" onClick={onToggle}>{window.Icons.chevL}</button>
        </div>
      </div>
      <FolderTreeNode node={tree} depth={0}
                      openSet={openFolders} toggle={toggleFolder}
                      current={currentFolder} setCurrent={setCurrentFolder} />
    </div>
  );
}

// ----- Collapsible filter card -----
function FilterCard({ expanded, onToggle, activeTags, setActiveTags, toggleTag, tagCounts, tagGroups }) {
  return (
    <div className="card" style={{ padding: expanded ? 14 : "10px 14px" }}>
      {/* Header — always visible. Active tags shown inline when collapsed. */}
      <div className="row between" style={{
        marginBottom: expanded ? 8 : 0,
        alignItems: "center",
        gap: 12,
        flexWrap: "wrap",
      }}>
        <div className="row gap-3" style={{ alignItems: "center", flex: 1, minWidth: 0, flexWrap: "wrap" }}>
          <span className="tag-key" style={{ flexShrink: 0 }}>Filter by tag</span>
          {/* Active tag pills always visible */}
          {activeTags.length > 0 ? (
            <div className="row gap-2" style={{ flexWrap: "wrap" }}>
              {activeTags.map(t => (
                <button key={t} onClick={() => toggleTag(t)}
                        className="row gap-2"
                        style={{
                          padding: "4px 10px",
                          background: "var(--accent)",
                          color: "#04101f",
                          border: "none",
                          borderRadius: 999,
                          fontSize: 12,
                          fontWeight: 600,
                          cursor: "pointer",
                          alignItems: "center",
                          whiteSpace: "nowrap",
                        }}>
                  {t}
                  <span style={{ width: 12, height: 12, display: "inline-flex" }}>{window.Icons.x}</span>
                </button>
              ))}
            </div>
          ) : (
            !expanded && <span className="tiny muted">No filters · click to add</span>
          )}
        </div>
        <div className="row gap-2" style={{ flexShrink: 0 }}>
          {activeTags.length > 0 && (
            <button className="btn ghost sm" onClick={() => setActiveTags([])}>
              Clear {activeTags.length}
            </button>
          )}
          <button className="btn ghost icon sm" title={expanded ? "Hide facets" : "Show facets"} onClick={onToggle}>
            <span style={{
              display: "inline-flex",
              transform: expanded ? "rotate(180deg)" : "none",
              transition: "transform 160ms ease",
            }}>{window.Icons.chevD}</span>
          </button>
        </div>
      </div>

      {/* Expanded facets */}
      {expanded && (
        <div className="col gap-3" style={{ marginTop: 4 }}>
          {tagGroups.map(group => {
            const groupTags = group.tags.filter(t => tagCounts[t] != null);
            if (groupTags.length === 0) return null;
            return (
              <div key={group.label}>
                <div className="tag-key" style={{ marginBottom: 6 }}>{group.label}</div>
                <div className="row gap-2" style={{ flexWrap: "wrap" }}>
                  {groupTags.map(t => {
                    const on = activeTags.includes(t);
                    const count = tagCounts[t];
                    return (
                      <button key={t} onClick={() => toggleTag(t)}
                              style={{
                                display: "inline-flex",
                                alignItems: "center",
                                gap: 6,
                                padding: "4px 10px",
                                borderRadius: 999,
                                border: `1px solid ${on ? "rgba(59,130,246,0.4)" : "var(--border-1)"}`,
                                background: on ? "rgba(59,130,246,0.12)" : "var(--bg-1)",
                                color: on ? "var(--accent-hi)" : "var(--text-2)",
                                fontSize: 12,
                                fontWeight: 500,
                                cursor: "pointer",
                                whiteSpace: "nowrap",
                              }}>
                        <span style={{
                          width: 12, height: 12, borderRadius: 3,
                          display: "inline-grid", placeItems: "center",
                          background: on ? "var(--accent)" : "transparent",
                          border: `1.5px solid ${on ? "var(--accent)" : "var(--border-2)"}`,
                          color: "#04101f", flexShrink: 0,
                        }}>
                          {on && <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="4"><path d="M20 6 9 17l-5-5"/></svg>}
                        </span>
                        {t}
                        <span className="num tiny" style={{ color: on ? "var(--accent-hi)" : "var(--text-4)", fontWeight: 500 }}>
                          {count}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function shade(hex, amt) {
  // simple hex shade
  const h = hex.replace("#","");
  const r = Math.max(0, Math.min(255, parseInt(h.slice(0,2),16) + amt));
  const g = Math.max(0, Math.min(255, parseInt(h.slice(2,4),16) + amt));
  const b = Math.max(0, Math.min(255, parseInt(h.slice(4,6),16) + amt));
  return `#${r.toString(16).padStart(2,"0")}${g.toString(16).padStart(2,"0")}${b.toString(16).padStart(2,"0")}`;
}

// ----------- Printers / add+manage -----------
function PrintersScreen() {
  const [adding, setAdding] = useState(false);

  if (adding) return <PrinterAddForm onCancel={() => setAdding(false)} />;

  return (
    <div className="col gap-4">
      <window.SectionHeader title="Printers"
                             sub="3 connected · 0 offline"
                             actions={<button className="btn primary sm" onClick={() => setAdding(true)}>{window.Icons.plus} Add printer</button>} />

      <div className="card" style={{ padding: 0, overflow:"hidden" }}>
        <table className="tbl">
          <thead>
            <tr>
              <th>Printer</th>
              <th>Model</th>
              <th>Build volume</th>
              <th>Capabilities</th>
              <th>Status</th>
              <th style={{textAlign:"right"}}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {window.PRINTERS.map(p => (
              <tr key={p.id}>
                <td>
                  <div className="row gap-3">
                    <div style={{
                      width: 36, height: 36, borderRadius: 8,
                      background: `linear-gradient(135deg, ${p.accent}33, transparent)`,
                      border:"1px solid var(--border-1)",
                      display:"grid", placeItems:"center"
                    }}>
                      <span className="mono tiny" style={{ color: p.accent }}>{p.badge}</span>
                    </div>
                    <div className="col">
                      <div style={{ fontWeight: 500 }}>{p.nickname}</div>
                      <div className="mono tiny muted">{p.id}</div>
                    </div>
                  </div>
                </td>
                <td><div className="small">{p.name}</div></td>
                <td className="num small">{p.buildVolume} mm</td>
                <td>
                  <div className="row gap-2" style={{ flexWrap:"wrap" }}>
                    {p.capabilities.slice(0,4).map(c => <span key={c} className="elig on">{c}</span>)}
                    {p.capabilities.length > 4 && <span className="elig">+{p.capabilities.length-4}</span>}
                  </div>
                </td>
                <td><window.StatusPill status={p.status} /></td>
                <td style={{ textAlign:"right" }}>
                  <div className="row gap-2" style={{ justifyContent:"flex-end" }}>
                    <button className="btn ghost sm" onClick={e => e.stopPropagation()}>Edit</button>
                    <button className="btn icon ghost sm">{window.Icons.more}</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PrinterAddForm({ onCancel }) {
  const [step, setStep] = useState(1);
  const [data, setData] = useState({
    model: "Bambu Lab P1S",
    nickname: "",
    connection: "lan",
    ip: "192.168.1.",
    capabilities: ["PLA","PETG"],
  });

  const models = [
    { name: "Bambu Lab P1S",          vol: "256×256×256", chamber: false, mats: ["PLA","PETG","PLA-CF","ABS","ASA","TPU"] },
    { name: "Bambu Lab X1 Carbon",    vol: "256×256×256", chamber: true,  mats: ["PLA","PETG","PA-CF","PC","ABS","ASA"] },
    { name: "Elegoo Centauri Carbon", vol: "256×256×256", chamber: true,  mats: ["PLA","PETG","PA-CF","PC","ABS"] },
    { name: "Snapmaker U1",           vol: "200×200×200", chamber: false, mats: ["PLA","PETG","TPU","Multi-color"] },
    { name: "Prusa MK4",              vol: "250×210×220", chamber: false, mats: ["PLA","PETG","ASA","ABS"] },
    { name: "Custom",                 vol: "—",           chamber: false, mats: [] },
  ];

  return (
    <div className="col gap-4">
      <div className="row gap-2">
        <button className="btn ghost sm" onClick={onCancel}>{window.Icons.chevL} Printers</button>
        <span className="muted small">/</span>
        <span className="small">Add printer</span>
      </div>

      <div style={{ maxWidth: 760 }}>
        {/* steps */}
        <div className="row gap-3" style={{ marginBottom: 24 }}>
          {[
            { n: 1, label: "Model" },
            { n: 2, label: "Connect" },
            { n: 3, label: "Capabilities" },
          ].map(s => (
            <div key={s.n} className="row gap-2" style={{ alignItems:"center" }}>
              <div style={{
                width: 24, height: 24, borderRadius: 12,
                display:"grid", placeItems:"center",
                fontSize: 11, fontWeight: 600,
                background: step >= s.n ? "var(--accent)" : "var(--bg-3)",
                color: step >= s.n ? "#04101f" : "var(--text-3)",
                border: step === s.n ? "2px solid var(--accent-hi)" : "1px solid var(--border-1)",
              }}>
                {step > s.n ? "✓" : s.n}
              </div>
              <span className="small" style={{ color: step >= s.n ? "var(--text-1)" : "var(--text-3)" }}>{s.label}</span>
              {s.n < 3 && <div style={{ width: 40, height: 1, background:"var(--border-1)", marginLeft: 8 }} />}
            </div>
          ))}
        </div>

        {step === 1 && (
          <div className="card" style={{ padding: 24 }}>
            <window.SectionHeader title="Pick a printer model" sub="We'll suggest sensible defaults." />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 10 }}>
              {models.map(m => {
                const active = data.model === m.name;
                return (
                  <button key={m.name} onClick={() => setData({...data, model: m.name})}
                          className="card"
                          style={{
                            textAlign:"left", padding: 14, cursor:"pointer",
                            background: active ? "var(--bg-3)" : "var(--bg-1)",
                            borderColor: active ? "var(--accent)" : "var(--border-1)",
                          }}>
                    <div className="row between">
                      <div style={{ fontWeight: 500 }}>{m.name}</div>
                      {active && <div style={{color:"var(--accent-hi)"}}>{window.Icons.check}</div>}
                    </div>
                    <div className="tiny muted" style={{ marginTop: 4 }}>{m.vol} mm · {m.chamber ? "enclosed" : "open"}</div>
                  </button>
                );
              })}
            </div>
            <div style={{ marginTop: 20 }}>
              <label className="label">Nickname</label>
              <input className="input" value={data.nickname} placeholder="e.g. Atlas, Forge, Iris"
                     onChange={e => setData({...data, nickname: e.target.value})} />
              <div className="tiny muted" style={{marginTop:6}}>Shown in queue and on tiles. Real model name stays in metadata.</div>
            </div>
            <div className="row gap-2" style={{ marginTop: 24, justifyContent:"flex-end" }}>
              <button className="btn ghost" onClick={onCancel}>Cancel</button>
              <button className="btn primary" onClick={() => setStep(2)}>Next {window.Icons.chevR}</button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="card" style={{ padding: 24 }}>
            <window.SectionHeader title="How should we talk to it?" />
            <div className="col gap-3">
              {[
                { id: "lan",    label: "LAN mode (manufacturer firmware)",     sub: "Stream camera + control via local API." },
                { id: "cloud",  label: "Cloud account",                         sub: "Sign in to vendor's cloud; OK for remote." },
                { id: "octo",   label: "OctoPrint / Klipper / Moonraker",       sub: "For custom firmware setups." },
              ].map(opt => {
                const active = data.connection === opt.id;
                return (
                  <button key={opt.id} onClick={() => setData({...data, connection: opt.id})}
                          className="card"
                          style={{ textAlign:"left", padding: 14, cursor:"pointer",
                                   background: active ? "var(--bg-3)" : "var(--bg-1)",
                                   borderColor: active ? "var(--accent)" : "var(--border-1)" }}>
                    <div className="row between">
                      <div>
                        <div style={{ fontWeight: 500 }}>{opt.label}</div>
                        <div className="tiny muted" style={{ marginTop: 2 }}>{opt.sub}</div>
                      </div>
                      <div style={{
                        width: 16, height: 16, borderRadius: 8,
                        border: `2px solid ${active ? "var(--accent)" : "var(--border-2)"}`,
                        background: active ? "var(--accent)" : "transparent",
                        boxShadow: active ? "inset 0 0 0 3px var(--bg-3)" : "none"
                      }}/>
                    </div>
                  </button>
                );
              })}
            </div>

            {data.connection === "lan" && (
              <div style={{ marginTop: 20 }}>
                <label className="label">IP address</label>
                <input className="input mono" value={data.ip} onChange={e => setData({...data, ip: e.target.value})} />
                <label className="label" style={{ marginTop: 12 }}>Access code</label>
                <input className="input mono" placeholder="••••••••" />
              </div>
            )}

            <div className="row gap-2" style={{ marginTop: 24, justifyContent:"flex-end" }}>
              <button className="btn" onClick={() => setStep(1)}>{window.Icons.chevL} Back</button>
              <button className="btn primary" onClick={() => setStep(3)}>Test connection</button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="card" style={{ padding: 24 }}>
            <window.SectionHeader title="Queue eligibility"
                                   sub="Which materials this printer can claim from the queue." />
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(3, 1fr)",
              gap: 8,
              marginTop: 12,
            }}>
              {["PLA","PETG","PLA-CF","ABS","ASA","PA-CF","PC","TPU","Multi-color","Soluble support"].map(m => {
                const on = data.capabilities.includes(m);
                return (
                  <button key={m} onClick={() => {
                    const next = on ? data.capabilities.filter(x=>x!==m) : [...data.capabilities, m];
                    setData({...data, capabilities: next});
                  }}
                  className="row gap-2"
                  style={{
                    padding: "10px 12px", borderRadius: 8,
                    background: on ? "rgba(59,130,246,0.12)" : "var(--bg-1)",
                    border: `1px solid ${on ? "rgba(59,130,246,0.4)" : "var(--border-1)"}`,
                    color: on ? "var(--accent-hi)" : "var(--text-2)",
                    cursor:"pointer", textAlign:"left"
                  }}>
                    <div style={{
                      width: 14, height: 14, borderRadius: 3,
                      background: on ? "var(--accent)" : "transparent",
                      border: `1.5px solid ${on ? "var(--accent)" : "var(--border-2)"}`,
                      display:"grid", placeItems:"center", color: "#04101f"
                    }}>{on && <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><path d="M20 6 9 17l-5-5"/></svg>}</div>
                    <span className="small">{m}</span>
                  </button>
                );
              })}
            </div>

            <div className="row gap-2" style={{ marginTop: 24, justifyContent:"flex-end" }}>
              <button className="btn" onClick={() => setStep(2)}>{window.Icons.chevL} Back</button>
              <button className="btn primary">{window.Icons.check} Finish</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { FilesScreen, PrintersScreen });
