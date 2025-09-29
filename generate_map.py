# generate_map.py
# Usage:
#   python generate_map.py --csv map.csv --out interactive_biogas_map.html \
#     --min-radius 5 --max-radius 18 \
#     --size-by auto \
#     --visibility-mode both \
#     --preselect-status all --preselect-techno none \
#     --heat-radius 20 --heat-blur 15
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


def to_num_series(s: pd.Series) -> pd.Series:
  """Coerce a pandas Series of mixed numeric strings into floats.

  Handles:
  - Spaces, non-breaking spaces, and comma decimal separators
  - Ranges like "370-450" or with en/em dashes or "à" → returns the mean
  - Prefix/suffix noise (e.g., ">450", "~450", "450+", units)
  - Common NaN-like tokens ("", "nan", "n/a", "na", "--")
  """

  def parse_one(x) -> float:
    if pd.isna(x):
      return np.nan
    t = str(x).strip()
    if t == "":
      return np.nan

    low = t.lower()
    if low in {"nan", "n/a", "na", "none", "null", "-", "--"}:
      return np.nan

    # Normalize spaces, NBSP, decimal comma, and dash variants
    t = (
      t.replace("\xa0", "")
       .replace(" ", "")
       .replace(",", ".")
    )
    # Normalize different dash characters and French range separator
    t = re.sub(r"[–—−]", "-", t)  # en/em/minus dashes → hyphen-minus
    t = t.replace("à", "-")

    # Extract numeric tokens (keep sign and decimal part)
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", t)
    if not nums:
      return np.nan

    vals = []
    for n in nums:
      try:
        vals.append(float(n))
      except ValueError:
        continue

    if not vals:
      return np.nan

    # If appears to be an explicit range (contains '-' between digits), take the mean
    if len(vals) >= 2 and '-' in t:
      return float(np.mean(vals[:2]))

    # Otherwise return the first parsed number
    return float(vals[0])

  return s.apply(parse_one).astype(float)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def make_scaler(values, r_min=5, r_max=16):
    vals = pd.Series(values, dtype="float").replace([np.inf, -np.inf], np.nan).dropna()
    if vals.empty:
        return lambda v: (r_min + r_max) / 2.0
    v_min = float(vals.quantile(0.05))
    v_max = float(vals.quantile(0.95))
    if v_max <= v_min:
        v_min = float(vals.min())
        v_max = float(vals.max()) if float(vals.max()) > v_min else v_min + 1.0

    def scale(v):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return (r_min + r_max) / 2.0
        if np.isnan(x):
            return (r_min + r_max) / 2.0
        x_clamped = max(min(x, v_max), v_min)
        return r_min + (x_clamped - v_min) * (r_max - r_min) / (v_max - v_min)

    return scale


def build_html(
    site_data,
    color_map,
    gas_techs,
    bounds,
    biogaz_points,
    biomethane_points,
    preselect_status_all: bool,
    preselect_techno_all: bool,
    visibility_mode: str,
    heat_radius: int,
    heat_blur: int,
) -> str:
    (min_lat, min_lon, max_lat, max_lon) = bounds
    # Visibility predicate JS based on mode
    vis_logic = {
        "both": "const show = technoOk && statusOk;",
        "techno": "const show = technoOk;",
        "status": "const show = statusOk;",
        "either": "const show = technoOk || statusOk;",
    }[visibility_mode]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Biogas & Biomethane Interactive Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
<style>
  html, body, #map {{ height: 100%; margin: 0; }}
  .controls {{ position: absolute; top: 10px; left: 10px; z-index: 1000; background: #fff; padding: 10px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.2); max-height: 85%; width: 280px; max-width: 50vw; font-size: 14px; }}
  .controls.collapsed {{ padding: 6px; width: auto; }}
  .controls-header {{ display:flex; align-items:center; justify-content:space-between; gap: 8px; margin-bottom: 6px; }}
  .controls-title {{ font-weight: 700; font-size: 16px; }}
  .group-title {{ font-weight: 700; margin: 6px 0 4px; font-size: 14px; }}
  .section-title {{ font-weight: 700; margin: 6px 0 4px; font-size: 14px; }}
  .subtle {{ color: #555; font-size: 13px; margin-bottom: 6px; }}
  .row {{ display:flex; align-items:center; margin: 3px 0; gap: 6px; }}
  .row label {{ flex: 1; overflow-wrap: anywhere; }}
  .swatch {{ width:12px; height:12px; border: 1px solid #999; flex: 0 0 12px; }}
  .swatch-circle {{ border-radius: 50%; }}
  .swatch-diamond {{ transform: rotate(45deg); transform-origin: center; border-radius: 0; }}
  .counter {{ font-size: 13px; color: #444; margin-top: 4px; }}
  .note {{ font-size: 13px; color: #666; margin-top: 4px; }}
  .divider {{ height: 1px; background: #eee; margin: 6px 0; }}
  .btns {{ display:flex; gap:6px; flex-wrap:wrap; margin:4px 0; }}
  .btn {{ font-size:13px; padding:4px 10px; border:1px solid #ddd; border-radius:6px; background:#f8f8f8; cursor:pointer; }}
  #controls-body {{ overflow: auto; max-height: calc(75vh - 60px); }}
  .controls.collapsed #controls-body {{ display: none; }}
  /* Demand sector diamond marker (DivIcon) */
  .diamond-wrap {{ position: relative; width: 16px; height: 16px; }}
  .diamond {{ width: 100%; height: 100%; transform: rotate(45deg); transform-origin: center; opacity: 0.8; border: 2px solid #333; box-sizing: border-box; }}
</style>
</head>
<body>
<div id="map"></div>
<div class="controls" id="controls">
  <div class="controls-header">
    <div class="controls-title">Legend & Filters</div>
    <button id="toggle-controls" class="btn" title="Collapse">–</button>
  </div>
  <div id="controls-body">
  <div class="section-title">Techno & Legend</div>
  <div class="subtle"><em>By default, no sites are shown. Select a techno below (statuses are {('preselected' if preselect_status_all else 'not preselected')}). <b>Gas family</b> sites appear as <b>circles</b>; <b>demand sectors</b> appear as <b>diamonds</b>. Size scales with capacity (gas) or bioCO₂ potential (sectors).</em></div>

  <div class="group">
    <div class="group-title">Gas family (biomethane, biogaz, bioCO₂)</div>
    <div class="btns">
      <button id="tech-gas-all" class="btn">Select gas</button>
      <button id="tech-gas-none" class="btn">Clear gas</button>
    </div>
    <div id="techno-gas"></div>
  </div>

  <div class="group">
    <div class="group-title">Demand sectors</div>
    <div class="btns">
      <button id="tech-other-all" class="btn">Select sectors</button>
      <button id="tech-other-none" class="btn">Clear sectors</button>
    </div>
    <div id="techno-other"></div>
  </div>

  <div class="divider"></div>

  <div class="section-title">Operational status</div>
  <div class="btns">
    <button id="status-all" class="btn">Select all</button>
    <button id="status-none" class="btn">Clear all</button>
  </div>
  <div id="status-filters"></div>

  <div class="divider"></div>

  <div class="section-title">Density layers</div>
  <label class="row"><input type="checkbox" id="toggle-biogaz-heat"> Biogaz heatmap</label>
  <label class="row"><input type="checkbox" id="toggle-biomethane-heat"> Biomethane heatmap</label>

  <div class="counter"><span id="visible-count">0</span> site(s) visible</div>
  <div class="note">Tip: Visibility rule = <b>{visibility_mode}</b>. Heatmaps are independent overlays.</div>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
  integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
<script>
  const SITES = {json.dumps(site_data)};
  const TECHNO_COLORS = {json.dumps(color_map)};
  const gasTechs = new Set({json.dumps(gas_techs)});

  const map = L.map('map', {{ zoomControl: true }});
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
  }}).addTo(map);
  const bounds = L.latLngBounds([[{min_lat}, {min_lon}], [{max_lat}, {max_lon}]]);
  map.fitBounds(bounds.pad(0.1));

  const markersLayer = L.layerGroup().addTo(map);
  const allMarkers = [];

  function fmt(v) {{
    if (v === null || v === undefined || (typeof v === 'number' && isNaN(v))) return 'N/A';
    if (typeof v === 'number') return v.toLocaleString();
    return String(v);
  }}

  function makePopup(p) {{
    const rows = [
      ['Municipality', p.municipality],
      ['Techno', p.techno],
      ['Status', p.status],
      ['Operator', p.operator],
      ['Production/Demand', p.production_demand],
      ['Capacity (GWh/year)', p.capacity_gwh_year],
      ['bioCO₂ injection potential (t/y)', p.co2_injection_potential_tpy],
      ['Site info', p.site_info],
      ['Lat, Lon', p.lat.toFixed(6)+', '+p.lon.toFixed(6)],
      ['Sizing metric', p.size_metric_label + ': ' + fmt(p.size_metric_value)]
    ];
    return rows.filter(r => r[1] !== undefined && r[1] !== null && String(r[1]).length > 0)
      .map(([k,v]) => `<div><b>${{k}}:</b> ${{fmt(v).replaceAll('<','&lt;').replaceAll('>','&gt;')}}</div>`)
      .join('');
  }}

  // Create markers (hidden initially). Circles for gas family, diamonds for demand sectors.
  function makeDiamondMarker(lat, lon, color, sizePx, popupHtml, props) {{
    const sz = Math.max(10, Math.round((sizePx || 10) * 2)); // approximate circle diameter
    const html = `<div class="diamond-wrap" style="width:${{sz}}px;height:${{sz}}px;">
      <div class="diamond" style="background:${{color}}; border-color:${{color}};"></div>
    </div>`;
    const icon = L.divIcon({{ html: html, className: '', iconSize: [sz, sz], iconAnchor: [sz/2, sz/2] }});
  const mk = L.marker([lat, lon], {{ icon: icon, zIndexOffset: 200 }}).bindPopup(popupHtml);
    mk._props = props;
    return mk;
  }}

  SITES.forEach(s => {{
    const isGas = gasTechs.has(s.techno);
    let m;
    if (isGas) {{
      m = L.circleMarker([s.lat, s.lon], {{
        radius: s.radius || 10,
        color: s.color || '#000',
        weight: 2,
        fillColor: s.color || '#000',
        fillOpacity: 0.7
      }}).bindPopup(makePopup(s));
    }} else {{
      m = makeDiamondMarker(s.lat, s.lon, s.color || '#000', s.radius || 10, makePopup(s), s);
    }}
    m._props = s;
    allMarkers.push(m);
  }});

  // Build Techno & Legend (grouped)
  const gasWrap = document.getElementById('techno-gas');
  const otherWrap = document.getElementById('techno-other');

  const allTechs = Array.from(new Set(SITES.map(s => s.techno))).filter(Boolean);
  function addTechnoRow(container, label, color, idx, prefix, checked, isGas) {{
    const row = document.createElement('div');
    row.className = 'row';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.id = `${{prefix}}-${{idx}}`;
    cb.value = label;
    cb.checked = checked;
    const sw = document.createElement('span');
    sw.className = 'swatch ' + (isGas ? 'swatch-circle' : 'swatch-diamond');
    sw.style.background = color || '#000';
    const lab = document.createElement('label');
    lab.htmlFor = cb.id;
    lab.textContent = label;
    row.appendChild(cb);
    row.appendChild(sw);
    row.appendChild(lab);
    container.appendChild(row);
  }}

  const preselectTech = {"true" if preselect_techno_all else "false"};
  allTechs.forEach((t, i) => {{
    if (gasTechs.has(t)) addTechnoRow(gasWrap, t, TECHNO_COLORS[t], i, 'tech', preselectTech, true);
    else addTechnoRow(otherWrap, t, TECHNO_COLORS[t], i, 'tech', preselectTech, false);
  }});

  // Status filters
  const statusWrap = document.getElementById('status-filters');
  const allStatuses = Array.from(new Set(SITES.map(s => s.status))).filter(Boolean);
  const preselectStatus = {"true" if preselect_status_all else "false"};
  allStatuses.forEach((s, i) => {{
    const row = document.createElement('div');
    row.className = 'row';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.id = `stat-${{i}}`;
    cb.value = s;
    cb.checked = preselectStatus;
    const lab = document.createElement('label');
    lab.htmlFor = cb.id;
    lab.textContent = s;
    row.appendChild(cb);
    row.appendChild(lab);
    statusWrap.appendChild(row);
  }});

  function setChecked(selector, val) {{
    document.querySelectorAll(selector).forEach(cb => cb.checked = val);
  }}

  // Quick-select buttons
  document.getElementById('status-all').onclick = () => {{ setChecked('#status-filters input', true); applyFilters(); }};
  document.getElementById('status-none').onclick = () => {{ setChecked('#status-filters input', false); applyFilters(); }};
  document.getElementById('tech-gas-all').onclick = () => {{ setChecked('#techno-gas input', true); applyFilters(); }};
  document.getElementById('tech-gas-none').onclick = () => {{ setChecked('#techno-gas input', false); applyFilters(); }};
  document.getElementById('tech-other-all').onclick = () => {{ setChecked('#techno-other input', true); applyFilters(); }};
  document.getElementById('tech-other-none').onclick = () => {{ setChecked('#techno-other input', false); applyFilters(); }};

  function getSelected(prefix) {{
    return Array.from(document.querySelectorAll(`input[id^="${{prefix}}-"]`))
      .filter(cb => cb.checked)
      .map(cb => cb.value);
  }}

  function updateVisibleCount() {{
    document.getElementById('visible-count').textContent = markersLayer.getLayers().length;
  }}

  function applyFilters() {{
    const selectedTechnos = getSelected('tech');
    const selectedStatuses = getSelected('stat');

    markersLayer.clearLayers();

    allMarkers.forEach(m => {{
      const s = m._props;
      const technoOk = selectedTechnos.length === 0 ? false : selectedTechnos.includes(s.techno);
      const statusOk = selectedStatuses.length === 0 ? false : selectedStatuses.includes(s.status);
      {vis_logic}
      if (show) {{
        markersLayer.addLayer(m);
      }}
    }});

    updateVisibleCount();
  }}

  document.querySelectorAll('#techno-gas input, #techno-other input, #status-filters input')
    .forEach(cb => cb.addEventListener('change', applyFilters));

  // Heatmaps
  const biogazHeat = L.heatLayer({json.dumps(biogaz_points)}, {{ radius: {heat_radius}, blur: {heat_blur}, maxZoom: 12 }});
  const biomethaneHeat = L.heatLayer({json.dumps(biomethane_points)}, {{ radius: {heat_radius}, blur: {heat_blur}, maxZoom: 12 }});

  document.getElementById('toggle-biogaz-heat').addEventListener('change', (e) => {{
    if (e.target.checked) {{ biogazHeat.addTo(map); }} else {{ map.removeLayer(biogazHeat); }}
  }});
  document.getElementById('toggle-biomethane-heat').addEventListener('change', (e) => {{
    if (e.target.checked) {{ biomethaneHeat.addTo(map); }} else {{ map.removeLayer(biomethaneHeat); }}
  }});

  // Collapse/expand controls
  const controlsEl = document.getElementById('controls');
  const toggleBtn = document.getElementById('toggle-controls');
  toggleBtn.addEventListener('click', () => {{
    controlsEl.classList.toggle('collapsed');
    toggleBtn.textContent = controlsEl.classList.contains('collapsed') ? '+' : '–';
  }});

  // Initial count
  updateVisibleCount();
</script>
</body>
</html>
"""
    return html


def main():
    ap = argparse.ArgumentParser(description="Generate an interactive biogas/biomethane map (Leaflet HTML).")
    ap.add_argument("--csv", required=True, help="Path to map.csv")
    ap.add_argument("--out", default="interactive_biogas_map.html", help="Output HTML file")
    ap.add_argument("--min-radius", type=float, default=5.0, help="Minimum marker radius (px)")
    ap.add_argument("--max-radius", type=float, default=16.0, help="Maximum marker radius (px)")
    ap.add_argument(
        "--size-by",
        choices=["auto", "capacity", "co2"],
        default="auto",
        help="Sizing metric: auto (gas=capacity, sectors=CO2), capacity (all), co2 (all)",
    )
    ap.add_argument(
        "--visibility-mode",
        choices=["both", "techno", "status", "either"],
        default="both",
        help="Show sites when: both filters match; techno only; status only; or either one",
    )
    ap.add_argument(
        "--preselect-status",
        choices=["all", "none"],
        default="all",
        help="Preselect all statuses by default",
    )
    ap.add_argument(
        "--preselect-techno",
        choices=["none", "all"],
        default="none",
        help="Preselect technos by default",
    )
    ap.add_argument("--heat-radius", type=int, default=20, help="Heatmap radius")
    ap.add_argument("--heat-blur", type=int, default=15, help="Heatmap blur")
    args = ap.parse_args()

    df = pd.read_csv(args.csv, encoding="latin-1", sep=None, engine="python")

    # Normalize coordinates (e.g., '48,123' → 48.123)
    for col in ["latitude", "longitude"]:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(",", ".", regex=False)
                .str.replace(" ", "", regex=False)
            )
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"]).copy()

    # Column mappings & fallbacks
    techno_col = "techno"
    status_col = "operational_status"
    lat_col = "latitude"
    lon_col = "longitude"
    capacity_col = "capacite_gwh_year" if "capacite_gwh_year" in df.columns else "capacite_gwh_year/co2_injection_potential_tpy"
    co2_col = "co2_injection_potential_tpy" if "co2_injection_potential_tpy" in df.columns else None
    municipality_col = "municipality" if "municipality" in df.columns else None

    # Clean numeric inputs
    if capacity_col in df.columns:
        df[capacity_col] = to_num_series(df[capacity_col])
    if co2_col and (co2_col in df.columns):
        df[co2_col] = to_num_series(df[co2_col])

    # Grouping: gas family vs demand sectors
    gas_family_keys = {"biomethane", "biogaz", "bioco2"}
    technos = sorted(df[techno_col].dropna().unique().tolist(), key=lambda x: str(x))
    gas_techs = [t for t in technos if norm(t) in gas_family_keys]
    other_techs = [t for t in technos if norm(t) not in gas_family_keys]

    # Colors (gas first, then others for stable legend)
    palette = [
        "#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd",
        "#8c564b","#e377c2","#7f7f7f","#bcbd22","#17becf",
        "#393b79","#637939","#8c6d31","#843c39","#7b4173",
        "#3182bd","#e6550d","#31a354","#756bb1","#636363"
    ]
    ordered_for_colors = gas_techs + other_techs
    color_map = {t: palette[i % len(palette)] for i, t in enumerate(ordered_for_colors)}

    # Sizing strategy
    cap_scaler = make_scaler(df[capacity_col], args.min_radius, args.max_radius) if capacity_col in df.columns else (lambda v: (args.min_radius + args.max_radius)/2)
    co2_scaler = make_scaler(df[co2_col], args.min_radius, args.max_radius) if co2_col and (co2_col in df.columns) else (lambda v: (args.min_radius + args.max_radius)/2)

    def pick_radius(t: str, cap_val, co2_val):
        if args.size_by == "capacity":
            return cap_scaler(cap_val)
        if args.size_by == "co2":
            return co2_scaler(co2_val)
        # auto
        return cap_scaler(cap_val) if norm(t) in gas_family_keys else co2_scaler(co2_val)

    # Compose site data
    site_data = []
    for r in df.to_dict(orient="records"):
        t = r.get(techno_col, "")
        cap = r.get(capacity_col, None) if capacity_col in r else None
        co2 = r.get(co2_col, None) if co2_col and (co2_col in r) else None
        is_gas = norm(t) in gas_family_keys

        radius = float(pick_radius(t, cap, co2))
        metric_label = (
            "Capacity (GWh/year)" if (args.size_by == "capacity" or (args.size_by == "auto" and is_gas)) else
            "bioCO₂ injection potential (t/y)"
        )
        metric_value = cap if metric_label.startswith("Capacity") else co2

        site_data.append({
            "techno": t,
            "status": r.get(status_col, ""),
            "lat": float(r.get(lat_col)),
            "lon": float(r.get(lon_col)),
            "operator": r.get("operator", ""),
            "production_demand": r.get("production/demand", ""),
            "capacity_gwh_year": cap,
            "co2_injection_potential_tpy": co2,
            "site_info": r.get("site_info", ""),
            "municipality": r.get(municipality_col, "") if municipality_col else "",
            "color": color_map.get(t, "#000000"),
            "radius": radius,
            "size_metric_label": metric_label,
            "size_metric_value": metric_value
        })

    # Heatmap datasets
    biogaz_points = [[s["lat"], s["lon"], 1] for s in site_data if norm(s["techno"]) == "biogaz"]
    biomethane_points = [[s["lat"], s["lon"], 1] for s in site_data if norm(s["techno"]) == "biomethane"]

    # Bounds
    min_lat, max_lat = float(df[lat_col].min()), float(df[lat_col].max())
    min_lon, max_lon = float(df[lon_col].min()), float(df[lon_col].max())

    html = build_html(
        site_data=site_data,
        color_map=color_map,
        gas_techs=gas_techs,
        bounds=(min_lat, min_lon, max_lat, max_lon),
        biogaz_points=biogaz_points,
        biomethane_points=biomethane_points,
        preselect_status_all=(args.preselect_status == "all"),
        preselect_techno_all=(args.preselect-techno == "all") if False else (args.preselect_techno == "all"),
        visibility_mode=args.visibility_mode,
        heat_radius=args.heat_radius,
        heat_blur=args.heat_blur,
    )

    Path(args.out).write_text(html, encoding="utf-8")
    print(f"✅ Wrote {args.out}")


if __name__ == "__main__":
    main()
