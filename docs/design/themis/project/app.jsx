/* global React, ReactDOM, window */
const { useState, useEffect, useMemo } = React;

// =========================================================================
// Themis — app shell
// =========================================================================

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": "#f59e0b",
  "density": "balanced",
  "nav": "expanded",
  "queueCard": "rich",
  "printerCard": "grid"
}/*EDITMODE-END*/;

// Derive an accent palette from a single hex.
function deriveAccent(hex) {
  // brighten and darken (very approximate hsl push)
  function shade(h, amount) {
    const v = parseInt(h.replace("#",""), 16);
    let r = (v >> 16) & 0xff;
    let g = (v >> 8)  & 0xff;
    let b =  v        & 0xff;
    const factor = 1 + amount;
    r = Math.max(0, Math.min(255, Math.round(r * factor + (amount > 0 ? (255 - r) * amount * 0.5 : 0))));
    g = Math.max(0, Math.min(255, Math.round(g * factor + (amount > 0 ? (255 - g) * amount * 0.5 : 0))));
    b = Math.max(0, Math.min(255, Math.round(b * factor + (amount > 0 ? (255 - b) * amount * 0.5 : 0))));
    return `#${[r,g,b].map(n => n.toString(16).padStart(2,"0")).join("")}`;
  }
  function rgba(h, a) {
    const v = parseInt(h.replace("#",""), 16);
    const r = (v >> 16) & 0xff, g = (v >> 8) & 0xff, b = v & 0xff;
    return `rgba(${r},${g},${b},${a})`;
  }
  return {
    accent: hex,
    accentHi: shade(hex, 0.35),
    accentLo: shade(hex, -0.55),
    accentGlow: rgba(hex, 0.18),
  };
}

function App() {
  const [t, setTweak] = window.useTweaks(TWEAK_DEFAULTS);

  // Apply tokens at root
  useEffect(() => {
    const d = deriveAccent(t.accent);
    const r = document.documentElement;
    r.style.setProperty("--accent",      d.accent);
    r.style.setProperty("--accent-hi",   d.accentHi);
    r.style.setProperty("--accent-lo",   d.accentLo);
    r.style.setProperty("--accent-glow", d.accentGlow);
    r.setAttribute("data-density", t.density);
  }, [t.accent, t.density]);

  const [route, setRoute] = useState("queue");
  const [settingsPage, setSettingsPage] = useState("general");
  const queueCount = useMemo(() => window.JOBS.filter(j => j.status === "queued").length, []);
  const ordersOpen = useMemo(() => window.ORDERS.filter(o => o.status !== "complete").length, []);

  // Screen mapping
  const screens = {
    queue: {
      title: "Job queue",
      crumbs: ["Workshop"],
      actions: (
        <>
          <button className="btn sm">{window.Icons.refresh} Resync</button>
          <button className="btn primary sm" onClick={() => setRoute("new-job")}>{window.Icons.plus} New job</button>
        </>
      ),
      render: () => <window.QueueScreen tweaks={t} setRoute={setRoute} />,
    },
    "new-job": {
      title: "New job",
      crumbs: ["Workshop", "Job queue"],
      actions: null,
      render: () => <window.NewJobScreen onCancel={() => setRoute("queue")} onCreate={() => setRoute("queue")} />,
    },
    fleet: {
      title: "Fleet",
      crumbs: ["Workshop"],
      actions: <button className="btn sm">{window.Icons.refresh} Resync</button>,
      render: () => <window.FleetScreen tweaks={t} />,
    },
    orders: {
      title: "Orders",
      crumbs: ["Workshop"],
      actions: <button className="btn primary sm" onClick={() => setRoute("new-order")}>{window.Icons.plus} New order</button>,
      render: () => <window.OrdersScreen tweaks={t} />,
    },
    "new-order": {
      title: "New order",
      crumbs: ["Workshop", "Orders"],
      actions: null,
      render: () => <window.NewOrderScreen onCancel={() => setRoute("orders")} />,
    },
    files: {
      title: "Model library",
      crumbs: ["Workshop"],
      actions: <button className="btn primary sm">{window.Icons.upload} Upload</button>,
      render: () => <window.FilesScreen />,
    },
    filaments: {
      title: "Filament library",
      crumbs: ["Workshop"],
      actions: (
        <>
          <button className="btn sm">{window.Icons.refresh} Sync vendor prices</button>
          <button className="btn primary sm">{window.Icons.plus} Add filament</button>
        </>
      ),
      render: () => <window.FilamentsScreen tweaks={t} />,
    },
    printers: {
      title: "Printers",
      crumbs: ["Workshop"],
      actions: null,
      render: () => <window.PrintersScreen />,
    },
    settings: {
      title: "Settings",
      crumbs: [],
      actions: null,
      render: () => <window.SettingsScreen subroute={settingsPage} setSubroute={setSettingsPage} />,
    },
  };

  const screen = screens[route] || screens.queue;
  const sidebarRoute = route === "new-order" ? "orders" :
                       route === "new-job"   ? "queue"  : route;

  return (
    <div className="app" data-nav={t.nav}>
      <window.Sidebar
        route={sidebarRoute}
        setRoute={setRoute}
        nav={t.nav}
        queueCount={queueCount}
        ordersOpen={ordersOpen}
      />
      <div className="main">
        <window.Topbar title={screen.title} crumbs={screen.crumbs} actions={screen.actions} />
        <div className="content" data-density={t.density}>
          {screen.render()}
        </div>
      </div>

      <window.TweaksPanel title="Tweaks">
        <window.TweakSection label="Look">
          <window.TweakColor
            label="Accent"
            value={t.accent}
            options={["#3b82f6", "#06b6d4", "#6366f1", "#10b981", "#f59e0b"]}
            onChange={v => setTweak("accent", v)}
          />
        </window.TweakSection>

        <window.TweakSection label="Layout">
          <window.TweakRadio
            label="Density"
            value={t.density}
            options={[
              { value: "dense",    label: "Dense" },
              { value: "balanced", label: "Mid" },
              { value: "roomy",    label: "Roomy" },
            ]}
            onChange={v => setTweak("density", v)}
          />
          <window.TweakRadio
            label="Sidebar"
            value={t.nav}
            options={[
              { value: "collapsed", label: "Icons" },
              { value: "expanded",  label: "Labels" },
            ]}
            onChange={v => setTweak("nav", v)}
          />
        </window.TweakSection>

        <window.TweakSection label="Job queue cards">
          <window.TweakRadio
            label="Style"
            value={t.queueCard}
            options={[
              { value: "compact", label: "Compact" },
              { value: "rich",    label: "Rich" },
              { value: "strip",   label: "Strip" },
            ]}
            onChange={v => setTweak("queueCard", v)}
          />
        </window.TweakSection>

        <window.TweakSection label="Fleet layout">
          <window.TweakRadio
            label="Cards"
            value={t.printerCard}
            options={[
              { value: "grid",  label: "Grid" },
              { value: "list",  label: "List" },
              { value: "focus", label: "Focus" },
            ]}
            onChange={v => setTweak("printerCard", v)}
          />
        </window.TweakSection>
      </window.TweaksPanel>
    </div>
  );
}

window.App = App;
ReactDOM.createRoot(document.getElementById("root")).render(<App />);
