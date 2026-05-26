/* global React, window */
const { useState } = React;

// =========================================================================
// New order intake — pick customer/internal, add parts (with qty + material),
// see auto-suggested plate groupings.
// =========================================================================

function NewOrderScreen({ onCancel }) {
  const [orderType, setOrderType] = useState("customer");
  const [customer, setCustomer] = useState("");
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("2026-05-28");
  const [notes, setNotes] = useState("");

  const [parts, setParts] = useState([
    { name: "Arm bracket — L", file: "vr_arm_bracket_L.3mf", qty: 4, material: "PA-CF", est: 78 },
    { name: "Cable clamp",     file: "cable_clamp_v3.3mf",   qty: 16, material: "PETG", est: 12 },
  ]);

  function addPart() {
    setParts([...parts, { name: "", file: "", qty: 1, material: "PLA", est: 30 }]);
  }
  function updPart(i, patch) {
    setParts(parts.map((p, idx) => idx === i ? { ...p, ...patch } : p));
  }
  function delPart(i) {
    setParts(parts.filter((_, idx) => idx !== i));
  }

  // Auto-group: parts by material → suggested plates
  const groups = parts.reduce((acc, p) => {
    if (!p.material) return acc;
    (acc[p.material] = acc[p.material] || []).push(p);
    return acc;
  }, {});
  const totalQty = parts.reduce((a,b)=>a + (Number(b.qty)||0), 0);
  const totalTime = parts.reduce((a,b)=>a + (Number(b.qty)||0) * (Number(b.est)||0), 0);

  const customers = ["Vela Robotics","Hartwell Models","Northbeam Audio","Atlas Mechanical","New customer…"];

  return (
    <div className="col gap-4">
      <div className="row gap-2">
        <button className="btn ghost sm" onClick={onCancel}>{window.Icons.chevL} Orders</button>
        <span className="muted small">/</span>
        <span className="small">New order</span>
      </div>

      <div className="screen-grid" style={{ gridTemplateColumns: "minmax(0, 1fr) 320px", gap: 18 }}>
        <div className="col gap-4">
          {/* Header */}
          <div className="card" style={{ padding: 20 }}>
            <window.SectionHeader title="Order info" />
            <div className="row gap-3" style={{ marginBottom: 14 }}>
              {[
                { id: "customer", label: "Customer order", sub: "Goes to a paying customer" },
                { id: "internal", label: "Internal project", sub: "R&D, marketing, spares" },
              ].map(opt => (
                <button key={opt.id} onClick={() => setOrderType(opt.id)}
                        className="card"
                        style={{
                          flex: 1, textAlign:"left", padding: 14, cursor:"pointer",
                          background: orderType===opt.id ? "var(--bg-3)" : "var(--bg-1)",
                          borderColor: orderType===opt.id ? "var(--accent)" : "var(--border-1)",
                        }}>
                  <div className="row between">
                    <div>
                      <div style={{ fontWeight: 500 }}>{opt.label}</div>
                      <div className="tiny muted" style={{ marginTop: 2 }}>{opt.sub}</div>
                    </div>
                    <div style={{
                      width: 16, height: 16, borderRadius: 8,
                      border: `2px solid ${orderType===opt.id ? "var(--accent)" : "var(--border-2)"}`,
                      background: orderType===opt.id ? "var(--accent)" : "transparent",
                      boxShadow: orderType===opt.id ? "inset 0 0 0 3px var(--bg-3)" : "none"
                    }}/>
                  </div>
                </button>
              ))}
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12 }}>
              <div>
                <label className="label">{orderType === "internal" ? "Project name" : "Customer"}</label>
                {orderType === "customer" ? (
                  <select className="select" value={customer} onChange={e => setCustomer(e.target.value)}>
                    <option value="">Select customer…</option>
                    {customers.map(c => <option key={c}>{c}</option>)}
                  </select>
                ) : (
                  <input className="input" value={customer} onChange={e => setCustomer(e.target.value)}
                         placeholder="e.g. R&D — reflow oven" />
                )}
              </div>
              <div>
                <label className="label">Due</label>
                <input type="date" className="input" value={due} onChange={e=>setDue(e.target.value)} />
              </div>
            </div>

            <div style={{ marginTop: 12 }}>
              <label className="label">Title</label>
              <input className="input" value={title} onChange={e=>setTitle(e.target.value)}
                     placeholder="e.g. Mk3 chassis brackets — batch 5" />
            </div>

            <div style={{ marginTop: 12 }}>
              <label className="label">Notes (optional)</label>
              <textarea className="textarea" value={notes} onChange={e=>setNotes(e.target.value)}
                        placeholder="Material preferences, finishing, anything special…" />
            </div>
          </div>

          {/* Parts */}
          <div className="card" style={{ padding: 0, overflow:"hidden" }}>
            <div style={{ padding:"14px 20px", borderBottom:"1px solid var(--border-1)" }}>
              <window.SectionHeader title="Parts to print"
                                     sub={`${parts.length} unique parts · ${totalQty} units total`}
                                     actions={<button className="btn sm" onClick={addPart}>{window.Icons.plus} Add row</button>} />
            </div>

            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ width: 40 }}></th>
                  <th>Part / file</th>
                  <th style={{ width: 130 }}>Material</th>
                  <th style={{ width: 90 }}>Qty</th>
                  <th style={{ width: 110 }}>Est. each</th>
                  <th style={{ width: 40 }}></th>
                </tr>
              </thead>
              <tbody>
                {parts.map((p, i) => (
                  <tr key={i} style={{ cursor:"default" }}>
                    <td>
                      <div style={{
                        width: 32, height: 32, borderRadius: 4,
                        background: window.matColor(p.material),
                        border:"1px solid var(--border-1)",
                        opacity: 0.7
                      }}/>
                    </td>
                    <td>
                      <input className="input" placeholder="Part name"
                             value={p.name}
                             onChange={e=>updPart(i,{name:e.target.value})}/>
                      <div className="tiny muted mono" style={{ marginTop: 4 }}>{p.file || "drop a .3mf or .stl"}</div>
                    </td>
                    <td>
                      <select className="select" value={p.material} onChange={e=>updPart(i,{material:e.target.value})}>
                        {["PLA","PETG","PLA-CF","PA-CF","ABS","ASA","PC","TPU","PLA (4-color)"].map(m => <option key={m}>{m}</option>)}
                      </select>
                    </td>
                    <td>
                      <input className="input num" type="number" min="1" value={p.qty}
                             onChange={e=>updPart(i,{qty:Number(e.target.value)})}/>
                    </td>
                    <td>
                      <input className="input num" type="number" min="1" value={p.est} step="5"
                             onChange={e=>updPart(i,{est:Number(e.target.value)})}/>
                    </td>
                    <td>
                      <button className="btn ghost icon sm" onClick={() => delPart(i)}>{window.Icons.x}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div style={{ padding: 14, borderTop:"1px solid var(--border-1)" }}>
              <button className="btn sm" onClick={addPart}>{window.Icons.plus} Add part</button>
              <button className="btn ghost sm" style={{ marginLeft: 8 }}>{window.Icons.upload} Import from .3mf</button>
            </div>
          </div>
        </div>

        {/* Right: live preview / plate grouping */}
        <div className="col gap-4">
          <div className="card" style={{ padding: 18 }}>
            <div className="tag-key">Suggested plates</div>
            <div className="small muted" style={{ marginTop: 2, marginBottom: 14 }}>
              Auto-grouped by material. You can re-pack at slice time.
            </div>
            {Object.entries(groups).map(([mat, list]) => {
              const total = list.reduce((a,b)=>a + Number(b.qty||0)*Number(b.est||0), 0);
              return (
                <div key={mat} className="card" style={{ padding: 12, background:"var(--bg-1)", marginBottom: 10 }}>
                  <div className="row between" style={{ marginBottom: 8 }}>
                    <window.MaterialChip material={mat} color={window.matColor(mat)} />
                    <span className="num tiny muted">{window.fmtTime(total)}</span>
                  </div>
                  {list.map((p, i) => (
                    <div key={i} className="row between" style={{ padding: "2px 0" }}>
                      <span className="small" style={{whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis"}}>{p.name || <span className="muted">unnamed</span>}</span>
                      <span className="num tiny muted">×{p.qty}</span>
                    </div>
                  ))}
                  <div className="row gap-2 tiny muted" style={{ marginTop: 8 }}>
                    eligible: <window.EligibilityChips ids={
                      mat.includes("PA-CF") || mat === "ABS" ? ["ecc-01"] :
                      mat.includes("4-color") ? ["snp-01"] :
                      ["p1s-01","ecc-01","snp-01"].filter(_ => true)
                    } />
                  </div>
                </div>
              );
            })}
            <div className="divider" />
            <div className="row between">
              <span className="tag-key">Total time</span>
              <span className="num small">{window.fmtTime(totalTime)}</span>
            </div>
            <div className="row between" style={{ marginTop: 4 }}>
              <span className="tag-key">Plates</span>
              <span className="num small">{Object.keys(groups).length}</span>
            </div>
          </div>

          <div className="card" style={{ padding: 18 }}>
            <button className="btn primary" style={{ width: "100%" }}>{window.Icons.check} Create order &amp; queue jobs</button>
            <button className="btn ghost sm" style={{ width: "100%", marginTop: 8 }}>Save draft</button>
            <div className="tiny muted" style={{ marginTop: 10, textAlign:"center" }}>
              Plates queue at the bottom. Slicing happens when a printer claims.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { NewOrderScreen });
