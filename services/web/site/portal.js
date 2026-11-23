// Platform portal: one read-only page per department, built from the published snapshot
// (/api/portal), the requirements register and the model card (/data/). No third-party
// code; every chart is plain SVG; all text goes in through textContent (never innerHTML).
"use strict";

(() => {
  const PAGES = ["predict", "overview", "requirements", "data", "analytics", "ml", "mlops",
    "monitoring", "devops", "architecture"];
  const SVG_NS = "http://www.w3.org/2000/svg";
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const SLICES = {
    distance_lt_2mi: "under 2 mi", distance_2_to_5mi: "2 to 5 mi", distance_5_to_10mi: "5 to 10 mi",
    distance_ge_10mi: "10 mi and over", weekday_am_peak: "weekday 7 to 10 am", weekday_pm_peak: "weekday 4 to 8 pm",
    night_0_to_5: "night 0 to 6 am", weekend: "weekend", airport: "airport trips",
  };
  const state = { snapshot: null, requirements: null, modelCard: null, zones: new Map(), rendered: new Set() };

  // ---------- tiny DOM helpers ----------
  function h(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else node.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }
  function s(tag, attrs = {}, ...children) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) node.setAttribute(k, String(v));
    for (const c of children.flat()) {
      if (c === null || c === undefined) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }
  const fmt = (n, digits = 0) =>
    n === null || n === undefined ? "–" : Number(n).toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  const pct = (n, digits = 1) => (n === null || n === undefined ? "–" : `${fmt(n * 100, digits)}%`);
  const zoneName = (id) => state.zones.get(id) || `Zone ${id}`;

  function card(title, ...body) {
    return h("article", { class: "card" }, title ? h("h3", {}, title) : null, ...body);
  }
  function pageHeader(kicker, title, lede) {
    return h("header", { class: "page-head" }, h("p", { class: "kicker" }, kicker), h("h1", {}, title),
      lede ? h("p", { class: "lede" }, lede) : null);
  }
  function chips(items) {
    return h("ul", { class: "chips" }, items.map((t) => h("li", {}, t)));
  }
  function bullets(items) {
    return h("ul", { class: "bullets" }, items.map((t) => h("li", {}, t)));
  }
  function kpi(label, value, note, tone) {
    return h("div", { class: `kpi ${tone || ""}` }, h("p", { class: "kpi-label" }, label),
      h("p", { class: "kpi-value" }, value), note ? h("p", { class: "kpi-note" }, note) : null);
  }
  function kpis(...tiles) {
    return h("div", { class: "kpis" }, tiles);
  }
  function pill(text, tone) {
    return h("span", { class: `pill ${tone}` }, text);
  }
  function unavailable(section) {
    const why = section && section.error ? ` (${section.error})` : "";
    return card(null, h("p", { class: "muted" }, `This section's source was unavailable when the snapshot was taken${why}.`));
  }
  function ok(section) {
    return section && section.status === "ok";
  }
  function internal(name) {
    return h("span", { class: "internal", title: "Private network only, not public" }, name, " ", h("small", {}, "internal"));
  }

  // ---------- charts (SVG) ----------
  // Horizontal bars: rows = [{label, value, note?, tone?}]
  function hbars(rows, { format = (v) => fmt(v), max, markers = [], labelWidth = 150 } = {}) {
    const rowH = 28;
    const width = 640;
    const plotX = labelWidth + 8;
    const longest = Math.max(...rows.map((r) => String(r.note ?? format(r.value)).length));
    const plotW = width - plotX - Math.min(240, 16 + 7.2 * longest);
    const top = markers.length ? 18 : 4;
    const height = top + rows.length * rowH + 4;
    const peak = max || Math.max(...rows.map((r) => r.value), ...markers.map((m) => m.value), 1e-9);
    const x = (v) => plotX + (Math.max(0, v) / peak) * plotW;
    const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img" });
    rows.forEach((r, i) => {
      const y = top + i * rowH;
      svg.append(
        s("text", { x: labelWidth, y: y + 18, class: "axis-label", "text-anchor": "end" }, r.label),
        s("rect", { x: plotX, y: y + 5, width: Math.max(1, x(r.value) - plotX), height: rowH - 10, rx: 3, class: `bar ${r.tone || ""}` }),
        s("text", { x: x(r.value) + 6, y: y + 18, class: "value-label" }, r.note ?? format(r.value)),
      );
    });
    for (const m of markers) {
      svg.append(
        s("line", { x1: x(m.value), x2: x(m.value), y1: top - 4, y2: height, class: `marker ${m.tone || ""}` }),
        s("text", { x: x(m.value), y: 11, class: "marker-label", "text-anchor": "middle" }, m.label),
      );
    }
    svg.prepend(s("title", {}, rows.map((r) => `${r.label}: ${r.note ?? format(r.value)}`).join("; ")));
    return h("div", { class: "chart-wrap" }, svg);
  }

  // Vertical columns over time: points = [{label, value, tone?}]
  function columns(points, { format = (v) => fmt(v), every = 7 } = {}) {
    const width = 720;
    const height = 220;
    const left = 48;
    const bottom = 26;
    const plotW = width - left - 8;
    const plotH = height - bottom - 10;
    const peak = Math.max(...points.map((p) => p.value), 1);
    const step = plotW / points.length;
    const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img" });
    for (const frac of [0, 0.5, 1]) {
      const y = 10 + plotH * (1 - frac);
      svg.append(
        s("line", { x1: left, x2: width - 8, y1: y, y2: y, class: "grid" }),
        s("text", { x: left - 6, y: y + 4, class: "axis-label", "text-anchor": "end" }, format(peak * frac)),
      );
    }
    points.forEach((p, i) => {
      const barH = (p.value / peak) * plotH;
      const x = left + i * step;
      const rect = s("rect", { x: x + step * 0.12, y: 10 + plotH - barH, width: step * 0.76, height: barH, class: `bar ${p.tone || ""}` },
        s("title", {}, `${p.label}: ${format(p.value)}`));
      svg.append(rect);
      if (i % every === 0) {
        svg.append(s("text", { x: x + step / 2, y: height - 8, class: "axis-label", "text-anchor": "middle" }, p.short || p.label));
      }
    });
    svg.prepend(s("title", {}, `Columns from ${points[0]?.label} to ${points.at(-1)?.label}`));
    return h("div", { class: "chart-wrap" }, svg);
  }

  // Weekday x hour heatmap: cells = [{dow 1..7, hour 0..23, value}]
  function heatmap(cells, { format = (v) => fmt(v, 2), title = "" } = {}) {
    const cw = 26;
    const ch = 24;
    const left = 40;
    const top = 18;
    const width = left + 24 * cw + 4;
    const height = top + 7 * ch + 4;
    const values = cells.map((c) => c.value).filter((v) => v !== null);
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart heat", role: "img" }, s("title", {}, title));
    for (let hr = 0; hr < 24; hr += 3) {
      svg.append(s("text", { x: left + hr * cw + cw / 2, y: 12, class: "axis-label", "text-anchor": "middle" }, `${hr}h`));
    }
    DAYS.forEach((d, i) => svg.append(s("text", { x: left - 6, y: top + i * ch + 16, class: "axis-label", "text-anchor": "end" }, d)));
    for (const c of cells) {
      if (c.value === null) continue;
      const t = hi > lo ? (c.value - lo) / (hi - lo) : 0.5;
      svg.append(s("rect", {
        x: left + c.hour * cw + 1, y: top + (c.dow - 1) * ch + 1, width: cw - 2, height: ch - 2, rx: 3,
        class: "heat-cell", "fill-opacity": (0.12 + 0.88 * t).toFixed(3),
      }, s("title", {}, `${DAYS[c.dow - 1]} ${c.hour}:00 · ${format(c.value)}`)));
    }
    const legend = h("p", { class: "legend" }, h("span", { class: "swatch low" }), ` ${format(lo)}  `,
      h("span", { class: "swatch high" }), ` ${format(hi)}`);
    return h("div", {}, h("div", { class: "chart-wrap scroll" }, svg), legend);
  }

  // Stages left to right: [{label, value, note}]
  function funnel(stages) {
    const first = stages[0].value || 1;
    return h("ol", { class: "funnel" }, stages.map((st, i) => h("li", {},
      h("p", { class: "funnel-label" }, st.label),
      h("p", { class: "funnel-value" }, fmt(st.value)),
      h("div", { class: "funnel-bar" }, h("span", { class: "funnel-fill", "data-w": (st.value / first).toFixed(4) })),
      h("p", { class: "funnel-note" }, i === 0 ? "as delivered" : `${pct(st.value / first)} kept · −${fmt(stages[i - 1].value - st.value)}`),
    )));
  }
  function sizeFunnels(root) {
    // Bar widths are set through the CSSOM (allowed by the strict CSP, unlike style="").
    for (const el of root.querySelectorAll(".funnel-fill")) el.style.width = `${Number(el.dataset.w) * 100}%`;
  }

  // ---------- tiny markdown (headings, lists, tables, bold, code) ----------
  function inline(text) {
    const out = [];
    const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let last = 0;
    for (const m of text.matchAll(re)) {
      if (m.index > last) out.push(text.slice(last, m.index));
      const t = m[0];
      out.push(t.startsWith("**") ? h("strong", {}, t.slice(2, -2)) : h("code", {}, t.slice(1, -1)));
      last = m.index + t.length;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }
  function markdown(src) {
    const root = h("div", { class: "md" });
    const lines = src.split("\n");
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i += 1; continue; }
      const head = line.match(/^(#{1,3}) (.*)$/);
      if (head) { root.append(h(`h${head[1].length + 1}`, {}, inline(head[2]))); i += 1; continue; }
      if (line.startsWith("|")) {
        const rows = [];
        while (i < lines.length && lines[i].startsWith("|")) { rows.push(lines[i]); i += 1; }
        const cells = (r) => r.slice(1, -1).split("|").map((c) => c.trim());
        const body = rows.filter((r) => !/^\|[-|\s]+\|$/.test(r));
        const [first, ...rest] = body;
        const firstCells = cells(first);
        const table = h("table", {});
        if (firstCells.some(Boolean)) table.append(h("thead", {}, h("tr", {}, firstCells.map((c) => h("th", {}, inline(c))))));
        table.append(h("tbody", {}, rest.map((r) => h("tr", {}, cells(r).map((c) => h("td", {}, inline(c)))))));
        root.append(h("div", { class: "table-wrap" }, table));
        continue;
      }
      if (/^\s*- /.test(line)) {
        const top = h("ul", {});
        let current = null;
        while (i < lines.length && /^\s*- /.test(lines[i])) {
          const nested = /^\s{2,}- /.test(lines[i]);
          const item = h("li", {}, inline(lines[i].replace(/^\s*- /, "")));
          if (nested && current) {
            let sub = current.querySelector("ul");
            if (!sub) { sub = h("ul", {}); current.append(sub); }
            sub.append(item);
          } else { top.append(item); current = item; }
          i += 1;
        }
        root.append(top);
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(#|\||\s*- )/.test(lines[i])) { para.push(lines[i]); i += 1; }
      root.append(h("p", {}, inline(para.join(" "))));
    }
    return root;
  }

  // ---------- pages ----------
  const render = {
    overview(page, snap) {
      const ml = snap.ml || {};
      const mon = (snap.monitoring || {}).report || {};
      const de = snap.data_engineering || {};
      const ci = ((snap.devops || {}).workflows || {}).ci || [];
      const champion = ml.champion || {};
      const gain = champion.test_rmse && ml.baseline_test_rmse ? 1 - champion.test_rmse / ml.baseline_test_rmse : null;
      const lastMonth = (de.funnel || []).at(-1) || {};
      const reqs = state.requirements;
      page.append(
        pageHeader("Owner's view", "The whole platform on one page",
          "Every department's live numbers, refreshed by the monthly pipeline run. Pick a department for the detail."),
        kpis(
          kpi("Champion model", champion.version ? `v${champion.version} · ${champion.model_family}` : "–", `trained on ${champion.trained_on || "–"}`),
          kpi("Test RMSE", champion.test_rmse ? `${fmt(champion.test_rmse, 2)} min` : "–", gain !== null ? `${pct(gain, 0)} better than baseline` : null, "good"),
          kpi("Trips in gold", fmt(lastMonth.gold), lastMonth.month ? `month ${lastMonth.month}` : null),
          kpi("Drift", mon.retrain_recommended ? "Retrain advised" : (mon.target_level || "–"),
            mon.current_month ? `${mon.reference_month} → ${mon.current_month}` : null, mon.retrain_recommended ? "warn" : "good"),
          kpi("Latest CI", ci[0] ? ci[0].status : "–", ci[0] ? `commit ${ci[0].commit}` : null, ci[0] && ci[0].status === "success" ? "good" : "warn"),
          kpi("Requirements traced", reqs ? `${reqs.functional.length + reqs.non_functional.length}` : "–", "each linked to code and a test"),
        ),
        h("h2", {}, "Departments"),
        h("div", { class: "grid" },
          dept("requirements", "Requirements", "Product", reqs ? `${reqs.functional.length} functional, ${reqs.non_functional.length} non-functional, all traced` : "Register of what the platform must do"),
          dept("data", "Data Engineering", "Data Platform", ok(de) ? `${(de.funnel || []).length} months loaded · ${(de.quality_rules || []).length} quality rules enforced` : "Lakehouse ingestion and quality"),
          dept("analytics", "Analytics / BI", "Analytics", ok(snap.analytics) ? `Demand and pace for ${snap.analytics.latest_month}` : "BI marts on gold"),
          dept("ml", "Data Science", "ML", champion.test_rmse ? `${(ml.candidates || []).length} registered models · champion RMSE ${fmt(champion.test_rmse, 2)}` : "Models and evaluation"),
          dept("mlops", "MLOps", "ML Platform", ok(snap.mlops) ? `Aliases: ${Object.entries(snap.mlops.aliases || {}).map(([a, v]) => `@${a}=v${v}`).join(", ")}` : "Registry, gate, schedule"),
          dept("monitoring", "Evaluation & Monitoring", "ML Platform", mon.current_month ? `RMSE ${mon.rmse_change_pct > 0 ? "+" : ""}${fmt(mon.rmse_change_pct, 1)}% on ${mon.current_month}` : "Drift and live performance"),
          dept("devops", "DevOps / CI-CD", "Platform", ci.length ? `${ci.filter((r) => r.status === "success").length}/${ci.length} recent CI runs green` : "Pipelines and releases"),
          dept("architecture", "Architecture", "All", "How the pieces fit together"),
        ),
      );
    },

    requirements(page) {
      const reqs = state.requirements;
      page.append(pageHeader("1 · Product", "Requirements", reqs ? reqs.goal : null));
      if (!reqs) { page.append(unavailable()); return; }
      const owners = [...new Set(reqs.functional.map((r) => r.owner))];
      page.append(
        kpis(
          kpi("Functional", String(reqs.functional.length), "what it does"),
          kpi("Non-functional", String(reqs.non_functional.length), "how well it does it"),
          kpi("Owners", String(owners.length), owners.join(", ")),
          kpi("Traceability", "100%", "CI fails if any link breaks", "good"),
        ),
        card("Functional requirements", reqTable(reqs.functional, "owner")),
        card("Non-functional requirements", reqTable(reqs.non_functional, "category")),
        card("How traceability is enforced", bullets([
          "Every requirement names the code that implements it and the test that proves it (path::test).",
          "tests/test_requirements.py checks that each path and test exists, so a rename breaks the build.",
          "This page reads the same register (docs/requirements.yaml) through a generated copy kept in sync by a test.",
        ])),
      );
    },

    data(page, snap) {
      const de = snap.data_engineering;
      page.append(pageHeader("2 · Data Platform", "Data Engineering",
        "Monthly TLC files land in a medallion lakehouse on Databricks: bronze keeps the raw rows, silver applies the quality rules, gold is the contract-enforced feature table."));
      if (!ok(de)) { page.append(unavailable(de)); return; }
      const months = de.funnel || [];
      const latest = months.at(-1) || {};
      const totalFailed = (de.quality_rules || []).reduce((a, r) => a + r.failed, 0);
      page.append(
        kpis(
          kpi("Months loaded", String(months.length), months.map((m) => m.month).join(", ")),
          kpi("Bronze rows (latest)", fmt(latest.bronze), latest.month),
          kpi("Gold rows (latest)", fmt(latest.gold), latest.bronze ? `${pct(latest.gold / latest.bronze)} of bronze` : null, "good"),
          kpi("Rule violations caught", fmt(totalFailed), "last pipeline update", totalFailed ? "warn" : "good"),
        ),
      );
      for (const m of months.slice().reverse()) {
        page.append(card(`Row funnel · ${m.month}`, funnel([
          { label: "Bronze (raw)", value: m.bronze || 0 },
          { label: "Silver (clean)", value: m.silver || 0 },
          { label: "Gold (features)", value: m.gold || 0 },
        ])));
      }
      if ((de.quality_rules || []).length) {
        const rules = de.quality_rules.slice().sort((a, b) => b.failed - a.failed);
        page.append(card("Quality rules: rows rejected in the last update",
          hbars(rules.map((r) => ({ label: r.rule, value: r.failed, note: r.failed ? `${fmt(r.failed)} (${pct(r.failed / (r.passed + r.failed), 2)})` : "0 · clean", tone: r.failed ? "warn" : "good" })), { labelWidth: 200 }),
          h("p", { class: "muted" }, "Lakeflow expectations; drop rules remove the row, warn rules count it and keep it.")));
      }
      page.append(
        h("div", { class: "grid two" },
          card("Practices", bullets([
            "ELT: land raw first, transform inside the lakehouse",
            "Auto Loader ingests only new files (incremental, exactly once)",
            "Schema contract checked before upload; manifest with SHA-256 and row count",
            "Quality rules as code, unit-tested, enforced as expectations",
            "dbt contracts and tests on gold; marts reconcile with gold",
            "Raw data versioned with DVC; gold exported as content-addressed snapshots",
          ])),
          card("Tools", chips(de.tools || [])),
        ),
      );
    },

    analytics(page, snap) {
      const an = snap.analytics;
      page.append(pageHeader("3 · Analytics", "Analytics / BI dashboard",
        "Built on dbt marts over the gold table (bi_hourly_demand, bi_daily_trips, bi_pickup_zones), queried on the Databricks SQL warehouse."));
      if (!ok(an)) { page.append(unavailable(an)); return; }
      const hourly = an.hourly || [];
      const trips = hourly.reduce((a, c) => a + c.trips, 0);
      const avgDur = trips ? hourly.reduce((a, c) => a + c.trips * c.avg_duration_min, 0) / trips : null;
      const busiest = hourly.slice().sort((a, b) => b.trips - a.trips)[0];
      const slowest = hourly.filter((c) => c.trips > 500).sort((a, b) => b.pace_min_per_mile - a.pace_min_per_mile)[0];
      page.append(
        kpis(
          kpi("Trips", fmt(trips), an.latest_month),
          kpi("Average trip", avgDur ? `${fmt(avgDur, 1)} min` : "–"),
          kpi("Busiest hour", busiest ? `${DAYS[busiest.dow - 1]} ${busiest.hour}:00` : "–", busiest ? `${fmt(busiest.trips)} trips` : null),
          kpi("Slowest traffic", slowest ? `${DAYS[slowest.dow - 1]} ${slowest.hour}:00` : "–", slowest ? `${fmt(slowest.pace_min_per_mile, 1)} min per mile` : null, "warn"),
        ),
        card(`Traffic pace by weekday and hour · ${an.latest_month}`,
          h("p", { class: "muted" }, "Minutes per mile: darker means slower traffic."),
          heatmap(hourly.map((c) => ({ dow: c.dow, hour: c.hour, value: c.pace_min_per_mile })), { format: (v) => `${fmt(v, 2)} min/mi`, title: "Pace heatmap" })),
        card(`Demand by weekday and hour · ${an.latest_month}`,
          heatmap(hourly.map((c) => ({ dow: c.dow, hour: c.hour, value: c.trips })), { format: (v) => `${fmt(v)} trips`, title: "Demand heatmap" })),
        card("Trips per day",
          h("p", { class: "muted" }, "Weekends highlighted."),
          columns((an.daily || []).map((d) => ({ label: d.date, short: d.date.slice(5), value: d.trips, tone: d.weekend ? "alt" : "" })), { format: (v) => (v >= 1000 ? `${fmt(v / 1000)}k` : fmt(v)) })),
        card(`Top pickup zones · ${an.latest_month}`,
          hbars((an.top_pickup_zones || []).map((z) => ({ label: zoneName(z.zone_id), value: z.trips, note: `${fmt(z.trips)} · ${fmt(z.avg_duration_min, 1)} min avg`, tone: z.airport ? "alt" : "" })), { labelWidth: 210 }),
          h("p", { class: "muted" }, "Airports highlighted.")),
        card("Tools", chips(an.tools || [])),
      );
    },

    ml(page, snap) {
      const ml = snap.ml;
      page.append(pageHeader("4 · Data Science", "Models and evaluation",
        "Trip duration is predicted from what is known at pickup. Models are compared on the same held-out last week, against a naive baseline."));
      if (!ok(ml)) { page.append(unavailable(ml)); return; }
      const champ = ml.champion || {};
      const rows = [{ label: "Mean baseline", value: ml.baseline_test_rmse || 0, tone: "muted-bar" }]
        .concat((ml.candidates || []).map((c) => ({ label: `v${c.version} ${c.model_family}${c.aliases.length ? ` (@${c.aliases.join(", @")})` : ""}`, value: c.test_rmse || 0, tone: c.aliases.includes("champion") ? "good" : "" })));
      const slices = Object.entries(champ.slices || {}).map(([k, v]) => ({ label: SLICES[k] || k.replaceAll("_", " "), value: v }));
      page.append(
        kpis(
          kpi("Champion", champ.version ? `v${champ.version}` : "–", champ.model_family),
          kpi("Test RMSE", `${fmt(champ.test_rmse, 2)} min`, `baseline ${fmt(ml.baseline_test_rmse, 2)}`, "good"),
          kpi("Test MAE", `${fmt(champ.test_mae, 2)} min`),
          kpi("R²", fmt(champ.test_r2, 3)),
        ),
        card("Test RMSE by model (lower is better)", hbars(rows, { format: (v) => `${fmt(v, 2)} min`, labelWidth: 260 })),
        card("Champion error by slice", hbars(slices, { format: (v) => `${fmt(v, 2)} min`, markers: [{ value: champ.test_rmse, label: "overall" }], labelWidth: 170 }),
          h("p", { class: "muted" }, "The gate rejects a challenger if any slice gets more than 5% worse.")),
        h("div", { class: "grid two" },
          card("Features (known at pickup)", bullets(ml.features || [])),
          card("Practices", bullets([
            "Time-based split (train, validate, then the last 7 days for test), never random",
            "Leakage guard: fare, tip and payment are never features",
            "Optuna tuning on the validation week only",
            "One shared feature library for training and serving",
            "Every run logged to MLflow with its data snapshot hash",
          ])),
        ),
        card("Tools", chips(ml.tools || [])),
      );
      const mc = card("Model card");
      mc.append(state.modelCard ? markdown(state.modelCard) : h("p", { class: "muted" }, "Model card unavailable."));
      page.append(mc);
    },

    mlops(page, snap) {
      const ops = snap.mlops;
      page.append(pageHeader("6 · MLOps", "From data to a live model, automatically",
        "Each month Airflow ingests the new file, refreshes the lakehouse, snapshots gold, trains a challenger and lets a gate decide. The API picks up a new champion without a restart."));
      if (!ok(ops)) { page.append(unavailable(ops)); return; }
      page.append(
        card("Monthly loop", h("ol", { class: "steps" }, [
          "Fetch TLC month", "Upload to landing", "Lakeflow bronze → silver", "dbt gold + marts", "Export gold snapshot",
          "Train challenger", "Promotion gate", "Drift report", "Publish portal snapshot",
        ].map((t) => h("li", {}, t))), h("p", { class: "muted" }, ops.schedule)),
        card("Model registry", h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, ["Version", "Family", "Test RMSE", "Gate", "Aliases"].map((t) => h("th", {}, t)))),
          h("tbody", {}, (ops.registry || []).slice().reverse().map((r) => h("tr", {},
            h("td", {}, `v${r.version}`), h("td", {}, r.model_family || "–"), h("td", {}, fmt(r.test_rmse, 3)),
            h("td", {}, r.gate_result ? pill(r.gate_result, r.gate_result === "promoted" ? "good" : "warn") : "–"),
            h("td", {}, r.aliases.map((a) => pill(`@${a}`, a === "champion" ? "good" : "neutral"))),
          ))),
        ))),
        h("div", { class: "grid two" },
          card("Promotion gate", bullets(ops.gate_rules || [])),
          card("Serving", bullets([
            "The API serves models:/taxi-trip-duration@champion and re-checks the alias",
            "Model cached on disk: a cold start serves at once, even if MLflow sleeps",
            "Scale to zero: the API and MLflow sleep when idle and wake on the first request",
            "Rollback is moving an alias back to @previous",
          ])),
        ),
        card("Tools", chips(ops.tools || []), h("p", { class: "muted" }, "Airflow and MLflow UIs: ", internal("Airflow"), " ", internal("MLflow"))),
      );
    },

    monitoring(page, snap) {
      const mon = snap.monitoring;
      page.append(pageHeader("7 · Evaluation & Monitoring", "Is the model still right?",
        "When a new month arrives it is compared with the month before (PSI per feature and on the target) and the live champion is scored on it."));
      if (!mon || mon.status === "empty") { page.append(card(null, h("p", { class: "muted" }, "No drift report yet: it appears after the second month."))); return; }
      if (!ok(mon)) { page.append(unavailable(mon)); return; }
      const r = mon.report;
      const levelTone = (l) => ({ stable: "good", moderate: "warn", significant: "bad" })[l] || "";
      const psi = Object.entries(r.feature_psi).map(([k, v]) => ({ label: k.replaceAll("_", " "), value: v, tone: levelTone(r.feature_level[k]) }))
        .concat([{ label: "target: duration", value: r.target_psi, tone: levelTone(r.target_level) }]);
      page.append(
        kpis(
          kpi("Verdict", r.retrain_recommended ? "Retrain recommended" : "Healthy", `${r.reference_month} → ${r.current_month}`, r.retrain_recommended ? "warn" : "good"),
          kpi("Champion RMSE then", `${fmt(r.rmse_reference, 2)} min`, `v${r.model_version}, own test week`),
          kpi("Champion RMSE now", `${fmt(r.rmse_current, 2)} min`, `${r.rmse_change_pct > 0 ? "+" : ""}${fmt(r.rmse_change_pct, 1)}% on ${r.current_month}`, r.rmse_change_pct > 10 ? "warn" : ""),
          kpi("Mean trip", `${fmt(r.duration_mean_current, 1)} min`, `was ${fmt(r.duration_mean_reference, 1)}`),
        ),
        card("Population Stability Index by feature",
          hbars(psi, { format: (v) => fmt(v, 4), max: Math.max(0.3, ...psi.map((p) => p.value)), labelWidth: 170,
            markers: [{ value: 0.1, label: "0.1 moderate", tone: "warn" }, { value: 0.25, label: "0.25 significant", tone: "bad" }] }),
          h("p", { class: "muted" }, `Compared ${fmt(r.rows_reference)} trips in ${r.reference_month} with ${fmt(r.rows_current)} in ${r.current_month}.`)),
        h("div", { class: "grid two" },
          card("Rules", bullets([
            "PSI below 0.1 is stable, 0.1 to 0.25 is moderate, above 0.25 is significant",
            "Retraining is recommended on significant drift or RMSE more than 10% worse",
            "Every report is logged to MLflow (experiment taxi-monitoring)",
          ])),
          card("Reasons", r.reasons.length ? bullets(r.reasons) : h("p", { class: "muted" }, "Nothing crosses a threshold.")),
        ),
      );
    },

    devops(page, snap) {
      const dv = snap.devops;
      page.append(pageHeader("5 · DevOps", "CI/CD and releases",
        "Every change goes through local hooks, then CI. Images are built, scanned and pushed only from main; rollout on the server is an approved step."));
      if (!ok(dv)) { page.append(unavailable(dv)); return; }
      const runList = (runs) => h("ul", { class: "runs" }, runs.map((r) => h("li", {},
        pill(r.status, r.status === "success" ? "good" : r.status === "failure" ? "bad" : "neutral"),
        h("a", { href: r.url, rel: "noopener" }, r.commit), h("span", { class: "muted" }, new Date(r.when).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })),
      )));
      page.append(
        h("div", { class: "grid two" },
          card("CI (tests, scans, builds)", runList((dv.workflows || {}).ci || [])),
          card("CD (images to GHCR)", runList((dv.workflows || {}).cd || [])),
        ),
        card("Delivery pipeline", h("ol", { class: "steps" }, (dv.pipeline || []).map((t) => h("li", {}, t)))),
        h("div", { class: "grid two" },
          card("Everything is versioned", bullets(dv.versioning || [])),
          card("Housekeeping", bullets([
            "Weekly workflow keeps the newest 10 images per package in GHCR",
            "Server timer prunes unused images (running and previous are kept)",
            "make docker-clean frees space on a developer machine",
            "Daily OS security refresh in every image build",
          ])),
        ),
        card("Tools", chips(dv.tools || [])),
      );
    },

    architecture(page) {
      page.append(pageHeader("Architecture", "How the pieces fit together",
        "Development happens locally; the lakehouse runs on Databricks; everything else runs on one home server and sleeps when idle."));
      const lane = (title, items, tone) => h("div", { class: `lane ${tone || ""}` }, h("p", { class: "lane-title" }, title),
        h("ol", {}, items.map((t) => h("li", {}, t))));
      page.append(
        card("Data flow", h("div", { class: "lanes" },
          lane("Sources", ["NYC TLC monthly parquet", "Taxi zone lookup"]),
          lane("Lakehouse (Databricks)", ["Landing volume", "Bronze · Auto Loader", "Silver · quality rules", "Gold · dbt contract", "BI marts · dbt"], "accent"),
          lane("Versioned storage", ["DVC raw data", "Gold snapshots (SHA-256)", "S3-compatible store"]),
          lane("ML (MLflow)", ["Train challenger", "Promotion gate", "Registry aliases", "Drift reports"], "accent"),
          lane("Serving", ["FastAPI (model cache)", "nginx web + portal", "Public HTTPS tunnel"]),
        ), h("p", { class: "muted" }, "Airflow orchestrates the monthly loop end to end; Sablier wakes the API and MLflow on demand.")),
        h("div", { class: "grid two" },
          card("Boundaries", bullets([
            "The public site reaches only the API's allow-listed GET/POST routes",
            "Airflow and MLflow UIs stay on the private network",
            "The portal reads one published snapshot: no department's tools are exposed",
            "Secrets live in connections and environment, never in code",
          ])),
          card("Contracts between components", bullets([
            "TripFeatures (API input) shared by training and serving",
            "Gold table contract enforced by dbt",
            "Gold snapshot = immutable, content-addressed parquet",
            "Model = registry alias, not a file path",
          ])),
        ),
        card("Decisions", h("p", {}, "Each design choice is recorded as an ADR in docs/adr (Databricks Free Edition, scale-to-zero, the portal snapshot and more).")),
      );
    },
  };

  function dept(hash, title, owner, line) {
    return h("a", { class: "dept", href: `#${hash}` }, h("p", { class: "kicker" }, owner), h("h3", {}, title), h("p", {}, line), h("span", { class: "go", "aria-hidden": "true" }, "→"));
  }

  function reqTable(rows, extra) {
    return h("div", { class: "req-list" }, rows.map((r) => h("details", {},
      h("summary", {}, h("span", { class: "req-id" }, r.id), h("span", { class: "req-title" }, r.title), h("span", { class: "req-owner" }, r[extra])),
      h("p", {}, r.detail),
      r.measured ? h("p", {}, h("strong", {}, "Measured: "), r.measured) : null,
      r.implemented_in ? h("p", { class: "muted" }, "Implemented in: ", r.implemented_in.map((p) => h("code", {}, p)).reduce((a, c) => (a.length ? [...a, ", ", c] : [c]), [])) : null,
      h("p", { class: "muted" }, "Evidence: ", r.evidence.map((p) => h("code", {}, p)).reduce((a, c) => (a.length ? [...a, ", ", c] : [c]), [])),
    )));
  }

  // ---------- router ----------
  function show(name) {
    const target = PAGES.includes(name) ? name : "predict";
    for (const p of PAGES) document.getElementById(`page-${p}`).hidden = p !== target;
    for (const a of document.querySelectorAll("#nav a")) {
      if (a.getAttribute("href") === `#${target}`) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    const page = document.getElementById(`page-${target}`);
    document.title = target === "predict" ? "NYC Taxi Trip Time" : `${page.dataset.title} · NYC Taxi Trip Time`;
    if (target !== "predict" && !state.rendered.has(target)) fill(target, page);
    // Keep the active tab visible in the scrollable nav without scrolling the page.
    const nav = document.getElementById("nav");
    const active = nav.querySelector(`a[href="#${target}"]`);
    if (active) {
      const left = active.offsetLeft - nav.offsetLeft;
      if (left < nav.scrollLeft || left + active.offsetWidth > nav.scrollLeft + nav.clientWidth) {
        nav.scrollLeft = left - 16;
      }
    }
  }

  function fill(name, page) {
    if (!state.snapshot && name !== "requirements" && name !== "architecture") {
      page.replaceChildren(h("p", { class: "muted loading" }, state.failed ? "The portal data could not be loaded. Please try again later." : "Loading live numbers…"));
      return;
    }
    page.replaceChildren();
    try {
      render[name](page, state.snapshot || {});
      sizeFunnels(page);
      state.rendered.add(name);
    } catch (err) {
      page.replaceChildren(h("p", { class: "error" }, "This section could not be shown."));
      console.error(err);
    }
  }

  async function getJson(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    return response.json();
  }

  async function load() {
    const [snapshot, requirements, card, zones] = await Promise.allSettled([
      getJson("/api/portal"), getJson("/data/requirements.json"),
      fetch("/data/model-card.md").then((r) => (r.ok ? r.text() : null)), getJson("/api/zones"),
    ]);
    if (snapshot.status === "fulfilled") state.snapshot = snapshot.value; else state.failed = true;
    if (requirements.status === "fulfilled") state.requirements = requirements.value;
    if (card.status === "fulfilled") state.modelCard = card.value;
    if (zones.status === "fulfilled") for (const z of zones.value) state.zones.set(z.id, z.zone);
    if (state.snapshot) {
      const when = new Date(state.snapshot.generated_at);
      document.getElementById("snapshot-time").textContent = `Portal data from ${when.toLocaleDateString("en-GB", { dateStyle: "medium" })} ·`;
    }
    state.rendered.clear();
    show(location.hash.slice(1));
  }

  window.addEventListener("hashchange", () => { show(location.hash.slice(1)); window.scrollTo(0, 0); });
  show(location.hash.slice(1));
  load();
})();
