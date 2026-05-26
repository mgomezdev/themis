/* global React, window */
const { useState, useMemo, useRef, useEffect } = React;

// =========================================================================
// New job — drop a .3mf or .stl; create one job per selected plate.
// Each plate-job carries its own printer eligibility, process preset,
// filament profile, fulfilled orders, and priority.
// =========================================================================

// ---------- mocked plate parser ----------
// Real 3MF parsing requires unzipping. For the prototype we synthesize
// plausible plate metadata based on filename. STL files always = 1 plate.
function readPlatesFromFilename(fileName) {
  const lower = fileName.toLowerCase();
  if (lower.endsWith(".stl")) {
    return [{
      id: "plate-1", index: 1, name: "Single body",
      parts: [{ name: fileName.replace(/\.stl$/i, ""), qty: 1, material: "—" }],
      estTime: 64, materials: ["—"], thumbColor: "#2a3552",
    }];
  }

  if (lower.includes("vr_arm") || lower.includes("arm_bracket")) {
    return [
      { id: "plate-1", index: 1, name: "Arm bracket — L (×2)",
        parts: [{ name: "Arm bracket — L", qty: 2, material: "PA-CF" }],
        estTime: 156, materials: ["PA-CF"], thumbColor: "#16203a",
        suggestedOrders: ["ORD-2241"] },
      { id: "plate-2", index: 2, name: "Arm bracket — R (×2)",
        parts: [{ name: "Arm bracket — R", qty: 2, material: "PA-CF" }],
        estTime: 156, materials: ["PA-CF"], thumbColor: "#16203a",
        suggestedOrders: ["ORD-2241"] },
      { id: "plate-3", index: 3, name: "Cable clamps (×8)",
        parts: [{ name: "Cable clamp", qty: 8, material: "PETG" }],
        estTime: 96, materials: ["PETG"], thumbColor: "#1e3a5a",
        suggestedOrders: ["ORD-2241"] },
    ];
  }
  if (lower.includes("hartwell") || lower.includes("fig")) {
    return [
      { id: "plate-1", index: 1, name: "Figure A — multi-color (×4)",
        parts: [{ name: "Figure A", qty: 4, material: "PLA (4-color)" }],
        estTime: 152, materials: ["PLA (4-color)"], thumbColor: "#3a2a3a",
        suggestedOrders: ["ORD-2243"] },
      { id: "plate-2", index: 2, name: "Figure A — accents (×4)",
        parts: [{ name: "Figure A accents", qty: 4, material: "PLA Silk" }],
        estTime: 88, materials: ["PLA (4-color)"], thumbColor: "#3a3a2a",
        suggestedOrders: ["ORD-2243"] },
    ];
  }
  if (lower.includes("cradle") || lower.includes("northbeam")) {
    return [
      { id: "plate-1", index: 1, name: "Cradle body (×2)",
        parts: [{ name: "Cradle body", qty: 2, material: "PETG" }],
        estTime: 128, materials: ["PETG"], thumbColor: "#1e3a4a",
        suggestedOrders: ["ORD-2244"] },
      { id: "plate-2", index: 2, name: "Foot dampener (×4)",
        parts: [{ name: "Foot dampener", qty: 4, material: "TPU" }],
        estTime: 72, materials: ["TPU"], thumbColor: "#4a1a1a",
        suggestedOrders: ["ORD-2244"] },
    ];
  }
  if (lower.includes("reflow") || lower.includes("wall_panel")) {
    return [
      { id: "plate-1", index: 1, name: "Wall panel (×1)",
        parts: [{ name: "Wall panel", qty: 1, material: "ABS" }],
        estTime: 192, materials: ["ABS"], thumbColor: "#222b41",
        suggestedOrders: ["ORD-2242"] },
    ];
  }
  return [
    { id: "plate-1", index: 1, name: "Plate 1",
      parts: [{ name: "Main body", qty: 1, material: "PLA" }],
      estTime: 84, materials: ["PLA"], thumbColor: "#2a3552" },
    { id: "plate-2", index: 2, name: "Plate 2",
      parts: [{ name: "Lid", qty: 1, material: "PLA" }],
      estTime: 52, materials: ["PLA"], thumbColor: "#2a3552" },
  ];
}

// ---------- material → printer compatibility ----------
function eligibleForMaterial(materials) {
  const set = new Set();
  for (const m of materials) {
    const lower = (m || "").toLowerCase();
    if (lower.includes("pa-cf") || lower.includes("pc ") || lower === "pc" ||
        lower.includes("abs") || lower.includes("asa")) {
      set.add("ecc-01"); continue;
    }
    if (lower.includes("4-color") || lower.includes("multi")) {
      set.add("snp-01"); continue;
    }
    if (lower.includes("tpu")) {
      ["p1s-01", "snp-01"].forEach(id => set.add(id)); continue;
    }
    ["p1s-01", "ecc-01", "snp-01"].forEach(id => set.add(id));
  }
  return Array.from(set);
}

// ---------- build default config for one plate ----------
function defaultConfigForPlate(plate) {
  return {
    selected: true,
    jobName: plate.name,
    priority: "normal",
    orderIds: plate.suggestedOrders ? [...plate.suggestedOrders] : [],
    selectedPrinters: [],
    perPrinter: {},
  };
}

// =========================================================================

function NewJobScreen({ onCancel, onCreate }) {
  const [file, setFile] = useState(null);
  const [plates, setPlates] = useState([]);
  const [plateConfigs, setPlateConfigs] = useState({});   // { [plateId]: config }
  const [activePlateId, setActivePlateId] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef(null);

  const selectedPlateIds = useMemo(
    () => plates.filter(p => plateConfigs[p.id]?.selected).map(p => p.id),
    [plates, plateConfigs]
  );

  // Pick the first plate as the active one when the file loads;
  // don't auto-jump away when the user toggles a plate's "Queue" off.
  useEffect(() => {
    if (plates.length > 0 && !plates.find(p => p.id === activePlateId)) {
      setActivePlateId(plates[0].id);
    }
  }, [plates, activePlateId]);

  // ------------- file actions -------------
  function handleFile(rawFile) {
    if (!rawFile) return;
    const f = {
      name: rawFile.name,
      size: rawFile.size,
      type: rawFile.name.toLowerCase().endsWith(".stl") ? "stl" : "3mf",
    };
    setFile(f);
    const detected = readPlatesFromFilename(f.name);
    setPlates(detected);
    const configs = {};
    detected.forEach(p => { configs[p.id] = defaultConfigForPlate(p); });
    setPlateConfigs(configs);
    setActivePlateId(detected[0]?.id || null);
  }
  function handleDrop(e) {
    e.preventDefault(); e.stopPropagation(); setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  }
  function clearFile() {
    setFile(null); setPlates([]); setPlateConfigs({}); setActivePlateId(null);
  }

  // ------------- plate config mutators -------------
  function setPlateConfig(plateId, patch) {
    setPlateConfigs(prev => ({ ...prev, [plateId]: { ...prev[plateId], ...patch } }));
  }
  function togglePlate(plateId, value) {
    setPlateConfig(plateId, { selected: value });
  }
  function togglePrinterForPlate(plateId, printerId) {
    setPlateConfigs(prev => {
      const cfg = prev[plateId];
      if (!cfg) return prev;
      const plate = plates.find(p => p.id === plateId);
      const inSelection = cfg.selectedPrinters.includes(printerId);
      if (inSelection) {
        const nextPP = { ...cfg.perPrinter };
        delete nextPP[printerId];
        return {
          ...prev,
          [plateId]: {
            ...cfg,
            selectedPrinters: cfg.selectedPrinters.filter(id => id !== printerId),
            perPrinter: nextPP,
          },
        };
      } else {
        const firstProcess = window.PROCESS_PRESETS.find(p => p.printerId === printerId);
        const firstFil = pickFilamentForPrinter(printerId, plate?.materials || []);
        return {
          ...prev,
          [plateId]: {
            ...cfg,
            selectedPrinters: [...cfg.selectedPrinters, printerId],
            perPrinter: {
              ...cfg.perPrinter,
              [printerId]: {
                processId: firstProcess?.id || null,
                filamentId: firstFil?.id || null,
                profileIdx: firstFil?.profileIdx ?? 0,
              },
            },
          },
        };
      }
    });
  }
  function setPerPrinter(plateId, printerId, patch) {
    setPlateConfigs(prev => ({
      ...prev,
      [plateId]: {
        ...prev[plateId],
        perPrinter: {
          ...prev[plateId].perPrinter,
          [printerId]: { ...prev[plateId].perPrinter[printerId], ...patch },
        },
      },
    }));
  }
  function setOrdersForPlate(plateId, orderIds) {
    setPlateConfig(plateId, { orderIds });
  }

  // ------------- validation -------------
  const plateIsComplete = (plateId) => {
    const cfg = plateConfigs[plateId];
    if (!cfg || !cfg.selected) return false;
    if (!cfg.jobName?.trim()) return false;
    if (cfg.selectedPrinters.length === 0) return false;
    return cfg.selectedPrinters.every(printerId => {
      const pp = cfg.perPrinter[printerId];
      return pp && pp.processId && pp.filamentId;
    });
  };
  const isComplete = selectedPlateIds.length > 0 && selectedPlateIds.every(plateIsComplete);

  return (
    <div className="col gap-4">
      <div className="row gap-2">
        <button className="btn ghost sm" onClick={onCancel}>{window.Icons.chevL} Queue</button>
        <span className="muted small">/</span>
        <span className="small">New job</span>
      </div>

      <div className="screen-grid" style={{ gridTemplateColumns: "minmax(0, 1fr) 340px", gap: 18 }}>
        <div className="col gap-4">

          {/* ---- Step 1: source file ---- */}
          <div className="card" style={{ padding: 20 }}>
            <window.SectionHeader
              title={<span><StepNum n={1} done={!!file} /> Source file</span>}
              sub="Drop a .3mf with one or more plates, or a single .stl body."
            />
            {!file ? (
              <Dropzone
                dragOver={dragOver}
                onDragEnter={() => setDragOver(true)}
                onDragLeave={() => setDragOver(false)}
                onDragOver={e => { e.preventDefault(); setDragOver(true); }}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
              />
            ) : (
              <FileCard
                file={file}
                plateCount={plates.length}
                selectedCount={selectedPlateIds.length}
                onClear={clearFile}
              />
            )}
            <input ref={fileInputRef} type="file" accept=".3mf,.stl"
                   style={{ display: "none" }}
                   onChange={e => handleFile(e.target.files?.[0])} />
          </div>

          {/* ---- Step 2: per-plate config (with queue toggle per plate) ---- */}
          {plates.length > 0 && activePlateId && (
            <div className="card" style={{ padding: 0, overflow: "hidden" }}>
              {/* tabs (multi-plate only) */}
              {plates.length > 1 && (
                <div style={{
                    display: "flex",
                    borderBottom: "1px solid var(--border-1)",
                    overflow: "auto",
                  }}>
                  {plates.map(plate => {
                    const cfg = plateConfigs[plate.id];
                    const isActive = activePlateId === plate.id;
                    const queued = !!cfg?.selected;
                    const complete = plateIsComplete(plate.id);
                    return (
                      <button
                        key={plate.id}
                        onClick={() => setActivePlateId(plate.id)}
                        style={{
                          padding: "12px 16px",
                          background: isActive ? "var(--bg-2)" : "transparent",
                          border: "none",
                          borderBottom: `2px solid ${isActive ? "var(--accent)" : "transparent"}`,
                          color: isActive ? "var(--text-1)" : queued ? "var(--text-2)" : "var(--text-4)",
                          fontFamily: "inherit",
                          fontSize: 13,
                          fontWeight: isActive ? 500 : 400,
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          whiteSpace: "nowrap",
                          flexShrink: 0,
                        }}>
                        <TabDot queued={queued} complete={complete} />
                        Plate {plate.index} · {cfg.jobName || plate.name}
                        {!queued && (
                          <span className="mono tiny" style={{
                              padding: "1px 6px", marginLeft: 4,
                              border: "1px solid var(--border-1)",
                              borderRadius: 4, color: "var(--text-4)",
                              fontSize: 9.5, letterSpacing: "0.04em",
                            }}>
                            SKIP
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}

              {/* the active plate's config */}
              <div style={{ padding: 20 }}>
                <PlateConfigPanel
                  plate={plates.find(p => p.id === activePlateId)}
                  config={plateConfigs[activePlateId]}
                  isMultiPlate={plates.length > 1}
                  onSetField={(patch) => setPlateConfig(activePlateId, patch)}
                  onTogglePrinter={(printerId) => togglePrinterForPlate(activePlateId, printerId)}
                  onSetPerPrinter={(printerId, patch) => setPerPrinter(activePlateId, printerId, patch)}
                  onSetOrders={(orderIds) => setOrdersForPlate(activePlateId, orderIds)}
                  onToggleQueued={(value) => togglePlate(activePlateId, value)}
                />
              </div>
            </div>
          )}
        </div>

        {/* ---- Right rail: summary ---- */}
        <div className="col gap-4">
          <SummaryCard
            file={file}
            plates={plates}
            plateConfigs={plateConfigs}
            selectedPlateIds={selectedPlateIds}
            activePlateId={activePlateId}
            plateIsComplete={plateIsComplete}
          />
          <div className="card" style={{ padding: 18 }}>
            <button className="btn primary" style={{ width: "100%" }}
                    disabled={!isComplete}
                    onClick={() => onCreate && onCreate({
                      file,
                      jobs: selectedPlateIds.map(id => ({
                        plate: plates.find(p => p.id === id),
                        config: plateConfigs[id],
                      })),
                    })}>
              {window.Icons.check} Add {selectedPlateIds.length || ""} job{selectedPlateIds.length === 1 ? "" : "s"} to queue
            </button>
            <button className="btn ghost sm" style={{ width: "100%", marginTop: 8 }} onClick={onCancel}>Cancel</button>
            <div className="tiny muted" style={{ marginTop: 10, textAlign: "center", lineHeight: 1.5 }}>
              Each plate becomes its own job.<br/>Slicing runs when a printer claims.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ========================================================================
// Step 1: dropzone + filecard
// ========================================================================

function StepNum({ n, done }) {
  return (
    <span style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        width: 20, height: 20,
        background: done ? "var(--accent)" : "var(--bg-3)",
        color: done ? "white" : "var(--text-3)",
        border: `1px solid ${done ? "var(--accent)" : "var(--border-1)"}`,
        borderRadius: 6,
        fontSize: 11, fontWeight: 600, marginRight: 10,
        fontFamily: "var(--font-mono)",
        verticalAlign: "middle",
      }}>
      {done ? React.cloneElement(window.Icons.check, { size: 12 }) : n}
    </span>
  );
}

function Dropzone({ dragOver, onDragEnter, onDragLeave, onDragOver, onDrop, onClick }) {
  return (
    <div
      onClick={onClick}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDrop}
      style={{
        border: `1.5px dashed ${dragOver ? "var(--accent)" : "var(--border-2)"}`,
        background: dragOver ? "var(--accent-glow)" : "var(--bg-1)",
        borderRadius: 12,
        padding: "32px 24px",
        textAlign: "center",
        cursor: "pointer",
        transition: "background 120ms, border-color 120ms",
      }}>
      <div style={{
          width: 48, height: 48, borderRadius: 12,
          background: "var(--bg-3)", color: dragOver ? "var(--accent-hi)" : "var(--text-3)",
          display: "grid", placeItems: "center", margin: "0 auto 12px",
        }}>
        {React.cloneElement(window.Icons.upload, { size: 22 })}
      </div>
      <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text-1)" }}>
        Drop a .3mf or .stl file
      </div>
      <div className="small muted" style={{ marginTop: 4 }}>
        Or click to browse · multi-plate 3MFs supported
      </div>
    </div>
  );
}

function FileCard({ file, plateCount, selectedCount, onClear }) {
  return (
    <div className="row gap-3" style={{
        padding: "14px 16px", background: "var(--bg-1)",
        border: "1px solid var(--border-1)", borderRadius: 10,
      }}>
      <div style={{
          width: 44, height: 44, borderRadius: 8,
          background: file.type === "3mf"
            ? "linear-gradient(135deg, #1e3a8a, #3b82f6)"
            : "linear-gradient(135deg, #475569, #94a3b8)",
          color: "white",
          display: "grid", placeItems: "center",
          fontWeight: 700, fontSize: 10.5,
          fontFamily: "var(--font-mono)", letterSpacing: "0.04em",
          border: "1px solid var(--border-2)",
        }}>
        {file.type.toUpperCase()}
      </div>
      <div className="col" style={{ flex: 1, minWidth: 0 }}>
        <div className="small" style={{
            fontWeight: 500, color: "var(--text-1)",
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>
          {file.name}
        </div>
        <div className="tiny muted" style={{ marginTop: 2 }}>
          {plateCount === 1
            ? "Single plate"
            : `${plateCount} plates · ${selectedCount} selected → ${selectedCount} job${selectedCount === 1 ? "" : "s"}`}
          {file.size ? ` · ${formatBytes(file.size)}` : ""}
        </div>
      </div>
      <button className="btn ghost sm" onClick={onClear}>{window.Icons.x} Replace</button>
    </div>
  );
}

function formatBytes(b) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b/1024).toFixed(1)} KB`;
  return `${(b/1024/1024).toFixed(1)} MB`;
}

// ========================================================================
// Step 2: plate tile (checkbox-style)
// ========================================================================

function PlateTile({ plate, selected, complete, isActive, onToggle, onActivate }) {
  return (
    <div
      onClick={onActivate}
      style={{
        background: selected ? "var(--bg-3)" : "var(--bg-1)",
        border: `1px solid ${isActive ? "var(--accent)" : selected ? "var(--border-2)" : "var(--border-1)"}`,
        boxShadow: isActive ? "0 0 0 1px var(--accent), 0 8px 24px -8px var(--accent-glow)" : "none",
        borderRadius: 10, padding: 12, cursor: "pointer",
        opacity: selected ? 1 : 0.55,
        transition: "background 120ms, opacity 120ms, border-color 120ms",
      }}>
      {/* preview */}
      <div style={{
          width: "100%", aspectRatio: "1 / 0.9", borderRadius: 8,
          background: `linear-gradient(135deg, ${plate.thumbColor}, ${darken(plate.thumbColor)})`,
          border: "1px solid var(--border-2)",
          display: "grid", placeItems: "center",
          position: "relative", overflow: "hidden",
          marginBottom: 10,
        }}>
        <div className="num" style={{
            color: "rgba(255,255,255,0.55)", fontSize: 12,
            border: "1px dashed rgba(255,255,255,0.18)",
            padding: "16px 22px", borderRadius: 4,
            background: "rgba(255,255,255,0.04)",
          }}>
          plate {plate.index}
        </div>
        <div style={{
            position: "absolute", top: 8, left: 8,
            fontFamily: "var(--font-mono)", fontSize: 10,
            color: "rgba(255,255,255,0.5)",
          }}>
          {plate.parts.reduce((a,b)=>a+b.qty,0)} part{plate.parts.reduce((a,b)=>a+b.qty,0) === 1 ? "" : "s"}
        </div>
        {/* status badge */}
        {selected && complete && (
          <div style={{
              position: "absolute", top: 8, right: 8,
              padding: "2px 8px", borderRadius: 999,
              background: "rgba(34,197,94,0.18)", color: "var(--ok)",
              fontSize: 10, fontFamily: "var(--font-mono)", letterSpacing: "0.04em",
              border: "1px solid rgba(34,197,94,0.3)",
            }}>
            READY
          </div>
        )}
      </div>
      <div className="row between" style={{ alignItems: "center" }}>
        <div className="row gap-2" style={{ alignItems: "center", minWidth: 0, flex: 1 }}>
          {/* big clickable checkbox */}
          <button
            onClick={e => { e.stopPropagation(); onToggle(!selected); }}
            style={{
              background: "transparent", border: "none", cursor: "pointer",
              padding: 0, display: "inline-flex", flexShrink: 0,
            }}>
            <Checkbox checked={selected} />
          </button>
          <div className="col" style={{ minWidth: 0, flex: 1 }}>
            <div className="small" style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {plate.name}
            </div>
            <div className="tiny muted" style={{ marginTop: 2 }}>
              {plate.materials.join(" · ")}
            </div>
          </div>
        </div>
        <div className="num tiny muted" style={{ flexShrink: 0, marginLeft: 8 }}>
          {window.fmtTime(plate.estTime)}
        </div>
      </div>
    </div>
  );
}

function darken(hex) {
  const v = parseInt(hex.replace("#",""), 16);
  let r = (v >> 16) & 0xff, g = (v >> 8) & 0xff, b = v & 0xff;
  r = Math.max(0, Math.round(r * 0.55));
  g = Math.max(0, Math.round(g * 0.55));
  b = Math.max(0, Math.round(b * 0.55));
  return `#${[r,g,b].map(n=>n.toString(16).padStart(2,"0")).join("")}`;
}

// ========================================================================
// Step 3: per-plate config panel
// ========================================================================

function PlateConfigPanel({ plate, config, isMultiPlate, onSetField, onTogglePrinter, onSetPerPrinter, onSetOrders, onToggleQueued }) {
  const eligibleIds = useMemo(() => eligibleForMaterial(plate.materials), [plate.materials]);
  const queued = !!config.selected;

  return (
    <div className="col gap-4">
      {/* Plate banner with queue toggle */}
      <div className="row gap-3" style={{ alignItems: "center" }}>
        <div style={{
            width: 56, height: 50, borderRadius: 8, flexShrink: 0,
            background: `linear-gradient(135deg, ${plate.thumbColor}, ${darken(plate.thumbColor)})`,
            border: "1px solid var(--border-2)",
            display: "grid", placeItems: "center",
            color: "rgba(255,255,255,0.55)", fontSize: 10,
            fontFamily: "var(--font-mono)",
            opacity: queued ? 1 : 0.55,
          }}>
          P{plate.index}
        </div>
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 500, color: queued ? "var(--text-1)" : "var(--text-3)" }}>
            {plate.name}
          </div>
          <div className="tiny muted" style={{ marginTop: 2 }}>
            {plate.materials.join(" · ")} · est. {window.fmtTime(plate.estTime)} · {plate.parts.reduce((a,b)=>a+b.qty,0)} part{plate.parts.reduce((a,b)=>a+b.qty,0) === 1 ? "" : "s"}
          </div>
        </div>
        {isMultiPlate && (
          <QueueToggle
            checked={queued}
            onChange={onToggleQueued}
          />
        )}
      </div>

      {/* Body: dims when plate is skipped */}
      <div style={{
          opacity: queued ? 1 : 0.5,
          pointerEvents: queued ? "auto" : "none",
          transition: "opacity 120ms",
        }}
        aria-disabled={!queued}>
        <div className="col gap-4">
          {/* Eligible printers */}
          <div>
            <div className="row between" style={{ marginBottom: 10, alignItems: "baseline" }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text-1)" }}>
                  <StepNum n={2} done={config.selectedPrinters.length > 0} />
                  Eligible printers
                </div>
                <div className="tiny muted" style={{ marginTop: 2, marginLeft: 30 }}>
                  Choose which printers may claim this plate. Configure preset + filament for each.
                </div>
              </div>
            </div>
            <PrinterPicker
              eligibleIds={eligibleIds}
              selectedPrinters={config.selectedPrinters}
              onToggle={onTogglePrinter}
              materials={plate.materials}
            />
            {config.selectedPrinters.length > 0 && (
              <div className="col gap-3" style={{ marginTop: 14 }}>
                {config.selectedPrinters.map(printerId => (
                  <PerPrinterConfig
                    key={printerId}
                    printerId={printerId}
                    config={config.perPrinter[printerId] || {}}
                    onChange={patch => onSetPerPrinter(printerId, patch)}
                    materials={plate.materials}
                  />
                ))}
              </div>
            )}
          </div>

          <div className="divider" style={{ margin: 0 }} />

          {/* Orders this plate fulfills */}
          <div>
            <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text-1)" }}>
              <StepNum n={3} done={config.orderIds.length > 0} />
              Fulfills orders
            </div>
            <div className="tiny muted" style={{ marginTop: 2, marginBottom: 10, marginLeft: 30 }}>
              Link this plate to the customer or internal order{config.orderIds.length === 1 ? "" : "s"} its parts ship into. Optional, but helps track progress.
            </div>
            <OrdersPicker
              selectedOrderIds={config.orderIds}
              onChange={onSetOrders}
            />
          </div>

          <div className="divider" style={{ margin: 0 }} />

          {/* Job details */}
          <div>
            <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text-1)" }}>
              <StepNum n={4} done={!!config.jobName.trim()} />
              Job details
            </div>
            <div className="tiny muted" style={{ marginTop: 2, marginBottom: 10, marginLeft: 30 }}>
              How this plate-job appears in the queue.
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12 }}>
              <div>
                <label className="label">Job name</label>
                <input className="input"
                       value={config.jobName}
                       onChange={e => onSetField({ jobName: e.target.value })}
                       placeholder="e.g. PA-CF arm brackets" />
              </div>
              <div>
                <label className="label">Priority</label>
                <select className="select"
                        value={config.priority}
                        onChange={e => onSetField({ priority: e.target.value })}>
                  <option value="rush">Rush — top of queue</option>
                  <option value="high">High</option>
                  <option value="normal">Normal</option>
                  <option value="low">Low / fill</option>
                </select>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ========================================================================
// Queue toggle (per-plate switch in the panel banner)
// ========================================================================

function QueueToggle({ checked, onChange }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      role="switch"
      aria-checked={checked}
      style={{
        display: "flex", alignItems: "center", gap: 10,
        background: "transparent", border: "none",
        padding: "4px 6px", cursor: "pointer",
        color: "var(--text-1)", fontFamily: "inherit",
        flexShrink: 0,
      }}>
      <div className="col" style={{ alignItems: "flex-end", lineHeight: 1.2 }}>
        <span style={{
            fontSize: 12.5,
            fontWeight: 500,
            color: checked ? "var(--text-1)" : "var(--text-3)",
          }}>
          {checked ? "Send to queue" : "Skip this plate"}
        </span>
        <span className="tiny muted" style={{ marginTop: 2 }}>
          {checked ? "Creates one job" : "No job created"}
        </span>
      </div>
      <div style={{
          width: 36, height: 20, borderRadius: 999,
          background: checked ? "var(--accent)" : "var(--bg-3)",
          border: `1px solid ${checked ? "var(--accent)" : "var(--border-2)"}`,
          position: "relative",
          transition: "background 120ms, border-color 120ms",
          flexShrink: 0,
          boxShadow: checked ? "0 0 0 3px var(--accent-glow)" : "none",
        }}>
        <div style={{
          width: 14, height: 14, borderRadius: "50%",
          background: "white",
          position: "absolute", top: 2,
          left: checked ? 18 : 2,
          transition: "left 120ms",
          boxShadow: "0 1px 2px rgba(0,0,0,0.25)",
        }}/>
      </div>
    </button>
  );
}

// ========================================================================
// Tab status dot
// ========================================================================

function TabDot({ queued, complete }) {
  if (!queued) {
    return (
      <span style={{
          width: 8, height: 8, borderRadius: "50%",
          border: "1px solid var(--border-2)",
          background: "transparent",
          flexShrink: 0,
        }} />
    );
  }
  const color = complete ? "var(--ok)" : "var(--warn)";
  return (
    <span style={{
        width: 8, height: 8, borderRadius: "50%",
        background: color,
        boxShadow: complete ? `0 0 6px ${color}` : "none",
        flexShrink: 0,
      }} />
  );
}

// ========================================================================
// Orders chip picker
// ========================================================================

function OrdersPicker({ selectedOrderIds, onChange }) {
  // Show open orders first; archive complete to the bottom in a separate row.
  const open = (window.ORDERS || []).filter(o => o.status !== "complete");
  const complete = (window.ORDERS || []).filter(o => o.status === "complete");

  function toggle(id) {
    if (selectedOrderIds.includes(id)) {
      onChange(selectedOrderIds.filter(x => x !== id));
    } else {
      onChange([...selectedOrderIds, id]);
    }
  }

  const Chip = ({ order }) => {
    const selected = selectedOrderIds.includes(order.id);
    return (
      <button onClick={(e) => { e.stopPropagation(); toggle(order.id); }}
              style={{
                display: "inline-flex", alignItems: "center", gap: 8,
                padding: "6px 10px 6px 8px",
                background: selected ? "var(--bg-3)" : "var(--bg-1)",
                border: `1px solid ${selected ? "var(--accent)" : "var(--border-1)"}`,
                boxShadow: selected ? "0 0 0 1px var(--accent)" : "none",
                borderRadius: 999,
                cursor: "pointer", color: "var(--text-1)",
                fontFamily: "inherit", fontSize: 12,
                whiteSpace: "nowrap",
                maxWidth: 280, minWidth: 0,
                transition: "background 120ms, border-color 120ms",
              }}>
        <span style={{
            width: 14, height: 14, flexShrink: 0,
            borderRadius: 4,
            border: `1.5px solid ${selected ? "var(--accent)" : "var(--border-2)"}`,
            background: selected ? "var(--accent)" : "transparent",
            display: "inline-grid", placeItems: "center",
          }}>
          {selected && <span style={{ color: "white", display: "inline-flex" }}>
            {React.cloneElement(window.Icons.check, { size: 9, stroke: 3 })}
          </span>}
        </span>
        <span className="mono tiny" style={{ color: "var(--text-3)", flexShrink: 0 }}>{order.id}</span>
        <span style={{
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            minWidth: 0, flex: 1,
          }}>
          {order.customer}
        </span>
        <window.StatusPill status={order.status} />
      </button>
    );
  };

  return (
    <div className="col gap-2">
      <div className="row gap-2" style={{ flexWrap: "wrap" }}>
        {open.map(o => <Chip key={o.id} order={o} />)}
        <button onClick={() => { /* placeholder: create new order */ }}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  padding: "6px 10px 6px 8px",
                  background: "transparent",
                  border: "1px dashed var(--border-2)",
                  borderRadius: 999,
                  color: "var(--text-3)",
                  cursor: "pointer", fontFamily: "inherit", fontSize: 12,
                }}>
          {React.cloneElement(window.Icons.plus, { size: 12 })} New order
        </button>
        <button onClick={() => onChange([])}
                disabled={selectedOrderIds.length === 0}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  padding: "6px 10px",
                  background: "transparent", border: "none",
                  color: selectedOrderIds.length === 0 ? "var(--text-4)" : "var(--text-3)",
                  cursor: selectedOrderIds.length === 0 ? "default" : "pointer",
                  fontFamily: "inherit", fontSize: 12,
                }}>
          None — standalone job
        </button>
      </div>
      {complete.length > 0 && (
        <details>
          <summary className="tiny muted" style={{ cursor: "pointer", padding: "4px 0", userSelect: "none" }}>
            Show completed orders ({complete.length})
          </summary>
          <div className="row gap-2" style={{ flexWrap: "wrap", marginTop: 6, opacity: 0.7 }}>
            {complete.map(o => <Chip key={o.id} order={o} />)}
          </div>
        </details>
      )}
    </div>
  );
}

// ========================================================================
// Printer picker + per-printer config
// ========================================================================

function PrinterPicker({ eligibleIds, selectedPrinters, onToggle, materials }) {
  return (
    <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
        gap: 10,
      }}>
      {window.PRINTERS.map(printer => {
        const eligible = eligibleIds.includes(printer.id);
        const selected = selectedPrinters.includes(printer.id);
        return (
          <button key={printer.id}
                  disabled={!eligible}
                  onClick={() => onToggle(printer.id)}
                  style={{
                    padding: 12,
                    background: selected ? "var(--bg-3)" : "var(--bg-1)",
                    border: `1px solid ${selected ? "var(--accent)" : "var(--border-1)"}`,
                    boxShadow: selected ? "0 0 0 1px var(--accent)" : "none",
                    borderRadius: 10, textAlign: "left",
                    cursor: eligible ? "pointer" : "not-allowed",
                    opacity: eligible ? 1 : 0.5,
                    color: "var(--text-1)", fontFamily: "inherit",
                    position: "relative",
                  }}>
            <div className="row gap-2" style={{ alignItems: "center" }}>
              <span className={`elig ${selected ? "on" : "off"}`}
                    style={{ background: selected ? "rgba(59,130,246,0.20)" : undefined }}>
                {printer.badge}
              </span>
              <div className="col" style={{ flex: 1, minWidth: 0 }}>
                <div className="small" style={{ fontWeight: 500 }}>{printer.nickname}</div>
                <div className="tiny muted">{printer.name}</div>
              </div>
              <Checkbox checked={selected} disabled={!eligible} />
            </div>
            <div className="tiny" style={{
                marginTop: 8,
                color: eligible ? "var(--text-3)" : "var(--err)",
              }}>
              {eligible
                ? printer.capabilities.slice(0, 3).join(" · ")
                : `Can't print ${materials.join(", ")}`}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function Checkbox({ checked, disabled }) {
  return (
    <div style={{
        width: 18, height: 18, flexShrink: 0,
        borderRadius: 5,
        border: `1.5px solid ${checked ? "var(--accent)" : "var(--border-2)"}`,
        background: checked ? "var(--accent)" : "transparent",
        display: "grid", placeItems: "center",
        opacity: disabled ? 0.5 : 1,
      }}>
      {checked && <span style={{ color: "white", display: "inline-flex" }}>
        {React.cloneElement(window.Icons.check, { size: 12, stroke: 3 })}
      </span>}
    </div>
  );
}

function PerPrinterConfig({ printerId, config, onChange, materials }) {
  const printer = window.getPrinter(printerId);
  const presets = useMemo(
    () => window.PROCESS_PRESETS.filter(p => p.printerId === printerId),
    [printerId]
  );
  const filaments = useMemo(
    () => filamentsForPrinter(printerId, materials),
    [printerId, materials]
  );

  const selectedPreset = presets.find(p => p.id === config.processId);
  const selectedFilament = filaments.find(f => f.filament.id === config.filamentId);
  const selectedFilProfileIdx = config.profileIdx ?? 0;
  const selectedFilProfile = selectedFilament?.filament.profiles.filter(pf => pf.printerId === printerId)[selectedFilProfileIdx];

  return (
    <div style={{
        padding: 14, background: "var(--bg-1)",
        border: "1px solid var(--border-1)", borderRadius: 10,
      }}>
      <div className="row gap-2" style={{ alignItems: "center", marginBottom: 12 }}>
        <span className="elig on">{printer.badge}</span>
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="small" style={{ fontWeight: 500 }}>{printer.nickname}</div>
          <div className="tiny muted">{printer.name}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <label className="label">Process preset</label>
          <select className="select"
                  value={config.processId || ""}
                  onChange={e => onChange({ processId: e.target.value })}>
            {presets.length === 0 && <option value="">— no presets —</option>}
            {presets.map(p => (
              <option key={p.id} value={p.id}>
                {p.name} · {p.layerHeight}mm
              </option>
            ))}
          </select>
          {selectedPreset && (
            <div className="tiny muted" style={{ marginTop: 6, lineHeight: 1.5 }}>
              {selectedPreset.nozzle} · {selectedPreset.walls} walls · {selectedPreset.infill}% infill
            </div>
          )}
        </div>

        <div>
          <label className="label">Filament + profile</label>
          <select className="select"
                  value={config.filamentId ? `${config.filamentId}::${selectedFilProfileIdx}` : ""}
                  onChange={e => {
                    const [filId, idxStr] = e.target.value.split("::");
                    onChange({ filamentId: filId, profileIdx: Number(idxStr) || 0 });
                  }}>
            {filaments.length === 0 && <option value="">— no compatible filaments —</option>}
            {filaments.flatMap(({ filament, profiles }) => profiles.map((pf, i) => (
              <option key={`${filament.id}::${i}`} value={`${filament.id}::${i}`}>
                {filament.name} · {pf.nozzle}
              </option>
            )))}
          </select>
          {selectedFilProfile && (
            <div className="row gap-2" style={{ marginTop: 6, alignItems: "center" }}>
              <FilamentDot color={selectedFilament.filament.color} />
              <span className="tiny muted">
                {selectedFilProfile.hotendTemp}° / {selectedFilProfile.bedTemp}° bed · {selectedFilProfile.layerHeight}mm
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function FilamentDot({ color }) {
  return (
    <span style={{
      display: "inline-block", width: 10, height: 10, borderRadius: "50%",
      background: color, border: "1px solid rgba(255,255,255,0.15)",
      boxShadow: `0 0 0 1px var(--border-2), 0 0 6px ${color}66`,
      flexShrink: 0,
    }} />
  );
}

// ========================================================================
// Right-rail summary
// ========================================================================

function SummaryCard({ file, plates, plateConfigs, selectedPlateIds, activePlateId, plateIsComplete }) {
  const totalTime = selectedPlateIds.reduce((a, id) => {
    const p = plates.find(x => x.id === id);
    return a + (p?.estTime || 0);
  }, 0);

  const allOrders = new Set();
  selectedPlateIds.forEach(id => {
    (plateConfigs[id]?.orderIds || []).forEach(o => allOrders.add(o));
  });

  return (
    <div className="card" style={{ padding: 18 }}>
      <div className="tag-key">Job summary</div>

      {/* file */}
      <div className="row gap-2" style={{ marginTop: 12 }}>
        <SummaryDot done={!!file} />
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="tiny" style={{
              color: file ? "var(--text-1)" : "var(--text-3)",
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>
            {file ? file.name : "No file yet"}
          </div>
        </div>
      </div>

      {/* plates picked */}
      <div className="row gap-2" style={{ marginTop: 10, alignItems: "flex-start" }}>
        <SummaryDot done={selectedPlateIds.length > 0} />
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="tiny" style={{ color: selectedPlateIds.length ? "var(--text-1)" : "var(--text-3)" }}>
            {selectedPlateIds.length === 0
              ? "No plates picked"
              : `${selectedPlateIds.length} plate${selectedPlateIds.length === 1 ? "" : "s"} → ${selectedPlateIds.length} job${selectedPlateIds.length === 1 ? "" : "s"}`}
          </div>
        </div>
      </div>

      {/* per-plate status */}
      {selectedPlateIds.length > 0 && (
        <div className="col gap-1" style={{ marginTop: 8, marginLeft: 24 }}>
          {selectedPlateIds.map(id => {
            const cfg = plateConfigs[id];
            const plate = plates.find(p => p.id === id);
            const complete = plateIsComplete(id);
            const isActive = activePlateId === id;
            return (
              <div key={id} className="row gap-2" style={{ alignItems: "center" }}>
                <span style={{
                    width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                    background: complete ? "var(--ok)" : "var(--warn)",
                    boxShadow: complete ? "0 0 4px var(--ok)" : "none",
                  }} />
                <span className="tiny" style={{
                    color: isActive ? "var(--text-1)" : "var(--text-2)",
                    fontWeight: isActive ? 500 : 400,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    flex: 1, minWidth: 0,
                  }}>
                  P{plate.index} · {cfg.jobName}
                </span>
                <span className="num tiny muted" style={{ flexShrink: 0 }}>
                  {cfg.selectedPrinters.length || 0}p
                </span>
              </div>
            );
          })}
        </div>
      )}

      {allOrders.size > 0 && (
        <>
          <div className="divider" />
          <div className="tag-key">Fulfills</div>
          <div className="row gap-1" style={{ marginTop: 6, flexWrap: "wrap" }}>
            {Array.from(allOrders).map(id => {
              const o = window.getOrder(id);
              return (
                <span key={id} className="mono tiny"
                      style={{
                        padding: "2px 8px", background: "var(--bg-1)",
                        border: "1px solid var(--border-1)",
                        borderRadius: 999, color: "var(--text-2)",
                      }}
                      title={o?.customer}>
                  {id}
                </span>
              );
            })}
          </div>
        </>
      )}

      <div className="divider" />

      <div className="row between">
        <span className="tag-key">Total plate time</span>
        <span className="num small">{totalTime > 0 ? window.fmtTime(totalTime) : "—"}</span>
      </div>
      <div className="row between" style={{ marginTop: 6 }}>
        <span className="tag-key">Slicing</span>
        <span className="small" style={{ color: "var(--text-2)" }}>On claim</span>
      </div>
    </div>
  );
}

function SummaryDot({ done }) {
  return (
    <span style={{
        width: 16, height: 16, flexShrink: 0,
        borderRadius: "50%",
        background: done ? "var(--accent)" : "var(--bg-3)",
        border: `1px solid ${done ? "var(--accent)" : "var(--border-1)"}`,
        display: "inline-grid", placeItems: "center", marginTop: 1,
      }}>
      {done && <span style={{ color: "white", display: "inline-flex" }}>
        {React.cloneElement(window.Icons.check, { size: 10, stroke: 3 })}
      </span>}
    </span>
  );
}

// ========================================================================
// helpers
// ========================================================================

function filamentsForPrinter(printerId, plateMaterials) {
  const wantsCF = plateMaterials.some(m => /cf/i.test(m));
  const wantsTPU = plateMaterials.some(m => /tpu/i.test(m));
  const wantsABS = plateMaterials.some(m => /\babs\b/i.test(m));
  const wantsASA = plateMaterials.some(m => /\basa\b/i.test(m));
  const wantsPETG = plateMaterials.some(m => /petg/i.test(m));
  const wantsPC = plateMaterials.some(m => /\bpc\b/i.test(m));
  const wantsMulti = plateMaterials.some(m => /multi|4-color/i.test(m));

  const matchesPlate = (fil) => {
    const t = fil.type.toLowerCase();
    if (wantsCF)    return t === "pa-cf" || /cf/i.test(fil.subtype || "");
    if (wantsTPU)   return t === "tpu";
    if (wantsABS)   return t === "abs";
    if (wantsASA)   return t === "asa";
    if (wantsPC)    return t === "pc";
    if (wantsMulti) return t === "pla";
    if (wantsPETG)  return t === "petg";
    return t === "pla";
  };

  return (window.FILAMENTS || [])
    .map(fil => ({
      filament: fil,
      profiles: (fil.profiles || []).filter(pf => pf.printerId === printerId),
    }))
    .filter(({ filament, profiles }) => profiles.length > 0 && matchesPlate(filament));
}

function pickFilamentForPrinter(printerId, plateMaterials) {
  const opts = filamentsForPrinter(printerId, plateMaterials);
  if (opts.length === 0) {
    const fallback = (window.FILAMENTS || []).find(
      f => (f.profiles || []).some(p => p.printerId === printerId)
    );
    if (!fallback) return null;
    return { id: fallback.id, profileIdx: 0 };
  }
  const fav = opts.find(o => o.filament.favorite);
  const pick = fav || opts[0];
  return { id: pick.filament.id, profileIdx: 0 };
}

Object.assign(window, { NewJobScreen });
