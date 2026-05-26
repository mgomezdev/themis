/* global React, window */
const { useState, useMemo } = React;

// =========================================================================
// Orders screen — list + detail (parts breakdown rolling up to jobs)
// =========================================================================

function OrdersScreen({ tweaks }) {
  const [expanded, setExpanded] = useState("ORD-2241");
  const [filter, setFilter] = useState("open"); // open | all | customer | internal

  const filtered = useMemo(() => {
    return window.ORDERS.filter(o => {
      if (filter === "open") return o.status !== "complete";
      if (filter === "customer") return o.type === "customer";
      if (filter === "internal") return o.type === "internal";
      return true;
    });
  }, [filter]);

  return (
    <div className="col gap-4" style={{ maxWidth: 1200 }}>
      <div className="row gap-2">
        {[["open","Open"],["all","All"],["customer","Customer"],["internal","Internal"]].map(([k,l]) => (
          <button key={k} onClick={()=>setFilter(k)} className={`btn sm ${filter===k?"primary":""}`}
                  style={filter===k?null:{background:"transparent", borderColor:"var(--border-1)"}}>{l}</button>
        ))}
        <div style={{ flex: 1 }}/>
        <span className="tiny muted" style={{whiteSpace:"nowrap"}}>{filtered.length} orders</span>
      </div>

      <div className="col" style={{ gap: 10 }}>
        {filtered.map(o => (
          <OrderAccordion key={o.id}
                          order={o}
                          expanded={expanded === o.id}
                          onToggle={() => setExpanded(expanded === o.id ? null : o.id)} />
        ))}
      </div>
    </div>
  );
}

function OrderAccordion({ order, expanded, onToggle }) {
  const totalParts = order.parts.reduce((a,b) => a + b.qty, 0);
  const printedParts = order.parts.reduce((a,b) => a + b.printed, 0);
  const pct = totalParts > 0 ? Math.round((printedParts / totalParts) * 100) : 0;
  const remainingTime = order.parts.reduce((a,b) => a + (b.qty - b.printed) * b.est, 0);
  const relatedJobs = window.JOBS.filter(j => j.parts.some(pr => pr.orderId === order.id));

  return (
    <div className="card"
         style={{
           padding: 0,
           overflow: "hidden",
           borderColor: expanded ? "var(--border-3)" : "var(--border-1)",
         }}>
      {/* SUMMARY HEADER — always visible, click to toggle */}
      <button onClick={onToggle}
              style={{
                width: "100%",
                background: "transparent",
                border: "none",
                color: "inherit",
                textAlign: "left",
                padding: "14px 18px",
                cursor: "pointer",
                display: "block",
              }}>
        <div className="row gap-4" style={{ alignItems: "center" }}>
          {/* chevron */}
          <div style={{
            width: 20,
            color: "var(--text-3)",
            transform: expanded ? "rotate(90deg)" : "none",
            transition: "transform 160ms ease",
            display: "inline-flex",
          }}>{window.Icons.chevR}</div>

          {/* ID + type */}
          <div className="col" style={{ width: 130, flexShrink: 0 }}>
            <span className="mono tiny muted" style={{whiteSpace:"nowrap"}}>{order.id}</span>
            <span className="tiny" style={{
              padding: "1px 6px",
              borderRadius: 4,
              background: order.type === "internal" ? "rgba(99,102,241,0.12)" : "rgba(56,189,248,0.12)",
              color: order.type === "internal" ? "#a5b4fc" : "var(--info)",
              fontWeight: 500,
              alignSelf: "flex-start",
              marginTop: 3,
            }}>{order.type === "internal" ? "INTERNAL" : "CUSTOMER"}</span>
          </div>

          {/* Title + customer */}
          <div className="col" style={{ flex: "1 1 0", minWidth: 0 }}>
            <div style={{
              fontWeight: 500, fontSize: 14,
              whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis"
            }}>{order.title}</div>
            <div className="tiny muted" style={{ marginTop: 2,
              whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis" }}>
              {order.customer}
            </div>
          </div>

          {/* Due */}
          <div className="col" style={{ width: 90, flexShrink: 0 }}>
            <span className="tag-key">DUE</span>
            <span className="num small" style={{ marginTop: 2, whiteSpace:"nowrap" }}>{order.due.slice(5)}</span>
          </div>

          {/* Parts */}
          <div className="col" style={{ width: 130, flexShrink: 0 }}>
            <div className="row between">
              <span className="tag-key">PARTS</span>
              <span className="num tiny" style={{ color: pct === 100 ? "var(--ok)" : "var(--text-2)" }}>
                {printedParts}<span className="muted">/{totalParts}</span>
              </span>
            </div>
            <div style={{ marginTop: 6 }}><window.Progress value={pct} /></div>
          </div>

          {/* Time remaining */}
          <div className="col" style={{ width: 80, flexShrink: 0, alignItems: "flex-end" }}>
            <span className="tag-key">REMAINING</span>
            <span className="num small" style={{ marginTop: 2, whiteSpace:"nowrap",
              color: remainingTime === 0 ? "var(--text-3)" : "var(--text-1)" }}>
              {remainingTime > 0 ? window.fmtTime(remainingTime) : "—"}
            </span>
          </div>

          {/* Status */}
          <div style={{ width: 110, flexShrink: 0, display: "flex", justifyContent: "flex-end" }}>
            <window.StatusPill status={order.status} />
          </div>
        </div>
      </button>

      {/* EXPANDED BODY */}
      {expanded && <OrderExpandedBody order={order} relatedJobs={relatedJobs}
                                       totalParts={totalParts} printedParts={printedParts}
                                       totalTime={remainingTime} />}
    </div>
  );
}

function OrderExpandedBody({ order, relatedJobs, totalParts, printedParts, totalTime }) {
  return (
    <div style={{
      borderTop: "1px solid var(--border-1)",
      background: "var(--bg-1)",
    }}>
      {/* Notes + actions */}
      <div className="row between" style={{ padding: "14px 18px", alignItems: "flex-start" }}>
        <div className="col" style={{ flex: 1, marginRight: 16 }}>
          {order.notes ? (
            <div style={{
              padding: 12,
              background: "var(--bg-2)",
              borderRadius: 8,
              borderLeft: "2px solid var(--accent)",
              color: "var(--text-2)",
              fontSize: 13,
              maxWidth: 700,
            }}>
              <span className="tag-key" style={{display:"block", marginBottom:4}}>Notes</span>
              {order.notes}
            </div>
          ) : (
            <div className="tiny muted">No notes. Placed {order.placed}.</div>
          )}
        </div>
        <div className="row gap-2" style={{ flexShrink: 0 }}>
          <button className="btn sm" onClick={e => e.stopPropagation()}>{window.Icons.copy} Clone</button>
          <button className="btn sm" onClick={e => e.stopPropagation()}>{window.Icons.plus} Add part</button>
          <button className="btn icon sm" onClick={e => e.stopPropagation()}>{window.Icons.more}</button>
        </div>
      </div>

      {/* Parts breakdown table */}
      <div style={{ padding: "0 0 14px" }}>
        <div style={{ padding: "0 18px 8px" }}>
          <span className="tag-key">Parts breakdown · {order.parts.length} unique</span>
        </div>
        <table className="tbl" style={{ background: "transparent" }}>
          <thead>
            <tr>
              <th style={{ background: "var(--bg-1)", paddingLeft: 18 }}></th>
              <th style={{ background: "var(--bg-1)" }}>Part</th>
              <th style={{ background: "var(--bg-1)" }}>Material</th>
              <th style={{ background: "var(--bg-1)", width: 140 }}>Qty</th>
              <th style={{ background: "var(--bg-1)", width: 200 }}>Status</th>
              <th style={{ background: "var(--bg-1)", width: 100, textAlign:"right" }}>Est. each</th>
              <th style={{ background: "var(--bg-1)", width: 110, textAlign:"right", paddingRight: 18 }}>Remaining</th>
            </tr>
          </thead>
          <tbody>
            {order.parts.map((p, i) => {
              const remaining = p.qty - p.printed;
              const remainTime = remaining * p.est;
              return (
                <tr key={p.id} style={{ cursor: "default" }}>
                  <td style={{ width: 40, paddingLeft: 18 }}>
                    <div style={{
                      width: 32, height: 32, borderRadius: 4,
                      background: p.thumbColor, border:"1px solid var(--border-1)",
                      backgroundImage: "linear-gradient(135deg, rgba(255,255,255,0.08), transparent 50%)",
                    }}/>
                  </td>
                  <td>
                    <div style={{ fontWeight: 500 }}>{p.name}</div>
                    <div className="mono tiny muted">{p.id}</div>
                  </td>
                  <td><window.MaterialChip material={p.material} color={window.matColor(p.material)} /></td>
                  <td>
                    <div className="num small"><span style={{ color: "var(--text-1)" }}>{p.printed}</span> / {p.qty}</div>
                    <div style={{ width: 100, marginTop: 4 }}>
                      <window.Progress value={Math.round((p.printed/p.qty)*100)} />
                    </div>
                  </td>
                  <td>
                    {p.printed === p.qty
                      ? <window.StatusPill status="complete" />
                      : p.printed > 0
                        ? <window.StatusPill status="partial" label={`${p.printed} done · ${remaining} to go`} />
                        : <window.StatusPill status="queued" />
                    }
                  </td>
                  <td className="num small" style={{ textAlign:"right", whiteSpace:"nowrap" }}>{window.fmtTime(p.est)}</td>
                  <td className="num small" style={{ textAlign:"right", whiteSpace:"nowrap", paddingRight: 18,
                       color: remaining > 0 ? "var(--text-1)" : "var(--text-3)" }}>
                    {remaining > 0 ? window.fmtTime(remainTime) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Related jobs */}
      {relatedJobs.length > 0 && (
        <div style={{
          padding: "14px 18px 18px",
          borderTop: "1px solid var(--border-1)",
          background: "var(--bg-0)",
        }}>
          <span className="tag-key">Jobs filling this order · {relatedJobs.length}</span>
          <div className="col gap-2" style={{ marginTop: 8 }}>
            {relatedJobs.map(j => {
              const partsHere = j.parts.filter(pr => pr.orderId === order.id);
              return (
                <div key={j.id} className="row between" style={{
                  padding: "10px 12px",
                  background: "var(--bg-2)",
                  borderRadius: 8,
                  border: "1px solid var(--border-1)",
                }}>
                  <div className="row gap-3" style={{ alignItems: "center", minWidth: 0 }}>
                    <span className="mono tiny muted" style={{whiteSpace:"nowrap"}}>{j.id}</span>
                    <div style={{
                      fontWeight: 500, fontSize: 13,
                      whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis"
                    }}>{j.plateName}</div>
                    <window.StatusPill status={j.status} />
                  </div>
                  <div className="row gap-4" style={{ flexShrink: 0 }}>
                    <span className="tiny muted" style={{whiteSpace:"nowrap"}}>
                      covers {partsHere.map(pr => `${pr.qty}× ${pr.partId.split("-").pop()}`).join(", ")}
                    </span>
                    <span className="num tiny" style={{whiteSpace:"nowrap"}}>{window.fmtTime(j.estTime)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function OrderRow({ order, active, onClick }) {
  const totalParts = order.parts.reduce((a,b) => a + b.qty, 0);
  const printedParts = order.parts.reduce((a,b) => a + b.printed, 0);
  const pct = totalParts > 0 ? Math.round((printedParts / totalParts) * 100) : 0;
  return (
    <button onClick={onClick}
            className="card"
            style={{
              textAlign: "left", cursor: "pointer", padding: 12,
              background: active ? "var(--bg-3)" : "var(--bg-2)",
              borderColor: active ? "var(--accent)" : "var(--border-1)",
              borderLeft: active ? `3px solid var(--accent)` : "1px solid var(--border-1)",
            }}>
      <div className="row between" style={{ alignItems:"flex-start", gap: 8 }}>
        <div className="col" style={{ minWidth: 0, flex: 1 }}>
          <div className="row gap-2" style={{ alignItems:"baseline", whiteSpace: "nowrap" }}>
            <span className="mono tiny muted">{order.id}</span>
            <span className="tiny" style={{
              padding: "1px 6px",
              borderRadius: 4,
              background: order.type === "internal" ? "rgba(99,102,241,0.12)" : "rgba(56,189,248,0.12)",
              color: order.type === "internal" ? "#a5b4fc" : "var(--info)",
              fontWeight: 500
            }}>{order.type === "internal" ? "INT" : "CUS"}</span>
          </div>
          <div style={{ fontWeight: 500, marginTop: 2, fontSize: 13.5,
                        whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis" }}>
            {order.customer}
          </div>
          <div className="tiny muted" style={{ marginTop: 1,
            whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis" }}>{order.title}</div>
        </div>
        <window.StatusPill status={order.status} />
      </div>
      <div className="row between" style={{ marginTop: 8 }}>
        <div className="tiny muted" style={{whiteSpace:"nowrap"}}>due <span className="num">{order.due.slice(5)}</span></div>
        <div className="num tiny" style={{ color: pct === 100 ? "var(--ok)" : "var(--text-2)", whiteSpace:"nowrap" }}>{printedParts}/{totalParts}</div>
      </div>
      <div style={{ marginTop: 6 }}><window.Progress value={pct} /></div>
    </button>
  );
}

function OrderDetail({ order }) {
  const totalParts = order.parts.reduce((a,b) => a + b.qty, 0);
  const printedParts = order.parts.reduce((a,b) => a + b.printed, 0);
  const totalTime = order.parts.reduce((a,b) => a + (b.qty - b.printed) * b.est, 0);
  const relatedJobs = window.JOBS.filter(j => j.parts.some(pr => pr.orderId === order.id));

  return (
    <div className="col gap-4" style={{ minWidth: 0 }}>
      <div className="card" style={{ padding: 20 }}>
        <div className="row between" style={{ alignItems:"flex-start" }}>
          <div>
            <div className="row gap-2" style={{ alignItems:"baseline" }}>
              <span className="mono small muted">{order.id}</span>
              <span className="tiny" style={{
                padding: "2px 8px",
                borderRadius: 4,
                background: order.type === "internal" ? "rgba(99,102,241,0.12)" : "rgba(56,189,248,0.12)",
                color: order.type === "internal" ? "#a5b4fc" : "var(--info)",
                fontWeight: 500
              }}>{order.type === "internal" ? "INTERNAL" : "CUSTOMER"}</span>
            </div>
            <div style={{ fontSize: 22, fontWeight: 600, marginTop: 4, letterSpacing:"-0.02em" }}>{order.title}</div>
            <div className="muted small" style={{ marginTop: 2 }}>{order.customer}</div>
          </div>
          <div className="row gap-2">
            <window.StatusPill status={order.status} />
            <button className="btn sm">{window.Icons.copy} Clone</button>
            <button className="btn icon sm">{window.Icons.more}</button>
          </div>
        </div>

        <div className="row gap-6" style={{ marginTop: 18, flexWrap: "wrap" }}>
          <window.Kv k="Placed"        v={<span className="num small">{order.placed}</span>} />
          <window.Kv k="Due"           v={<span className="num small">{order.due}</span>} />
          <window.Kv k="Parts"         v={<span className="num small">{printedParts}<span className="muted"> / {totalParts}</span></span>} />
          <window.Kv k="Remaining"     v={<span className="num small">{window.fmtTime(totalTime)}</span>} />
          <window.Kv k="Jobs"          v={<span className="num small">{relatedJobs.length}</span>} />
        </div>

        {order.notes && (
          <div style={{
            marginTop: 16, padding: 12,
            background: "var(--bg-1)", borderRadius: 8,
            borderLeft: "2px solid var(--accent)",
            color: "var(--text-2)", fontSize: 13
          }}>
            <span className="tag-key" style={{display:"block", marginBottom:4}}>Notes</span>
            {order.notes}
          </div>
        )}
      </div>

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ padding: "14px 20px", borderBottom:"1px solid var(--border-1)" }}>
          <window.SectionHeader title="Parts breakdown"
                                 sub={`${order.parts.length} unique parts`}
                                 actions={<button className="btn sm">{window.Icons.plus} Add part</button>} />
        </div>
        <table className="tbl">
          <thead>
            <tr>
              <th></th>
              <th>Part</th>
              <th>Material</th>
              <th style={{ width: 120 }}>Qty</th>
              <th style={{ width: 220 }}>Status</th>
              <th style={{ width: 110, textAlign:"right" }}>Est. each</th>
              <th style={{ width: 110, textAlign:"right" }}>Remaining</th>
            </tr>
          </thead>
          <tbody>
            {order.parts.map((p, i) => {
              const remaining = p.qty - p.printed;
              const remainTime = remaining * p.est;
              return (
                <tr key={p.id}>
                  <td style={{ width: 40 }}>
                    <div style={{
                      width: 32, height: 32, borderRadius: 4,
                      background: p.thumbColor, border:"1px solid var(--border-1)",
                      backgroundImage: "linear-gradient(135deg, rgba(255,255,255,0.08), transparent 50%)",
                    }}/>
                  </td>
                  <td>
                    <div style={{ fontWeight: 500 }}>{p.name}</div>
                    <div className="mono tiny muted">{p.id}</div>
                  </td>
                  <td><window.MaterialChip material={p.material} color={window.matColor(p.material)} /></td>
                  <td>
                    <div className="num small"><span style={{ color: "var(--text-1)" }}>{p.printed}</span> / {p.qty}</div>
                    <div style={{ width: 90, marginTop: 4 }}>
                      <window.Progress value={Math.round((p.printed/p.qty)*100)} />
                    </div>
                  </td>
                  <td>
                    {p.printed === p.qty
                      ? <window.StatusPill status="complete" />
                      : p.printed > 0
                        ? <window.StatusPill status="partial" label={`${p.printed} done · ${remaining} to go`} />
                        : <window.StatusPill status="queued" />
                    }
                  </td>
                  <td className="num small" style={{ textAlign:"right" }}>{window.fmtTime(p.est)}</td>
                  <td className="num small" style={{ textAlign:"right", color: remaining > 0 ? "var(--text-1)" : "var(--text-3)" }}>
                    {remaining > 0 ? window.fmtTime(remainTime) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ padding: 20 }}>
        <window.SectionHeader title="Jobs filling this order"
                               sub="Plates that include parts from this order" />
        {relatedJobs.length === 0 && <window.Empty title="No jobs yet" icon={window.Icons.queue} />}
        <div className="col gap-2">
          {relatedJobs.map(j => {
            const partsHere = j.parts.filter(pr => pr.orderId === order.id);
            return (
              <div key={j.id} className="card" style={{ padding: 12, background:"var(--bg-1)" }}>
                <div className="row between">
                  <div className="row gap-3" style={{ alignItems:"center" }}>
                    <span className="mono tiny muted">{j.id}</span>
                    <div style={{ fontWeight: 500, fontSize: 13.5 }}>{j.plateName}</div>
                    <window.StatusPill status={j.status} />
                  </div>
                  <div className="row gap-3">
                    <span className="tiny muted">covers: {partsHere.map(pr => `${pr.qty}× ${pr.partId.split("-").pop()}`).join(", ")}</span>
                    <span className="num tiny">{window.fmtTime(j.estTime)}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { OrdersScreen });
