/* global React, window */
const { useState, useMemo } = React;

// =========================================================================
// Settings — sub-paged surface with shared left sub-nav.
// Pages:  General · Tags · Notifications · Print defaults · Data & backup · About
// =========================================================================

function SettingsScreen({ subroute, setSubroute }) {
  // Subroute is owned by App (so URL/router can take over later); fall back here.
  const [localPage, setLocalPage] = useState("general");
  const page = subroute || localPage;
  const setPage = setSubroute || setLocalPage;

  const sections = [
    {
      label: "Workshop",
      items: [
        { id: "general",       label: "General",        icon: window.Icons.settings, sub: "Units, defaults, workshop name" },
        { id: "tags",          label: "Tags",           icon: SettingsIcons.tag,     sub: "Manage labels across files & jobs" },
        { id: "print",         label: "Print defaults", icon: window.Icons.printer,  sub: "Slicing, drying, low-spool" },
      ],
    },
    {
      label: "Integrations",
      items: [
        { id: "spoolman",      label: "Spoolman",       icon: SettingsIcons.spoolman, sub: "Sync filament inventory" },
      ],
    },
    {
      label: "System",
      items: [
        { id: "notifications", label: "Notifications",  icon: window.Icons.bell,     sub: "Alerts for jobs & spools" },
        { id: "data",          label: "Data & backup",  icon: SettingsIcons.backup,  sub: "Export, import, cleanup" },
        { id: "about",         label: "About",          icon: SettingsIcons.info,    sub: "Version & links" },
      ],
    },
  ];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "240px 1fr", gap: 18, minHeight: 0 }}>
      {/* sub-nav */}
      <aside style={{
          position: "sticky", top: 0, height: "fit-content",
          background: "var(--bg-2)",
          border: "1px solid var(--border-1)",
          borderRadius: 12,
          padding: 8,
        }}>
        {sections.map((sec, si) => (
          <div key={sec.label} style={{ marginTop: si === 0 ? 0 : 12 }}>
            <div className="nav-section-label" style={{ padding: "8px 12px 4px" }}>{sec.label}</div>
            <div className="col" style={{ gap: 1 }}>
              {sec.items.map(item => (
                <button key={item.id}
                        onClick={() => setPage(item.id)}
                        className={`nav-item ${page === item.id ? "active" : ""}`}
                        style={{ borderRadius: 8 }}>
                  {item.icon}
                  <span className="label">{item.label}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </aside>

      {/* page */}
      <div style={{ minWidth: 0 }}>
        {page === "general"       && <GeneralPage />}
        {page === "tags"          && <TagsPage />}
        {page === "print"         && <PrintDefaultsPage />}
        {page === "spoolman"      && <SpoolmanPage />}
        {page === "notifications" && <NotificationsPage />}
        {page === "data"          && <DataBackupPage />}
        {page === "about"         && <AboutPage />}
      </div>
    </div>
  );
}

// ========================================================================
// Shared layout pieces
// ========================================================================

function PageHeader({ title, sub, actions }) {
  return (
    <div className="row between" style={{ marginBottom: 18, alignItems: "flex-start" }}>
      <div>
        <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600, letterSpacing: "-0.01em" }}>{title}</h2>
        {sub && <div className="muted small" style={{ marginTop: 4 }}>{sub}</div>}
      </div>
      {actions && <div className="row gap-2">{actions}</div>}
    </div>
  );
}

function FieldRow({ label, hint, children }) {
  return (
    <div style={{
        display: "grid", gridTemplateColumns: "1fr 360px", gap: 24,
        padding: "16px 0",
        borderBottom: "1px solid var(--border-1)",
        alignItems: "flex-start",
      }}>
      <div style={{ paddingTop: 4 }}>
        <div style={{ fontSize: 13.5, fontWeight: 500, color: "var(--text-1)" }}>{label}</div>
        {hint && <div className="tiny muted" style={{ marginTop: 4, lineHeight: 1.5, maxWidth: 480 }}>{hint}</div>}
      </div>
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}

function Toggle({ checked, onChange }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      style={{
        width: 38, height: 22, borderRadius: 999,
        background: checked ? "var(--accent)" : "var(--bg-3)",
        border: `1px solid ${checked ? "var(--accent)" : "var(--border-2)"}`,
        position: "relative",
        cursor: "pointer",
        boxShadow: checked ? "0 0 0 3px var(--accent-glow)" : "none",
        transition: "background 120ms, border-color 120ms",
        padding: 0,
        flexShrink: 0,
      }}>
      <div style={{
        position: "absolute", top: 2, left: checked ? 18 : 2,
        width: 16, height: 16, borderRadius: "50%",
        background: "white",
        boxShadow: "0 1px 2px rgba(0,0,0,0.3)",
        transition: "left 120ms",
      }}/>
    </button>
  );
}

// ========================================================================
// General
// ========================================================================

function GeneralPage() {
  const [s, set] = useState({ ...window.SETTINGS.general });
  const update = (patch) => {
    const next = { ...s, ...patch };
    set(next);
    Object.assign(window.SETTINGS.general, patch);
  };
  return (
    <div className="card" style={{ padding: 28 }}>
      <PageHeader title="General" sub="Workshop-wide defaults that the rest of the app inherits." />

      <FieldRow label="Workshop name"
                hint="Shown on print labels, exported PDFs, and at the top of dashboard reports.">
        <input className="input" value={s.workshopName}
               onChange={e => update({ workshopName: e.target.value })}
               style={{ width: "100%" }} />
      </FieldRow>

      <FieldRow label="Units"
                hint="Affects build volumes, layer heights, and material lengths shown across the app.">
        <Segmented value={s.units} onChange={v => update({ units: v })}
                   options={[{value:"metric", label:"Metric · mm / g"},
                             {value:"imperial", label:"Imperial · in / oz"}]} />
      </FieldRow>

      <FieldRow label="Date format"
                hint="Used for order due dates and job timestamps.">
        <Segmented value={s.dateFormat} onChange={v => update({ dateFormat: v })}
                   options={[{value:"iso",label:"2026-05-25"},
                             {value:"us", label:"05/25/2026"},
                             {value:"eu", label:"25.05.2026"}]} />
      </FieldRow>

      <FieldRow label="Week starts on"
                hint="Affects the weekly schedule view.">
        <Segmented value={s.weekStart} onChange={v => update({ weekStart: v })}
                   options={[{value:"monday",label:"Monday"},{value:"sunday",label:"Sunday"}]} />
      </FieldRow>

      <FieldRow label="Default job priority"
                hint="Applied when creating a new job from the queue or from an order.">
        <select className="select" value={s.defaultPriority}
                onChange={e => update({ defaultPriority: e.target.value })}>
          <option value="rush">Rush — top of queue</option>
          <option value="high">High</option>
          <option value="normal">Normal</option>
          <option value="low">Low / fill</option>
        </select>
      </FieldRow>

      <div className="row gap-2" style={{ marginTop: 20, justifyContent: "flex-end" }}>
        <button className="btn sm">Reset defaults</button>
        <button className="btn primary sm">{window.Icons.check} Save changes</button>
      </div>
    </div>
  );
}

function Segmented({ value, onChange, options }) {
  return (
    <div style={{
        display: "inline-flex",
        background: "var(--bg-1)",
        border: "1px solid var(--border-1)",
        borderRadius: 8,
        padding: 3,
        gap: 2,
        flexWrap: "wrap",
      }}>
      {options.map(o => (
        <button key={o.value}
                onClick={() => onChange(o.value)}
                style={{
                  padding: "6px 12px",
                  background: value === o.value ? "var(--bg-3)" : "transparent",
                  border: "1px solid",
                  borderColor: value === o.value ? "var(--border-2)" : "transparent",
                  borderRadius: 6,
                  color: value === o.value ? "var(--text-1)" : "var(--text-3)",
                  fontFamily: "inherit",
                  fontSize: 12.5,
                  cursor: "pointer",
                  whiteSpace: "nowrap",
                  fontWeight: value === o.value ? 500 : 400,
                }}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ========================================================================
// Tags — CRUD over window.TAGS, with usage counts derived from FILES
// ========================================================================

const TAG_COLOR_PALETTE = [
  "#60a5fa", "#67e8f9", "#94a3b8", "#a78bfa", "#f87171", "#f472b6",
  "#fbbf24", "#fb7185", "#a3e635", "#34d399", "#fb923c", "#e879f9",
  "#22d3ee", "#f0abfc", "#22c55e", "#38bdf8", "#a8a29e", "#475472",
];

function tagUsageCount(tagName) {
  return (window.FILES || []).filter(f => (f.tags || []).includes(tagName)).length;
}

function TagsPage() {
  // Single source of truth on window so other screens pick up the changes.
  const [, bump] = useState(0);
  const rerender = () => bump(v => v + 1);
  const tags = window.TAGS;

  const [filter, setFilter] = useState("all");      // all | <category>
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState(null); // open inline editor for this tag
  const [creating, setCreating] = useState(false);  // new-tag inline editor

  const categories = useMemo(() => {
    const set = new Set(tags.map(t => t.category).filter(Boolean));
    return ["all", ...Array.from(set)];
  }, [tags, /* eslint-disable-line */ bump]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tags.filter(t => {
      if (filter !== "all" && t.category !== filter) return false;
      if (!q) return true;
      return t.name.toLowerCase().includes(q) || (t.category || "").toLowerCase().includes(q);
    });
  }, [tags, filter, query, /* eslint-disable-line */ bump]);

  // Aggregate stats
  const totals = {
    total: tags.length,
    categories: new Set(tags.map(t => t.category).filter(Boolean)).size,
    inUse: tags.filter(t => tagUsageCount(t.name) > 0).length,
    orphan: tags.filter(t => tagUsageCount(t.name) === 0).length,
  };

  function deleteTag(id) {
    const tag = tags.find(t => t.id === id);
    if (!tag) return;
    const use = tagUsageCount(tag.name);
    const msg = use === 0
      ? `Delete the "${tag.name}" tag?`
      : `"${tag.name}" is used on ${use} file${use===1?"":"s"}. Delete the tag and remove it from those items?`;
    if (!confirm(msg)) return;
    const idx = tags.findIndex(t => t.id === id);
    if (idx >= 0) tags.splice(idx, 1);
    // Cascade: remove from FILES.tags
    (window.FILES || []).forEach(f => {
      if (f.tags) f.tags = f.tags.filter(name => name !== tag.name);
    });
    if (editingId === id) setEditingId(null);
    rerender();
  }

  function saveTag(id, patch) {
    const tag = tags.find(t => t.id === id);
    if (!tag) return;
    const oldName = tag.name;
    Object.assign(tag, patch);
    // If rename, propagate to FILES
    if (patch.name && patch.name !== oldName) {
      (window.FILES || []).forEach(f => {
        if (f.tags) f.tags = f.tags.map(n => n === oldName ? patch.name : n);
      });
    }
    setEditingId(null);
    rerender();
  }

  function createTag(draft) {
    if (!draft.name?.trim()) return;
    // Avoid dup names (case-insensitive)
    const dup = tags.find(t => t.name.toLowerCase() === draft.name.trim().toLowerCase());
    if (dup) { alert(`A tag called "${dup.name}" already exists.`); return; }
    tags.push({
      id: `tag-${draft.name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${Math.random().toString(36).slice(2,5)}`,
      name: draft.name.trim(),
      color: draft.color || TAG_COLOR_PALETTE[tags.length % TAG_COLOR_PALETTE.length],
      category: draft.category?.trim() || "Custom",
    });
    setCreating(false);
    rerender();
  }

  return (
    <div className="col gap-3">
      <div className="card" style={{ padding: 28 }}>
        <PageHeader
          title="Tags"
          sub="Labels you can attach to files, jobs, and orders. Tags are shared across the app — renaming one updates everywhere it's used."
        />

        {/* Summary stats */}
        <div className="row gap-3" style={{ marginBottom: 18, flexWrap: "wrap" }}>
          <TagStat label="Total tags"   value={totals.total} />
          <TagStat label="Categories"   value={totals.categories} />
          <TagStat label="In use"       value={totals.inUse}
                                        sub={`${totals.total > 0 ? Math.round(totals.inUse/totals.total*100) : 0}% of all tags`} />
          <TagStat label="Unused"       value={totals.orphan}
                                        tone={totals.orphan > 0 ? "warn" : "idle"}
                                        sub={totals.orphan === 0 ? "" : "Candidates to delete"} />
        </div>

        {/* Filter chips + search + new */}
        <div className="row gap-2" style={{ marginBottom: 14, flexWrap: "wrap" }}>
          <div className="row gap-2" style={{ flex: 1, minWidth: 280 }}>
            <div style={{
                flex: 1,
                display: "flex", alignItems: "center", gap: 8,
                padding: "8px 12px", background:"var(--bg-1)",
                border: "1px solid var(--border-1)", borderRadius: 8,
              }}>
              {React.cloneElement(window.Icons.search, { size: 14 })}
              <input value={query}
                     onChange={e => setQuery(e.target.value)}
                     placeholder="Search tags…"
                     style={{
                       flex: 1, background: "transparent", border: "none",
                       color: "var(--text-1)", outline: "none",
                       fontSize: 13, fontFamily: "var(--font-sans)"
                     }}/>
              {query && (
                <button className="btn ghost icon sm" onClick={() => setQuery("")}>
                  {window.Icons.x}
                </button>
              )}
            </div>
          </div>
          <button className="btn primary sm" onClick={() => setCreating(true)}>
            {window.Icons.plus} New tag
          </button>
        </div>

        {/* Category filter */}
        <div className="row gap-2" style={{ marginBottom: 14, flexWrap: "wrap" }}>
          {categories.map(c => (
            <button key={c}
                    onClick={() => setFilter(c)}
                    className={`btn sm ${filter === c ? "primary" : ""}`}
                    style={filter === c ? null : { background:"transparent", borderColor:"var(--border-1)" }}>
              {c === "all" ? "All" : c}
              <span className="num muted" style={{ marginLeft: 6, fontSize: 11 }}>
                {c === "all" ? tags.length : tags.filter(t => t.category === c).length}
              </span>
            </button>
          ))}
        </div>

        {/* Tag list */}
        <div style={{
            border: "1px solid var(--border-1)",
            borderRadius: 8,
            overflow: "hidden",
            background: "var(--bg-1)",
          }}>
          {/* Header */}
          <div className="row gap-3" style={{
              padding: "10px 16px",
              borderBottom: "1px solid var(--border-1)",
              color: "var(--text-4)",
              fontSize: 10.5,
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              fontWeight: 500,
              background: "var(--bg-2)",
            }}>
            <div style={{ width: 4, flexShrink: 0 }}/>
            <div style={{ flex: 1 }}>Tag</div>
            <div style={{ width: 140, flexShrink: 0 }}>Category</div>
            <div style={{ width: 80, flexShrink: 0, textAlign: "right" }}>Usage</div>
            <div style={{ width: 110, flexShrink: 0, textAlign: "right" }}>Actions</div>
          </div>

          {/* Inline create row (always at top when active) */}
          {creating && (
            <TagEditorRow
              isNew
              initial={{ name: "", color: TAG_COLOR_PALETTE[0], category: filter === "all" ? "Custom" : filter }}
              onSave={createTag}
              onCancel={() => setCreating(false)}
            />
          )}

          {filtered.length === 0 && !creating && (
            <div className="col" style={{ alignItems: "center", padding: "40px 20px", color: "var(--text-3)" }}>
              <div className="small">No tags match.</div>
            </div>
          )}

          {filtered.map(tag => (
            editingId === tag.id ? (
              <TagEditorRow
                key={tag.id}
                initial={tag}
                onSave={(patch) => saveTag(tag.id, patch)}
                onCancel={() => setEditingId(null)}
              />
            ) : (
              <TagRow
                key={tag.id}
                tag={tag}
                usage={tagUsageCount(tag.name)}
                onEdit={() => setEditingId(tag.id)}
                onDelete={() => deleteTag(tag.id)}
              />
            )
          ))}
        </div>
      </div>
    </div>
  );
}

function TagStat({ label, value, sub, tone }) {
  const color = tone === "warn" ? "var(--warn)" : tone === "idle" ? "var(--text-3)" : "var(--text-1)";
  return (
    <div style={{
        flex: "1 1 0", minWidth: 140,
        padding: "14px 16px",
        background: "var(--bg-1)",
        border: "1px solid var(--border-1)",
        borderRadius: 10,
      }}>
      <div className="tag-key">{label}</div>
      <div className="num" style={{ fontSize: 22, fontWeight: 600, color, marginTop: 6, letterSpacing: "-0.02em" }}>
        {value}
      </div>
      {sub && <div className="tiny muted" style={{ marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

function TagRow({ tag, usage, onEdit, onDelete }) {
  return (
    <div className="row gap-3" style={{
        padding: "10px 16px",
        borderBottom: "1px solid var(--border-1)",
        alignItems: "center",
        transition: "background 80ms",
      }}
      onMouseEnter={(e) => e.currentTarget.style.background = "var(--bg-2)"}
      onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}>
      <span style={{
        width: 4, height: 28, borderRadius: 2, flexShrink: 0,
        background: tag.color,
      }}/>
      <TagChip tag={tag} />
      <div className="col" style={{ flex: 1, minWidth: 0 }} />
      <div style={{ width: 140, flexShrink: 0 }}>
        <span className="tiny muted">{tag.category || "—"}</span>
      </div>
      <div style={{ width: 80, flexShrink: 0, textAlign: "right" }}>
        {usage > 0 ? (
          <span className="num small" style={{ color: "var(--text-1)" }}>{usage}</span>
        ) : (
          <span className="tiny muted">unused</span>
        )}
      </div>
      <div style={{ width: 110, flexShrink: 0, display: "flex", justifyContent: "flex-end", gap: 4 }}>
        <button className="btn ghost icon sm" title="Rename" onClick={onEdit}>
          {React.cloneElement(window.Icons.settings, { size: 14 })}
        </button>
        <button className="btn ghost icon sm" title="Delete tag" onClick={onDelete}
                style={{ color: "var(--err)" }}>
          {window.Icons.trash}
        </button>
      </div>
    </div>
  );
}

function TagChip({ tag, large }) {
  return (
    <span style={{
        display: "inline-flex", alignItems: "center", gap: 8,
        padding: large ? "6px 14px" : "4px 10px",
        background: hexToRgba(tag.color, 0.12),
        border: `1px solid ${hexToRgba(tag.color, 0.30)}`,
        borderRadius: 999,
        color: tag.color,
        fontSize: large ? 13 : 12,
        fontWeight: 500,
        whiteSpace: "nowrap",
      }}>
      <span style={{
        width: 8, height: 8, borderRadius: "50%",
        background: tag.color, boxShadow: `0 0 6px ${tag.color}`,
      }}/>
      {tag.name}
    </span>
  );
}

function hexToRgba(hex, a) {
  if (!hex || !hex.startsWith("#")) return `rgba(148,163,184,${a})`;
  const v = parseInt(hex.replace("#",""), 16);
  const r = (v >> 16) & 0xff, g = (v >> 8) & 0xff, b = v & 0xff;
  return `rgba(${r},${g},${b},${a})`;
}

function TagEditorRow({ initial, isNew, onSave, onCancel }) {
  const [name, setName] = useState(initial.name || "");
  const [color, setColor] = useState(initial.color || TAG_COLOR_PALETTE[0]);
  const [category, setCategory] = useState(initial.category || "Custom");

  const previewTag = { name: name || "new tag", color, category };

  function submit() {
    if (!name.trim()) return;
    onSave({ name: name.trim(), color, category: category.trim() || "Custom" });
  }

  return (
    <div style={{
        padding: "14px 16px",
        borderBottom: "1px solid var(--border-1)",
        background: "var(--bg-2)",
      }}>
      <div className="row gap-3" style={{ alignItems: "flex-start" }}>
        <span style={{
          width: 4, height: 28, borderRadius: 2, flexShrink: 0,
          marginTop: 8,
          background: color,
        }}/>

        <div className="col" style={{ flex: 1, minWidth: 0, gap: 10 }}>
          {/* Row 1: name + category + preview */}
          <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr", gap: 10 }}>
            <div>
              <label className="label">Name</label>
              <input className="input"
                     autoFocus
                     value={name}
                     onChange={e => setName(e.target.value)}
                     onKeyDown={e => {
                       if (e.key === "Enter") submit();
                       if (e.key === "Escape") onCancel();
                     }}
                     placeholder="tag-name" />
            </div>
            <div>
              <label className="label">Category</label>
              <input className="input"
                     value={category}
                     onChange={e => setCategory(e.target.value)}
                     placeholder="Material, Use…" />
            </div>
            <div>
              <label className="label">Preview</label>
              <div style={{
                  padding: "8px 12px",
                  background: "var(--bg-1)",
                  border: "1px solid var(--border-1)",
                  borderRadius: 8,
                  display: "flex", alignItems: "center",
                  minHeight: 36,
                }}>
                <TagChip tag={previewTag} />
              </div>
            </div>
          </div>

          {/* Row 2: color palette */}
          <div>
            <label className="label">Color</label>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {TAG_COLOR_PALETTE.map(c => (
                <button key={c}
                        onClick={() => setColor(c)}
                        style={{
                          width: 24, height: 24, borderRadius: 6,
                          background: c,
                          border: `2px solid ${c === color ? "var(--text-1)" : "transparent"}`,
                          boxShadow: c === color ? `0 0 0 1px ${c}, 0 0 8px ${c}66` : "none",
                          cursor: "pointer",
                          padding: 0,
                        }}
                        title={c}/>
              ))}
            </div>
          </div>
        </div>

        <div className="col gap-2" style={{ flexShrink: 0, paddingTop: 22 }}>
          <button className="btn primary sm" onClick={submit} disabled={!name.trim()}>
            {window.Icons.check} {isNew ? "Create" : "Save"}
          </button>
          <button className="btn sm" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

// ========================================================================
// Notifications
// ========================================================================

function NotificationsPage() {
  const [s, set] = useState({ ...window.SETTINGS.notifications });
  const update = (patch) => { const next = { ...s, ...patch }; set(next); Object.assign(window.SETTINGS.notifications, patch); };
  return (
    <div className="card" style={{ padding: 28 }}>
      <PageHeader title="Notifications"
                  sub="Choose which events trigger an alert. All notifications stay local — nothing is sent to anyone else." />

      <div style={{ marginBottom: 4, fontSize: 11, color: "var(--text-4)", textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500 }}>
        Events
      </div>

      <FieldRow label="Job completed"
                hint="Notify when any printer finishes a print successfully.">
        <Toggle checked={s.onJobComplete} onChange={v => update({ onJobComplete: v })} />
      </FieldRow>
      <FieldRow label="Job failed or paused"
                hint="Fires on detected layer-shift, filament runout, thermal runaway, or user-paused jobs that idle for more than 10 minutes.">
        <Toggle checked={s.onJobFailed} onChange={v => update({ onJobFailed: v })} />
      </FieldRow>
      <FieldRow label="Claim waiting"
                hint="When a queued job is waiting for a free printer that just became idle.">
        <Toggle checked={s.onClaimWaiting} onChange={v => update({ onClaimWaiting: v })} />
      </FieldRow>
      <FieldRow label="Printer goes idle"
                hint="Notify whenever any printer transitions to idle — useful for back-to-back batches.">
        <Toggle checked={s.onPrinterIdle} onChange={v => update({ onPrinterIdle: v })} />
      </FieldRow>
      <FieldRow label="Low spool"
                hint="Warn when an assigned filament drops below the threshold set in Print defaults.">
        <Toggle checked={s.onLowSpool} onChange={v => update({ onLowSpool: v })} />
      </FieldRow>

      <div style={{ marginTop: 24, marginBottom: 4, fontSize: 11, color: "var(--text-4)", textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500 }}>
        Delivery
      </div>
      <FieldRow label="Desktop notifications"
                hint="Show system notifications in the corner of the screen even when Themis is in a background tab.">
        <Toggle checked={s.desktopNotifications} onChange={v => update({ desktopNotifications: v })} />
      </FieldRow>
      <FieldRow label="Sound on alerts"
                hint="Plays a chime for failure-level events only.">
        <Toggle checked={s.soundOnAlerts} onChange={v => update({ soundOnAlerts: v })} />
      </FieldRow>
    </div>
  );
}

// ========================================================================
// Print defaults
// ========================================================================

function PrintDefaultsPage() {
  const [s, set] = useState({ ...window.SETTINGS.print });
  const update = (patch) => { const next = { ...s, ...patch }; set(next); Object.assign(window.SETTINGS.print, patch); };

  const allDryMaterials = ["PLA","PETG","PA-CF","ABS","ASA","PC","TPU"];
  function toggleDry(m) {
    const has = s.requireDryBefore.includes(m);
    update({ requireDryBefore: has ? s.requireDryBefore.filter(x => x !== m) : [...s.requireDryBefore, m] });
  }

  return (
    <div className="card" style={{ padding: 28 }}>
      <PageHeader title="Print defaults"
                  sub="Workshop-wide print behavior. Per-job overrides win when set during new-job intake." />

      <FieldRow label="Slice on claim"
                hint="When a printer claims a queued job, slice immediately using its configured profile rather than waiting for a manual slice.">
        <Toggle checked={s.sliceOnClaim} onChange={v => update({ sliceOnClaim: v })} />
      </FieldRow>
      <FieldRow label="Auto-start after slice"
                hint="Once slicing finishes, start the print without waiting for confirmation. Saves walking back to your desk — but can't be cancelled before the first layer.">
        <Toggle checked={s.autoStartAfterSlice} onChange={v => update({ autoStartAfterSlice: v })} />
      </FieldRow>
      <FieldRow label="Chamber pre-heat"
                hint="On chamber-heated printers (Centauri), pre-heat the chamber as part of the slice-on-claim step so the first layer lands at temp.">
        <Toggle checked={s.chamberPreheat} onChange={v => update({ chamberPreheat: v })} />
      </FieldRow>

      <FieldRow label="Require dry-before-print"
                hint="Block printing until the assigned spool has been through the dryer recently. Click materials to toggle.">
        <div className="row gap-2" style={{ flexWrap: "wrap" }}>
          {allDryMaterials.map(m => {
            const on = s.requireDryBefore.includes(m);
            return (
              <button key={m}
                      onClick={() => toggleDry(m)}
                      className={`btn sm ${on ? "primary" : ""}`}
                      style={on ? null : { background:"transparent", borderColor:"var(--border-1)" }}>
                {m}
              </button>
            );
          })}
        </div>
      </FieldRow>

      <FieldRow label="Low-spool threshold"
                hint="Warn when a spool drops below this percentage of its starting weight.">
        <div className="row gap-3" style={{ alignItems: "center" }}>
          <input type="range" min="5" max="50" step="5"
                 value={s.lowSpoolPercent}
                 onChange={e => update({ lowSpoolPercent: Number(e.target.value) })}
                 style={{ flex: 1 }} />
          <span className="num" style={{ minWidth: 48, color: "var(--text-1)", fontSize: 14, textAlign: "right" }}>
            {s.lowSpoolPercent}%
          </span>
        </div>
      </FieldRow>

      <FieldRow label="Cooldown wait after job"
                hint="Pause this many minutes between back-to-back claims for the same printer (lets the bed cool for clean part release).">
        <div className="row gap-2" style={{ alignItems: "center" }}>
          <input className="input num" type="number" min="0" max="60"
                 value={s.cooldownWaitMinutes}
                 onChange={e => update({ cooldownWaitMinutes: Number(e.target.value) })}
                 style={{ width: 80 }}/>
          <span className="small muted">minutes</span>
        </div>
      </FieldRow>
    </div>
  );
}

// ========================================================================
// Data & backup
// ========================================================================

function DataBackupPage() {
  const [s, set] = useState({ ...window.SETTINGS.data });
  const update = (patch) => { const next = { ...s, ...patch }; set(next); Object.assign(window.SETTINGS.data, patch); };
  return (
    <div className="card" style={{ padding: 28 }}>
      <PageHeader title="Data & backup"
                  sub="Where Themis keeps your library, and how to move it around." />

      <FieldRow label="Automatic backup"
                hint="Snapshot your library (orders, jobs, files, filaments, tags) to disk at the cadence below.">
        <Toggle checked={s.autoBackup} onChange={v => update({ autoBackup: v })} />
      </FieldRow>
      <FieldRow label="Backup frequency">
        <Segmented value={s.backupFrequency} onChange={v => update({ backupFrequency: v })}
                   options={[{value:"daily",label:"Daily"},{value:"weekly",label:"Weekly"},{value:"monthly",label:"Monthly"}]} />
      </FieldRow>
      <FieldRow label="Keep completed jobs"
                hint="How long to retain completed jobs and their slicer output before archiving.">
        <div className="row gap-2" style={{ alignItems: "center" }}>
          <input className="input num" type="number" min="7" max="365"
                 value={s.keepCompletedJobs}
                 onChange={e => update({ keepCompletedJobs: Number(e.target.value) })}
                 style={{ width: 80 }}/>
          <span className="small muted">days</span>
        </div>
      </FieldRow>

      <div style={{ marginTop: 28, paddingTop: 20, borderTop: "1px solid var(--border-1)" }}>
        <div style={{ fontSize: 12, color: "var(--text-4)", textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500, marginBottom: 14 }}>
          Manual actions
        </div>
        <div className="row gap-2" style={{ flexWrap: "wrap" }}>
          <button className="btn sm">{window.Icons.upload} Export library (.json)</button>
          <button className="btn sm">{window.Icons.upload} Export jobs (.csv)</button>
          <button className="btn sm">{window.Icons.copy} Import library…</button>
          <button className="btn ghost sm" style={{ color: "var(--err)" }}>
            {window.Icons.trash} Clear completed jobs
          </button>
        </div>
        <div className="tiny muted" style={{ marginTop: 14, lineHeight: 1.5, maxWidth: 540 }}>
          Library exports include all orders, jobs, files, filament profiles, process presets, tags, and your settings.
          Imports merge into existing data — duplicates are reconciled by id.
        </div>
      </div>
    </div>
  );
}

// ========================================================================
// Spoolman integration
// ========================================================================

function SpoolmanPage() {
  const [s, set] = useState({ ...window.SETTINGS.spoolman });
  const update = (patch) => {
    const next = { ...s, ...patch };
    set(next);
    Object.assign(window.SETTINGS.spoolman, patch);
  };

  // Mock connection lifecycle so the UI feels real.
  const [testing, setTesting] = useState(false);
  function testConnection() {
    if (!s.url.trim()) return;
    setTesting(true);
    update({ connectionStatus: "connecting" });
    // Fake handshake — succeed if URL looks plausible.
    setTimeout(() => {
      const ok = /^https?:\/\//i.test(s.url) && s.url.length > 10;
      update({
        connectionStatus: ok ? "connected" : "error",
        lastSyncedAt: ok ? new Date().toISOString() : s.lastSyncedAt,
      });
      setTesting(false);
    }, 1100);
  }

  function disconnect() {
    update({ enabled: false, connectionStatus: "disconnected", lastSyncedAt: null });
  }

  function syncNow() {
    update({ connectionStatus: "connecting" });
    setTimeout(() => {
      update({ connectionStatus: "connected", lastSyncedAt: new Date().toISOString() });
    }, 900);
  }

  // Pretend stats — would come from Spoolman in reality.
  const mockStats = useMemo(() => ({
    spools: 27,
    materials: 14,
    vendors: 7,
    lowSpools: 3,
  }), [s.connectionStatus, s.lastSyncedAt]);

  const isConnected = s.connectionStatus === "connected";

  return (
    <div className="col gap-3">
      <div className="card" style={{ padding: 28 }}>
        <div className="row between" style={{ marginBottom: 18, alignItems: "flex-start" }}>
          <div className="row gap-3" style={{ alignItems: "center" }}>
            <SpoolmanMark />
            <div>
              <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600, letterSpacing: "-0.01em" }}>Spoolman</h2>
              <div className="muted small" style={{ marginTop: 4, maxWidth: 540, lineHeight: 1.5 }}>
                Open-source filament & spool tracker. Themis can pull your spool inventory and push usage automatically as jobs complete.
              </div>
            </div>
          </div>
          <ConnectionPill status={s.connectionStatus} />
        </div>

        {/* Master toggle */}
        <FieldRow label="Enable Spoolman sync"
                  hint="When off, Themis ignores Spoolman entirely. Local filament library still works.">
          <Toggle checked={s.enabled} onChange={v => update({ enabled: v })} />
        </FieldRow>

        {/* Connection: URL + API key */}
        <div style={{ opacity: s.enabled ? 1 : 0.5, pointerEvents: s.enabled ? "auto" : "none", transition: "opacity 120ms" }}>
          <FieldRow label="Server URL"
                    hint="The address your Spoolman instance listens on. Common defaults: http://spoolman.local:7912 or http://<host>:7912.">
            <div className="row gap-2">
              <input className="input"
                     value={s.url}
                     onChange={e => update({ url: e.target.value, connectionStatus: "disconnected" })}
                     placeholder="http://spoolman.local:7912"
                     style={{ flex: 1 }} />
            </div>
          </FieldRow>

          <FieldRow label="API key"
                    hint="Optional. Required only if you've enabled the Spoolman API authentication option. Stored locally — never sent anywhere else.">
            <div className="row gap-2">
              <SecretInput value={s.apiKey} onChange={v => update({ apiKey: v, connectionStatus: s.connectionStatus === "connected" ? "disconnected" : s.connectionStatus })} />
            </div>
          </FieldRow>

          <div style={{ padding: "16px 0", borderBottom: "1px solid var(--border-1)", display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn primary sm"
                    disabled={!s.url.trim() || testing}
                    onClick={testConnection}>
              {testing
                ? <><Spinner size={12} /> Connecting…</>
                : <>{window.Icons.link} Test connection</>}
            </button>
            {isConnected && (
              <>
                <button className="btn sm" onClick={syncNow} disabled={testing}>
                  {window.Icons.refresh} Sync now
                </button>
                <button className="btn ghost sm" onClick={disconnect} style={{ color: "var(--err)" }}>
                  Disconnect
                </button>
              </>
            )}
          </div>

          {/* Inventory snapshot — shown only when connected */}
          {isConnected && (
            <div style={{ padding: "20px 0", borderBottom: "1px solid var(--border-1)" }}>
              <div className="row between" style={{ alignItems: "baseline", marginBottom: 12 }}>
                <div>
                  <div style={{ fontSize: 12, color: "var(--text-4)", textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500 }}>
                    Inventory snapshot
                  </div>
                  <div className="tiny muted" style={{ marginTop: 4 }}>
                    Last synced {s.lastSyncedAt ? formatRelativeTime(s.lastSyncedAt) : "never"}.
                  </div>
                </div>
              </div>
              <div className="row gap-3" style={{ flexWrap: "wrap" }}>
                <SpoolStat label="Spools"     value={mockStats.spools} />
                <SpoolStat label="Materials"  value={mockStats.materials} />
                <SpoolStat label="Vendors"    value={mockStats.vendors} />
                <SpoolStat label="Low spools" value={mockStats.lowSpools}
                                              tone={mockStats.lowSpools > 0 ? "warn" : "idle"} />
              </div>
            </div>
          )}

          {/* Sync behavior */}
          <div style={{ marginTop: 24, marginBottom: 4, fontSize: 11, color: "var(--text-4)", textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500 }}>
            Sync behavior
          </div>

          <FieldRow label="Sync interval"
                    hint="How often Themis pulls fresh spool data from Spoolman. Per-event sync (below) is always on top of this.">
            <div className="row gap-3" style={{ alignItems: "center" }}>
              <input type="range" min="1" max="60" step="1"
                     value={s.syncInterval}
                     onChange={e => update({ syncInterval: Number(e.target.value) })}
                     style={{ flex: 1 }} />
              <span className="num" style={{ minWidth: 84, color: "var(--text-1)", fontSize: 14, textAlign: "right" }}>
                every {s.syncInterval}m
              </span>
            </div>
          </FieldRow>

          <FieldRow label="Push usage on job events"
                    hint="When a job completes, immediately tell Spoolman how many grams to deduct from the assigned spool — don't wait for the next interval.">
            <Toggle checked={s.syncOnEvents} onChange={v => update({ syncOnEvents: v })} />
          </FieldRow>

          <FieldRow label="Deduct grams from spools"
                    hint="If off, Themis reads from Spoolman but never writes weight changes back. Use for read-only mirroring.">
            <Toggle checked={s.deductFromSpoolman} onChange={v => update({ deductFromSpoolman: v })} />
          </FieldRow>

          <FieldRow label="Mirror vendor & material catalog"
                    hint="Keep Themis's manufacturer + material-type fields in sync with Spoolman's catalog. Disable if you'd rather curate manually.">
            <Toggle checked={s.pullVendorMaterials} onChange={v => update({ pullVendorMaterials: v })} />
          </FieldRow>

          <FieldRow label="Auto-create spools"
                    hint="If you add a new filament in Themis and Spoolman doesn't know about it, push a new spool record automatically.">
            <Toggle checked={s.autoCreateSpools} onChange={v => update({ autoCreateSpools: v })} />
          </FieldRow>

          {/* Field mapping */}
          <div style={{ marginTop: 24, marginBottom: 4, fontSize: 11, color: "var(--text-4)", textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500 }}>
            Field mapping
          </div>

          <FieldRow label="Location label"
                    hint="Tag every spool record this Themis instance writes with a location, so multi-workshop Spoolman setups stay tidy.">
            <input className="input"
                   value={s.syncLocation}
                   onChange={e => update({ syncLocation: e.target.value })}
                   placeholder="Workshop"
                   style={{ width: "100%" }} />
          </FieldRow>

          <FieldRow label="Send lot numbers"
                    hint="Include the manufacturer's lot/batch number in synced records when known.">
            <Toggle checked={s.syncLot} onChange={v => update({ syncLot: v })} />
          </FieldRow>
        </div>

        {/* Setup help footer */}
        <div style={{
            marginTop: 28, padding: "14px 16px",
            background: "var(--bg-1)",
            border: "1px solid var(--border-1)",
            borderRadius: 10,
            display: "flex", gap: 12, alignItems: "flex-start",
          }}>
          <div style={{ color: "var(--accent-hi)", paddingTop: 2 }}>
            {React.cloneElement(SettingsIcons.info, { size: 16 })}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="small" style={{ fontWeight: 500, color: "var(--text-1)" }}>
              First time setting up?
            </div>
            <div className="tiny muted" style={{ marginTop: 4, lineHeight: 1.5, maxWidth: 580 }}>
              Spoolman runs as a Docker container or a Python service. Once it's up, the web UI is at the same address as the API.
              Add the URL above, hit <strong>Test connection</strong>, then choose what you want Themis to sync.
            </div>
            <div className="row gap-2" style={{ marginTop: 10 }}>
              <a className="btn ghost sm" href="https://github.com/Donkie/Spoolman" target="_blank" rel="noreferrer">
                {React.cloneElement(window.Icons.external, { size: 12 })} Spoolman on GitHub
              </a>
              <a className="btn ghost sm" href="https://github.com/Donkie/Spoolman#installation" target="_blank" rel="noreferrer">
                {React.cloneElement(window.Icons.external, { size: 12 })} Installation guide
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function SpoolmanMark() {
  return (
    <div style={{
      width: 44, height: 44, borderRadius: 10,
      background: "linear-gradient(135deg, #f59e0b, #b45309)",
      border: "1px solid var(--border-2)",
      boxShadow: "0 0 16px rgba(245,158,11,0.25)",
      display: "grid", placeItems: "center",
      color: "white",
      flexShrink: 0,
    }}>
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="9" />
        <circle cx="12" cy="12" r="5" />
        <circle cx="12" cy="12" r="2" fill="currentColor" />
      </svg>
    </div>
  );
}

function ConnectionPill({ status }) {
  const map = {
    connected:    { label: "Connected",    tone: "ok"   },
    connecting:   { label: "Connecting…",  tone: "info" },
    error:        { label: "Can't reach server", tone: "err"  },
    disconnected: { label: "Not connected",tone: "idle" },
  };
  const { label, tone } = map[status] || map.disconnected;
  return <span className={`pill ${tone}`}><span className="dot" />{label}</span>;
}

function SecretInput({ value, onChange }) {
  const [show, setShow] = useState(false);
  return (
    <div style={{
        flex: 1, display: "flex", alignItems: "center", gap: 4,
        background: "var(--bg-1)",
        border: "1px solid var(--border-1)",
        borderRadius: 8,
        padding: "0 4px 0 12px",
      }}>
      <input
        type={show ? "text" : "password"}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder="Leave blank if auth is off"
        style={{
          flex: 1, background: "transparent", border: "none",
          color: "var(--text-1)", outline: "none",
          padding: "8px 0",
          fontSize: 13, fontFamily: value ? "var(--font-mono)" : "var(--font-sans)",
        }} />
      <button className="btn ghost icon sm" type="button" onClick={() => setShow(v => !v)} title={show ? "Hide" : "Show"}>
        {show
          ? React.cloneElement(window.Icons.x, { size: 14 })
          : React.cloneElement(window.Icons.search, { size: 14 })}
      </button>
    </div>
  );
}

function SpoolStat({ label, value, tone }) {
  const color = tone === "warn" ? "var(--warn)" : tone === "idle" ? "var(--text-3)" : "var(--text-1)";
  return (
    <div style={{
        flex: "1 1 0", minWidth: 110,
        padding: "12px 14px",
        background: "var(--bg-1)",
        border: "1px solid var(--border-1)",
        borderRadius: 10,
      }}>
      <div className="tag-key">{label}</div>
      <div className="num" style={{
          fontSize: 22, fontWeight: 600,
          color,
          marginTop: 4,
          letterSpacing: "-0.02em",
        }}>{value}</div>
    </div>
  );
}

function Spinner({ size = 14 }) {
  return (
    <span style={{
      width: size, height: size, borderRadius: "50%",
      border: "2px solid rgba(255,255,255,0.3)",
      borderTopColor: "white",
      display: "inline-block",
      animation: "spin 0.7s linear infinite",
      marginRight: 4,
    }}/>
  );
}

function formatRelativeTime(iso) {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const ms = now - then;
  if (ms < 60_000) return "just now";
  const m = Math.round(ms / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  return `${d}d ago`;
}

// ========================================================================
// About
// ========================================================================

function AboutPage() {
  return (
    <div className="card" style={{ padding: 28 }}>
      <PageHeader title="About Themis" />

      <div className="row gap-4" style={{ alignItems: "center", marginBottom: 24 }}>
        <div style={{
          width: 64, height: 64, borderRadius: 14,
          background: "linear-gradient(135deg, var(--accent-hi), var(--accent-lo))",
          boxShadow: "0 0 24px var(--accent-glow)",
          border: "1px solid var(--border-2)",
          display: "grid", placeItems: "center",
          color: "white", fontWeight: 700, fontSize: 24, letterSpacing: "-0.02em",
          fontFamily: "var(--font-sans)",
        }}>θ</div>
        <div className="col">
          <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: "-0.01em" }}>themis<span style={{ color: "var(--text-3)", fontWeight: 400 }}>.farm</span></div>
          <div className="small muted">Workshop print-farm manager · single-user build</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 16 }}>
        <AboutTile k="Version"      v="0.7.2" mono />
        <AboutTile k="Released"     v="2026-05-22" mono />
        <AboutTile k="Channel"      v="Stable" />
        <AboutTile k="Storage used" v="18.4 MB" mono />
      </div>

      <div style={{ marginTop: 28, paddingTop: 20, borderTop: "1px solid var(--border-1)" }}>
        <div style={{ fontSize: 12, color: "var(--text-4)", textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500, marginBottom: 12 }}>
          Links
        </div>
        <div className="col gap-2">
          {[
            ["Release notes",    "View what's new in each build"],
            ["Documentation",    "How everything works under the hood"],
            ["Report an issue",  "Send a bug or feature request"],
            ["Keyboard shortcuts", "Reference card"],
          ].map(([label, sub]) => (
            <a key={label} href="#" onClick={e => e.preventDefault()}
               style={{
                 display: "flex", alignItems: "center", justifyContent: "space-between",
                 padding: "10px 14px",
                 background: "var(--bg-1)",
                 border: "1px solid var(--border-1)",
                 borderRadius: 8,
                 textDecoration: "none",
                 color: "var(--text-1)",
               }}>
              <div className="col">
                <div className="small" style={{ fontWeight: 500 }}>{label}</div>
                <div className="tiny muted" style={{ marginTop: 2 }}>{sub}</div>
              </div>
              <span style={{ color: "var(--text-3)", display: "inline-flex" }}>
                {React.cloneElement(window.Icons.external, { size: 14 })}
              </span>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}

function AboutTile({ k, v, mono }) {
  return (
    <div style={{
        padding: "12px 14px",
        background: "var(--bg-1)",
        border: "1px solid var(--border-1)",
        borderRadius: 8,
      }}>
      <div className="tag-key">{k}</div>
      <div style={{
          marginTop: 4,
          fontSize: 14,
          fontFamily: mono ? "var(--font-mono)" : "var(--font-sans)",
          color: "var(--text-1)",
        }}>{v}</div>
    </div>
  );
}

// ========================================================================
// Tiny local icons that aren't in the main set
// ========================================================================

const SettingsIcons = {
  tag:      <window.Icon paths={["M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 12.9V3h9.9l7.7 7.7a2 2 0 0 1 0 2.7z","M7 7h.01"]} />,
  backup:   <window.Icon paths={["M21 12v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6","M21 12a9 9 0 0 0-15.36-6.36L3 8","M3 4v4h4","M12 8v8","M9 13l3 3 3-3"]} />,
  info:     <window.Icon paths={["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z","M12 16v-4","M12 8h.01"]} />,
  // Spoolman = filament reel, drawn as a side-view spool (two flanges + core)
  spoolman: <window.Icon paths={["M5 5h14","M5 19h14","M5 5v14","M19 5v14","M9 8h6","M9 16h6","M9 8v8","M15 8v8"]} />,
};

Object.assign(window, { SettingsScreen });
