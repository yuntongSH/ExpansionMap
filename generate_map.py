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
from pyproj import Transformer


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
    layer_category_map,
    bounds,
    biogaz_points,
    biomethane_points,
    supply_points,
    offtake_points,
    competitors_points,
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
<title>BioCO2 Expansion Map in Europe</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
<style>
  html, body, #map {{ height: 100%; margin: 0; }}
  .map-title {{ position: absolute; top: 10px; left: 50%; transform: translateX(-50%); z-index: 1000; background: linear-gradient(135deg, #00143B 0%, #006660 100%); color: white; padding: 12px 30px; border-radius: 25px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); font-size: 24px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; }}
  .controls {{ position: absolute; top: 10px; left: 10px; z-index: 1000; background: #fff; padding: 10px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.2); max-height: 85%; width: 280px; max-width: 50vw; font-size: 14px; }}
  .controls.collapsed {{ padding: 6px; width: auto; }}
  .controls-header {{ display:flex; align-items:center; justify-content:space-between; gap: 8px; margin-bottom: 6px; }}
  .controls-title {{ font-weight: 700; font-size: 16px; }}
  .group-title {{ font-weight: 600; margin: 8px 0 4px; font-size: 13px; color: #333; }}
  .section-title {{ font-weight: 700; margin: 6px 0 4px; font-size: 14px; }}
  .layer-section {{ border: 2px solid #ddd; border-radius: 8px; padding: 12px; margin: 12px 0; background: #fafafa; box-shadow: 0 1px 4px rgba(0,0,0,0.1); }}
  .layer-section-title {{ font-weight: 700; font-size: 17px; color: #222; margin-bottom: 10px; padding-bottom: 6px; border-bottom: 2px solid #ccc; text-transform: uppercase; letter-spacing: 0.5px; }}
  .layer-supply {{ border-color: #00143B; background: linear-gradient(135deg, #f0f4ff 0%, #e6eeff 100%); }}
  .layer-supply .layer-section-title {{ color: #00143B; border-bottom-color: #00143B; }}
  .layer-offtake {{ border-color: #D18F41; background: linear-gradient(135deg, #fff8ef 0%, #fff1e0 100%); }}
  .layer-offtake .layer-section-title {{ color: #D18F41; border-bottom-color: #D18F41; }}
  .layer-competitors {{ border-color: #006660; background: linear-gradient(135deg, #f0fffe 0%, #e6fff9 100%); }}
  .layer-competitors .layer-section-title {{ color: #006660; border-bottom-color: #006660; }}
  .layer-opportunity {{ border-color: #9C27B0; background: linear-gradient(135deg, #fef5ff 0%, #f3e5f5 100%); }}
  .layer-opportunity .layer-section-title {{ color: #6a1b9a; border-bottom-color: #9C27B0; }}
  .layer-eiffel {{ border-color: #FFD700; background: linear-gradient(135deg, #fffbf0 0%, #fff8e1 100%); }}
  .layer-eiffel .layer-section-title {{ color: #F57C00; border-bottom-color: #FFD700; }}
  .layer-status {{ border-color: #607D8B; background: linear-gradient(135deg, #f5f7f8 0%, #eceff1 100%); }}
  .layer-status .layer-section-title {{ color: #37474F; border-bottom-color: #607D8B; }}
  .heatmap-control {{ display: flex; align-items: center; justify-content: space-between; padding: 8px 10px; margin: 6px 0; background: white; border-radius: 6px; border: 1px solid #ddd; box-shadow: 0 1px 3px rgba(0,0,0,0.1); transition: all 0.2s; }}
  .heatmap-control:hover {{ box-shadow: 0 2px 6px rgba(0,0,0,0.15); transform: translateY(-1px); }}
  .heatmap-control input[type="checkbox"] {{ width: 18px; height: 18px; cursor: pointer; }}
  .heatmap-label {{ display: flex; align-items: center; gap: 8px; flex: 1; font-weight: 500; font-size: 13px; }}
  .heatmap-indicator {{ width: 20px; height: 20px; border-radius: 4px; flex-shrink: 0; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }}
  .heatmap-supply {{ background: linear-gradient(135deg, #1B5E20 0%, #4CAF50 50%, #1B5E20 100%); }}
  .heatmap-offtake {{ background: linear-gradient(135deg, #F9A825 0%, #FFD700 50%, #FF8F00 100%); }}
  .heatmap-competitors {{ background: linear-gradient(135deg, #0D47A1 0%, #2196F3 50%, #0D47A1 100%); }}
  .opportunity-note {{ font-size: 12px; color: #6a1b9a; font-style: italic; margin-top: 8px; padding: 8px; background: rgba(156, 39, 176, 0.05); border-radius: 4px; border-left: 3px solid #9C27B0; }}
  .sub-section {{ margin: 10px 0 15px 0; padding: 10px; background: rgba(255,255,255,0.5); border-radius: 6px; border: 1px solid rgba(0,0,0,0.08); }}
  .sub-section-title {{ font-weight: 600; font-size: 14px; color: #444; margin-bottom: 8px; padding-bottom: 4px; border-bottom: 1px solid rgba(0,0,0,0.1); }}
  .subtle {{ color: #555; font-size: 13px; margin-bottom: 6px; }}
  .row {{ display:flex; align-items:center; margin: 3px 0; gap: 6px; }}
  .row label {{ flex: 1; overflow-wrap: anywhere; }}
  .swatch {{ width:12px; height:12px; border: 1px solid #999; flex: 0 0 12px; }}
  .swatch-circle {{ border-radius: 50%; }}
  .swatch-square {{ border-radius: 2px; }}
  .swatch-diamond {{ transform: rotate(45deg); transform-origin: center; border-radius: 0; }}
  .swatch-star {{ position: relative; width: 0; height: 0; border: none; border-left: 6px solid transparent; border-right: 6px solid transparent; border-bottom: 4px solid; flex: 0 0 12px; }}
  .swatch-star::before {{ content: ''; position: absolute; width: 0; height: 0; border-left: 6px solid transparent; border-right: 6px solid transparent; border-top: 4px solid; top: 2px; left: -6px; }}
  .swatch-star::after {{ content: '★'; position: absolute; font-size: 14px; left: -7px; top: -10px; }}
  .counter {{ font-size: 13px; color: #444; margin-top: 4px; }}
  .note {{ font-size: 13px; color: #666; margin-top: 4px; }}
  .divider {{ height: 1px; background: #eee; margin: 6px 0; }}
  .zoom-mode-indicator {{ position: absolute; top: 10px; left: 50%; transform: translateX(-50%); background: rgba(156, 39, 176, 0.95); color: white; padding: 12px 20px; border-radius: 8px; font-weight: 600; font-size: 14px; z-index: 1000; box-shadow: 0 4px 12px rgba(0,0,0,0.3); display: none; }}
  .zoom-mode-active {{ display: block; animation: pulse 2s ease-in-out infinite; }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.8; }}
  }}
  .btns {{ display:flex; gap:6px; flex-wrap:wrap; margin:4px 0; }}
  .btn {{ font-size:13px; padding:4px 10px; border:1px solid #ddd; border-radius:6px; background:#f8f8f8; cursor:pointer; }}
  .btn-eiffel {{ background:#ffd700; border-color:#cc9900; font-weight: 600; }}
  .btn-eiffel.active {{ background:#ffeb3b; box-shadow: 0 0 8px rgba(255,215,0,0.6); }}
  #controls-body {{ overflow: auto; max-height: calc(75vh - 60px); }}
  .controls.collapsed #controls-body {{ display: none; }}
  /* Demand sector diamond marker (DivIcon) */
  .diamond-wrap {{ position: relative; width: 16px; height: 16px; }}
  .diamond {{ width: 100%; height: 100%; transform: rotate(45deg); transform-origin: center; opacity: 0.8; border: 2px solid #333; box-sizing: border-box; }}
  /* Efuels triangle marker (DivIcon) */
  .triangle-wrap {{ position: relative; width: 16px; height: 16px; display:flex; align-items:center; justify-content:center; }}
  .triangle {{ width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-bottom: 14px solid; opacity: 0.85; }}
</style>
</head>
<body>
<div class="map-title">BioCO2 Expansion Map in Europe</div>
<div id="map"></div>
<div id="zoom-mode-indicator" class="zoom-mode-indicator">🔍 Zoom-in Mode: Showing all sites within 100km</div>
<div class="controls" id="controls">
  <div class="controls-header">
    <div class="controls-title">Legend & Filters</div>
    <button id="toggle-controls" class="btn" title="Collapse">–</button>
  </div>
  <div id="controls-body">
  <div class="section-title">Techno & Legend</div>
  <div class="subtle"><em>By default, no sites are shown. Select a techno below (statuses are {('preselected' if preselect_status_all else 'not preselected')}). Toggle categories to visualize site locations.</em></div>

  <div class="layer-section layer-opportunity">
    <div class="layer-section-title">🎯 Opportunity Zones</div>
    
    <!-- Supply Heatmap (parent) -->
    <div class="heatmap-control">
      <label class="heatmap-label" for="toggle-supply-heat">
        <div class="heatmap-indicator heatmap-supply"></div>
        <span>Supply Heatmap</span>
      </label>
      <input type="checkbox" id="toggle-supply-heat">
    </div>
    <div style="margin-left: 25px; margin-bottom: 10px;">
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-biomethane-heat">
          <div class="heatmap-indicator heatmap-supply"></div>
          <span>Biomethane Heatmap</span>
        </label>
        <input type="checkbox" id="toggle-biomethane-heat">
      </div>
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-biogas-heat">
          <div class="heatmap-indicator heatmap-supply"></div>
          <span>Biogas Heatmap</span>
        </label>
        <input type="checkbox" id="toggle-biogas-heat">
      </div>
    </div>
    
    <!-- Offtake Heatmap (parent) -->
    <div class="heatmap-control">
      <label class="heatmap-label" for="toggle-offtake-heat">
        <div class="heatmap-indicator heatmap-offtake"></div>
        <span>Offtake Heatmap</span>
      </label>
      <input type="checkbox" id="toggle-offtake-heat">
    </div>
    <div style="margin-left: 25px; margin-bottom: 10px;">
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-foodprocessing-heat">
          <div class="heatmap-indicator heatmap-offtake"></div>
          <span>Food Processing Heatmap</span>
        </label>
        <input type="checkbox" id="toggle-foodprocessing-heat">
      </div>
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-efuels-heat">
          <div class="heatmap-indicator heatmap-offtake"></div>
          <span>E-fuels Heatmap</span>
        </label>
        <input type="checkbox" id="toggle-efuels-heat">
      </div>
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-storage-heat">
          <div class="heatmap-indicator heatmap-offtake"></div>
          <span>Storage Heatmap</span>
        </label>
        <input type="checkbox" id="toggle-storage-heat">
      </div>
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-greenhouses-heat">
          <div class="heatmap-indicator heatmap-offtake"></div>
          <span>Greenhouses Heatmap</span>
        </label>
        <input type="checkbox" id="toggle-greenhouses-heat">
      </div>
    </div>
    
    <!-- Competitors Heatmap (parent) -->
    <div class="heatmap-control">
      <label class="heatmap-label" for="toggle-competitors-heat">
        <div class="heatmap-indicator heatmap-competitors"></div>
        <span>Competitors Heatmap</span>
      </label>
      <input type="checkbox" id="toggle-competitors-heat">
    </div>
    <div style="margin-left: 25px; margin-bottom: 10px;">
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-bioco2-heat">
          <div class="heatmap-indicator heatmap-competitors"></div>
          <span>BioCO₂ Heatmap</span>
        </label>
        <input type="checkbox" id="toggle-bioco2-heat">
      </div>
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-fossilco2-heat">
          <div class="heatmap-indicator heatmap-competitors"></div>
          <span>FossilCO₂ Heatmap</span>
        </label>
        <input type="checkbox" id="toggle-fossilco2-heat">
      </div>
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-capture-heat">
          <div class="heatmap-indicator heatmap-competitors"></div>
          <span>Capture Projects Heatmap</span>
        </label>
        <input type="checkbox" id="toggle-capture-heat">
      </div>
    </div>
    
    <div class="opportunity-note">
      <strong>💡 Opportunity zones:</strong> Areas with high supply + high offtake but low competitors
    </div>
  </div>

  <div class="layer-section layer-supply">
    <div class="layer-section-title">Supply</div>
    <div class="btns">
      <button id="supply-all" class="btn">Select All Supply</button>
      <button id="supply-none" class="btn">Clear All Supply</button>
    </div>
    <div id="layer-supply-content"></div>
  </div>

  <div class="layer-section layer-offtake">
    <div class="layer-section-title">Offtake</div>
    <div class="btns">
      <button id="offtake-all" class="btn">Select All Offtake</button>
      <button id="offtake-none" class="btn">Clear All Offtake</button>
    </div>
    <div id="layer-offtake-content"></div>
  </div>

  <div class="layer-section layer-competitors">
    <div class="layer-section-title">Competitors</div>
    <div class="btns">
      <button id="competitors-all" class="btn">Select All Competitors</button>
      <button id="competitors-none" class="btn">Clear All Competitors</button>
    </div>
    <div id="layer-competitors-content"></div>
  </div>

  <div class="divider"></div>

  <div class="layer-section layer-eiffel">
    <div class="layer-section-title">Eiffel Gaz Vert</div>
    <div class="btns">
      <button id="toggle-eiffel" class="btn btn-eiffel">Highlight Eiffel plants</button>
    </div>
  </div>

  <div class="divider"></div>

  <div class="layer-section layer-status">
    <div class="layer-section-title">Operational Status</div>
    <div class="btns">
      <button id="status-all" class="btn">Select all</button>
      <button id="status-none" class="btn">Clear all</button>
    </div>
    <div id="status-filters"></div>
  </div>

  <div class="divider"></div>

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
  const LAYER_CATEGORY_MAP = {json.dumps(layer_category_map)};

  const map = L.map('map', {{ zoomControl: true }});
  // Use CartoDB Light (light gray background) for a clean, uniform appearance
  L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd'
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
      ['Production/Demand', p.production_demand]
    ];
    
    // Add capacity based on layer and category
    const layer = p.layer;
    const category = p.category;
    
    if (layer === 'Supply') {{
      rows.push(['Capacity (GWh/year)', p.capacity_gwh_year]);
    }} else if (layer === 'Offtake') {{
      if (category === 'E-methanol' || category === 'E-SAF') {{
        rows.push(['Capacity (kt/year)', p.capacity_kt_per_year]);
      }} else if (category === 'Storage' || category === 'Food processing') {{
        rows.push(['bioCO₂ injection potential (t/y)', p.co2_injection_potential_tpy]);
      }}
    }} else if (layer === 'Competitors') {{
      if (category === 'BioCO2' || category === 'FossilCO2') {{
        rows.push(['CO₂ capacity (t/y)', p.co2_injection_potential_tpy]);
      }} else if (category === 'Capture') {{
        rows.push(['bioCO₂ injection potential (t/y)', p.co2_injection_potential_tpy]);
      }}
    }}
    
    rows.push(['Site info', p.site_info]);
    rows.push(['Lat, Lon', p.lat.toFixed(6)+', '+p.lon.toFixed(6)]);
    rows.push(['Sizing metric', p.size_metric_label + ': ' + fmt(p.size_metric_value)]);
    
    if (p.is_eiffel) {{
      rows.unshift(['🏆 Eiffel Investment', p.eiffel_project_name || 'Yes']);
    }}
    return rows.filter(r => r[1] !== undefined && r[1] !== null && String(r[1]).length > 0)
      .map(([k,v]) => `<div><b>${{k}}:</b> ${{fmt(v).replaceAll('<','&lt;').replaceAll('>','&gt;')}}</div>`)
      .join('');
  }}

  // Create markers (hidden initially). Circles for gas, triangles for efuels, diamonds for demand sectors.
  function makeDiamondMarker(lat, lon, color, sizePx, popupHtml, props) {{
    const sz = Math.max(10, Math.round((sizePx || 10) * 2));
    const html = `<div class="diamond-wrap" style="width:${{sz}}px;height:${{sz}}px;">
      <div class="diamond" style="background:${{color}}; border-color:${{color}};"></div>
    </div>`;
    const icon = L.divIcon({{ html: html, className: '', iconSize: [sz, sz], iconAnchor: [sz/2, sz/2] }});
    const mk = L.marker([lat, lon], {{ icon: icon, zIndexOffset: 200 }}).bindPopup(popupHtml);
    mk._props = props;
    return mk;
  }}

  function makeStarMarker(lat, lon, color, sizePx, popupHtml, props) {{
    const sz = Math.max(12, Math.round((sizePx || 10) * 2));
    const html = `<div style="width:${{sz}}px;height:${{sz}}px;display:flex;align-items:center;justify-content:center;">
      <span style="color:${{color}};font-size:${{sz}}px;line-height:1;">★</span>
    </div>`;
    const icon = L.divIcon({{ html: html, className: '', iconSize: [sz, sz], iconAnchor: [sz/2, sz/2] }});
    const mk = L.marker([lat, lon], {{ icon: icon, zIndexOffset: 150 }}).bindPopup(popupHtml);
    mk._props = props;
    return mk;
  }}

  function makeSquareMarker(lat, lon, color, sizePx, popupHtml, props) {{
    const sz = Math.max(10, Math.round((sizePx || 10) * 2));
    const html = `<div style="width:${{sz}}px;height:${{sz}}px;background:${{color}};border:2px solid ${{color}};opacity:0.85;"></div>`;
    const icon = L.divIcon({{ html: html, className: '', iconSize: [sz, sz], iconAnchor: [sz/2, sz/2] }});
    const mk = L.marker([lat, lon], {{ icon: icon, zIndexOffset: 100 }}).bindPopup(popupHtml);
    mk._props = props;
    return mk;
  }}

  SITES.forEach(s => {{
    // Different shapes per layer: Circle for Supply, Star for Offtake, Diamond for Competitors
    let m;
    const radius = s.radius || 10;
    const color = s.color || '#000';
    
    if (s.layer === 'Supply') {{
      // Circle marker for Supply
      m = L.circleMarker([s.lat, s.lon], {{
        radius: radius,
        color: color,
        weight: 2,
        fillColor: color,
        fillOpacity: 0.7
      }}).bindPopup(makePopup(s));
    }} else if (s.layer === 'Offtake') {{
      // Star marker for Offtake
      m = makeStarMarker(s.lat, s.lon, color, radius, makePopup(s), s);
    }} else if (s.layer === 'Competitors') {{
      // Diamond marker for Competitors
      m = makeDiamondMarker(s.lat, s.lon, color, radius, makePopup(s), s);
    }} else {{
      // Default to circle for unknown layers
      m = L.circleMarker([s.lat, s.lon], {{
        radius: radius,
        color: color,
        weight: 2,
        fillColor: color,
        fillOpacity: 0.7
      }}).bindPopup(makePopup(s));
    }}
    
    m._props = s;
    m._originalStyle = {{ color: color, fillColor: color }};
    allMarkers.push(m);
  }});

  // Build Techno & Legend (grouped by Layer and Category)
  const layerContainers = {{
    'Supply': document.getElementById('layer-supply-content'),
    'Offtake': document.getElementById('layer-offtake-content'),
    'Competitors': document.getElementById('layer-competitors-content')
  }};

  function addTechnoRow(container, category, label, color, idx, prefix, checked, layer) {{
    const row = document.createElement('label');
    row.className = 'heatmap-control';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.id = `${{prefix}}-${{idx}}`;
    cb.value = label;
    cb.checked = checked;
    const leftSpan = document.createElement('span');
    leftSpan.style.display = 'flex';
    leftSpan.style.alignItems = 'center';
    leftSpan.style.gap = '8px';
    
    // Create shape indicator based on layer
    const sw = document.createElement('span');
    if (layer === 'Supply') {{
      sw.className = 'swatch swatch-circle';
      sw.style.background = color || '#000';
    }} else if (layer === 'Offtake') {{
      sw.className = 'swatch swatch-star';
      sw.style.color = color || '#000';
    }} else if (layer === 'Competitors') {{
      sw.className = 'swatch swatch-diamond';
      sw.style.background = color || '#000';
    }} else {{
      // Default to circle
      sw.className = 'swatch swatch-circle';
      sw.style.background = color || '#000';
    }}
    
    const textSpan = document.createElement('span');
    textSpan.textContent = label;
    leftSpan.appendChild(cb);
    leftSpan.appendChild(sw);
    leftSpan.appendChild(textSpan);
    const categorySpan = document.createElement('span');
    categorySpan.style.fontSize = '11px';
    categorySpan.style.color = '#666';
    categorySpan.textContent = category;
    row.appendChild(leftSpan);
    row.appendChild(categorySpan);
    container.appendChild(row);
  }}

  const preselectTech = {"true" if preselect_techno_all else "false"};
  let techIdx = 0;
  
  // Define sub-section groupings
  const subSectionGroups = {{
    'Offtake': {{
      'Food processing': ['Food processing'],
      'E-fuels': ['E-methanol', 'E-SAF'],
      'Storage': ['Storage'],
      'Greenhouses': ['Greenhouses']
    }},
    'Competitors': {{
      'BioCO₂': ['BioCO2'],
      'FossilCO₂': ['FossilCO2'],
      'Capture projects': ['Capture']
    }}
  }};
  
  // Populate technos by layer and category
  Object.keys(LAYER_CATEGORY_MAP).forEach(layer => {{
    const container = layerContainers[layer];
    if (container) {{
      // Check if this layer has sub-sections
      if (subSectionGroups[layer]) {{
        Object.keys(subSectionGroups[layer]).forEach(subSectionName => {{
          const categories = subSectionGroups[layer][subSectionName];
          
          // Create sub-section container
          const subSection = document.createElement('div');
          subSection.className = 'sub-section';
          const subTitle = document.createElement('div');
          subTitle.className = 'sub-section-title';
          subTitle.textContent = subSectionName;
          subSection.appendChild(subTitle);
          
          // Add technos for each category in this sub-section
          categories.forEach(category => {{
            if (LAYER_CATEGORY_MAP[layer][category]) {{
              const technos = LAYER_CATEGORY_MAP[layer][category];
              technos.forEach(techno => {{
                addTechnoRow(subSection, category, techno, TECHNO_COLORS[techno], techIdx++, 'tech', preselectTech, layer);
              }});
            }}
          }});
          
          container.appendChild(subSection);
        }});
      }} else {{
        // No sub-sections, just add technos directly
        Object.keys(LAYER_CATEGORY_MAP[layer]).forEach(category => {{
          const technos = LAYER_CATEGORY_MAP[layer][category];
          technos.forEach(techno => {{
            addTechnoRow(container, category, techno, TECHNO_COLORS[techno], techIdx++, 'tech', preselectTech, layer);
          }});
        }});
      }}
    }}
  }});

  // Status filters
  const statusWrap = document.getElementById('status-filters');
  const allStatuses = Array.from(new Set(SITES.map(s => s.status))).filter(Boolean);
  const preselectStatus = {"true" if preselect_status_all else "false"};
  allStatuses.forEach((s, i) => {{
    const row = document.createElement('label');
    row.className = 'heatmap-control';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.id = `stat-${{i}}`;
    cb.value = s;
    cb.checked = preselectStatus;
    const leftSpan = document.createElement('span');
    leftSpan.style.display = 'flex';
    leftSpan.style.alignItems = 'center';
    leftSpan.style.gap = '8px';
    const textSpan = document.createElement('span');
    textSpan.textContent = s;
    leftSpan.appendChild(cb);
    leftSpan.appendChild(textSpan);
    row.appendChild(leftSpan);
    statusWrap.appendChild(row);
  }});

  function setChecked(selector, val) {{
    document.querySelectorAll(selector).forEach(cb => cb.checked = val);
  }}

  // Quick-select buttons
  document.getElementById('status-all').onclick = () => {{ setChecked('#status-filters input', true); applyFilters(); }};
  document.getElementById('status-none').onclick = () => {{ setChecked('#status-filters input', false); applyFilters(); }};
  
  // Section-level layer buttons
  document.getElementById('supply-all').onclick = () => {{ setChecked('#layer-supply-content input', true); applyFilters(); }};
  document.getElementById('supply-none').onclick = () => {{ setChecked('#layer-supply-content input', false); applyFilters(); }};
  document.getElementById('offtake-all').onclick = () => {{ setChecked('#layer-offtake-content input', true); applyFilters(); }};
  document.getElementById('offtake-none').onclick = () => {{ setChecked('#layer-offtake-content input', false); applyFilters(); }};
  document.getElementById('competitors-all').onclick = () => {{ setChecked('#layer-competitors-content input', true); applyFilters(); }};
  document.getElementById('competitors-none').onclick = () => {{ setChecked('#layer-competitors-content input', false); applyFilters(); }};

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
      // For sites with no status (like Greenhouses), always consider statusOk as true if techno is selected
      const statusOk = s.status === '' ? true : (selectedStatuses.length === 0 ? false : selectedStatuses.includes(s.status));
      {vis_logic}
      if (show) {{
        markersLayer.addLayer(m);
      }}
    }});

    updateVisibleCount();
  }}

  document.querySelectorAll('#layer-supply-content input, #layer-offtake-content input, #layer-competitors-content input, #status-filters input')
    .forEach(cb => cb.addEventListener('change', applyFilters));

  // Eiffel investment highlighting
  let eiffelHighlightActive = false;
  document.getElementById('toggle-eiffel').addEventListener('click', (e) => {{
    eiffelHighlightActive = !eiffelHighlightActive;
    e.target.classList.toggle('active', eiffelHighlightActive);
    
    allMarkers.forEach(m => {{
      const s = m._props;
      if (eiffelHighlightActive && s.is_eiffel) {{
        // Highlight Eiffel investments with gold glow
        if (m instanceof L.CircleMarker) {{
          m.setStyle({{ color: '#FFD700', fillColor: '#FFD700', weight: 3, fillOpacity: 0.9 }});
        }} else {{
          // For DivIcon markers, add a wrapper highlight
          const el = m.getElement();
          if (el) el.style.filter = 'drop-shadow(0 0 6px #FFD700)';
        }}
      }} else {{
        // Reset to original style
        if (m instanceof L.CircleMarker) {{
          m.setStyle({{ 
            color: m._originalStyle.color, 
            fillColor: m._originalStyle.fillColor, 
            weight: 2, 
            fillOpacity: 0.7 
          }});
        }} else {{
          const el = m.getElement();
          if (el) el.style.filter = '';
        }}
      }}
    }});
  }});

  // Zoom-in mode functionality
  let zoomModeActive = false;
  let zoomModeCircle = null;
  let zoomModeFocusSite = null;
  const ZOOM_IN_THRESHOLD = 10; // Zoom level to activate zoom-in mode
  const RADIUS_KM = 100; // 100km radius

  // Calculate distance between two points in km (Haversine formula)
  function getDistance(lat1, lon1, lat2, lon2) {{
    const R = 6371; // Earth radius in km
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
  }}

  // Enter zoom-in mode
  function enterZoomMode(site, latlng, clickedMarker) {{
    zoomModeActive = true;
    zoomModeFocusSite = site;
    
    // Show indicator
    document.getElementById('zoom-mode-indicator').classList.add('zoom-mode-active');
    
    // Draw 100km radius circle with better visibility
    if (zoomModeCircle) {{
      map.removeLayer(zoomModeCircle);
    }}
    zoomModeCircle = L.circle(latlng, {{
      radius: RADIUS_KM * 1000, // Convert to meters
      color: '#9C27B0',
      fillColor: '#E1BEE7',
      fillOpacity: 0.15,
      weight: 4,
      dashArray: '15, 10',
      className: 'radius-circle'
    }}).addTo(map);
    
    // Clear and show all sites within 100km
    markersLayer.clearLayers();
    let focusMarker = null;
    allMarkers.forEach(m => {{
      const s = m._props;
      const distance = getDistance(site.lat, site.lon, s.lat, s.lon);
      if (distance <= RADIUS_KM) {{
        markersLayer.addLayer(m);
        if (s === site) {{
          focusMarker = m;
        }}
      }}
    }});
    
    updateVisibleCount();
    
    // Zoom in on the site
    map.setView(latlng, Math.max(map.getZoom(), ZOOM_IN_THRESHOLD + 1), {{
      animate: true,
      duration: 0.8
    }});
    
    // Keep the popup open for the selected site
    setTimeout(() => {{
      if (focusMarker) {{
        focusMarker.openPopup();
      }} else if (clickedMarker) {{
        clickedMarker.openPopup();
      }}
    }}, 900); // Open after zoom animation completes
  }}

  // Exit zoom-in mode
  function exitZoomMode() {{
    if (!zoomModeActive) return;
    
    zoomModeActive = false;
    zoomModeFocusSite = null;
    
    // Hide indicator
    document.getElementById('zoom-mode-indicator').classList.remove('zoom-mode-active');
    
    // Remove radius circle
    if (zoomModeCircle) {{
      map.removeLayer(zoomModeCircle);
      zoomModeCircle = null;
    }}
    
    // Restore normal filter mode
    applyFilters();
  }}

  // Add click handlers to all markers
  allMarkers.forEach(m => {{
    m.on('click', (e) => {{
      const site = m._props;
      const latlng = e.latlng;
      
      // Enter zoom-in mode when clicking a site
      enterZoomMode(site, latlng, m);
    }});
  }});

  // Monitor zoom level - exit zoom mode when zooming out
  map.on('zoomend', () => {{
    if (zoomModeActive && map.getZoom() < ZOOM_IN_THRESHOLD) {{
      exitZoomMode();
    }}
  }});

  // Individual heatmap layers
  const heatmaps = {{
    biomethane: null,
    biogas: null,
    foodprocessing: null,
    efuels: null,
    storage: null,
    greenhouses: null,
    bioco2: null,
    fossilco2: null,
    capture: null
  }};

  // Helper function to create a heatmap from filtered sites
  function createHeatmap(filterFunc, gradient, max = null) {{
    const points = SITES.filter(filterFunc).map(s => [s.lat, s.lon, 1]);
    const options = {{
      radius: {heat_radius},
      blur: {heat_blur},
      maxZoom: 12,
      gradient: gradient
    }};
    if (max !== null) {{
      options.max = max;
    }}
    return L.heatLayer(points, options);
  }}

  // Supply gradient
  const supplyGradient = {{0.4: 'lime', 0.6: 'yellow', 0.8: 'orange', 1.0: 'red'}};
  
  // Offtake gradient
  const offtakeGradient = {{0.4: 'yellow', 0.6: 'gold', 0.8: 'orange', 1.0: 'darkorange'}};
  
  // Competitors gradient
  const competitorsGradient = {{0.2: 'cyan', 0.4: 'deepskyblue', 0.6: 'blue', 0.8: 'darkblue', 1.0: 'purple'}};

  // Supply Heatmap parent toggle
  document.getElementById('toggle-supply-heat').addEventListener('change', (e) => {{
    const childCheckboxes = ['toggle-biomethane-heat', 'toggle-biogas-heat'];
    childCheckboxes.forEach(id => {{
      document.getElementById(id).checked = e.target.checked;
      document.getElementById(id).dispatchEvent(new Event('change'));
    }});
  }});

  // Biomethane heatmap
  document.getElementById('toggle-biomethane-heat').addEventListener('change', (e) => {{
    if (heatmaps.biomethane) {{
      map.removeLayer(heatmaps.biomethane);
      heatmaps.biomethane = null;
    }}
    if (e.target.checked) {{
      heatmaps.biomethane = createHeatmap(
        s => s.layer === 'Supply' && ['Bio-CNG', 'Bio-LNG', 'Biomethane'].includes(s.techno),
        supplyGradient
      );
      heatmaps.biomethane.addTo(map);
    }}
  }});

  // Biogas heatmap
  document.getElementById('toggle-biogas-heat').addEventListener('change', (e) => {{
    if (heatmaps.biogas) {{
      map.removeLayer(heatmaps.biogas);
      heatmaps.biogas = null;
    }}
    if (e.target.checked) {{
      heatmaps.biogas = createHeatmap(
        s => s.category === 'Biogas',
        supplyGradient
      );
      heatmaps.biogas.addTo(map);
    }}
  }});

  // Offtake Heatmap parent toggle
  document.getElementById('toggle-offtake-heat').addEventListener('change', (e) => {{
    const childCheckboxes = ['toggle-foodprocessing-heat', 'toggle-efuels-heat', 'toggle-storage-heat', 'toggle-greenhouses-heat'];
    childCheckboxes.forEach(id => {{
      document.getElementById(id).checked = e.target.checked;
      document.getElementById(id).dispatchEvent(new Event('change'));
    }});
  }});

  // Food Processing heatmap
  document.getElementById('toggle-foodprocessing-heat').addEventListener('change', (e) => {{
    if (heatmaps.foodprocessing) {{
      map.removeLayer(heatmaps.foodprocessing);
      heatmaps.foodprocessing = null;
    }}
    if (e.target.checked) {{
      heatmaps.foodprocessing = createHeatmap(
        s => s.category === 'Food processing',
        offtakeGradient
      );
      heatmaps.foodprocessing.addTo(map);
    }}
  }});

  // E-fuels heatmap
  document.getElementById('toggle-efuels-heat').addEventListener('change', (e) => {{
    if (heatmaps.efuels) {{
      map.removeLayer(heatmaps.efuels);
      heatmaps.efuels = null;
    }}
    if (e.target.checked) {{
      heatmaps.efuels = createHeatmap(
        s => ['E-methanol', 'E-SAF'].includes(s.techno),
        offtakeGradient
      );
      heatmaps.efuels.addTo(map);
    }}
  }});

  // Storage heatmap - with higher intensity for better visibility
  document.getElementById('toggle-storage-heat').addEventListener('change', (e) => {{
    if (heatmaps.storage) {{
      map.removeLayer(heatmaps.storage);
      heatmaps.storage = null;
    }}
    if (e.target.checked) {{
      const storageSites = SITES.filter(s => s.category === 'Storage');
      const points = storageSites.map(s => [s.lat, s.lon, 5]); // Increased intensity from 1 to 5
      
      heatmaps.storage = L.heatLayer(points, {{
        radius: {heat_radius} * 1.5, // Increased radius for better visibility
        blur: {heat_blur} * 1.2,
        maxZoom: 12,
        max: 10, // Set max for better color distribution
        gradient: offtakeGradient
      }});
      heatmaps.storage.addTo(map);
    }}
  }});

  // Greenhouses heatmap
  document.getElementById('toggle-greenhouses-heat').addEventListener('change', (e) => {{
    if (heatmaps.greenhouses) {{
      map.removeLayer(heatmaps.greenhouses);
      heatmaps.greenhouses = null;
    }}
    if (e.target.checked) {{
      heatmaps.greenhouses = createHeatmap(
        s => s.techno === 'Greenhouses',
        offtakeGradient
      );
      heatmaps.greenhouses.addTo(map);
    }}
  }});

  // Competitors Heatmap parent toggle
  document.getElementById('toggle-competitors-heat').addEventListener('change', (e) => {{
    const childCheckboxes = ['toggle-bioco2-heat', 'toggle-fossilco2-heat', 'toggle-capture-heat'];
    childCheckboxes.forEach(id => {{
      document.getElementById(id).checked = e.target.checked;
      document.getElementById(id).dispatchEvent(new Event('change'));
    }});
  }});

  // BioCO2 heatmap
  document.getElementById('toggle-bioco2-heat').addEventListener('change', (e) => {{
    if (heatmaps.bioco2) {{
      map.removeLayer(heatmaps.bioco2);
      heatmaps.bioco2 = null;
    }}
    if (e.target.checked) {{
      heatmaps.bioco2 = createHeatmap(
        s => s.techno === 'BioCO2',
        competitorsGradient,
        3.0
      );
      heatmaps.bioco2.addTo(map);
    }}
  }});

  // FossilCO2 heatmap
  document.getElementById('toggle-fossilco2-heat').addEventListener('change', (e) => {{
    if (heatmaps.fossilco2) {{
      map.removeLayer(heatmaps.fossilco2);
      heatmaps.fossilco2 = null;
    }}
    if (e.target.checked) {{
      heatmaps.fossilco2 = createHeatmap(
        s => s.techno === 'FossilCO2',
        competitorsGradient,
        3.0
      );
      heatmaps.fossilco2.addTo(map);
    }}
  }});

  // Capture Projects heatmap
  document.getElementById('toggle-capture-heat').addEventListener('change', (e) => {{
    if (heatmaps.capture) {{
      map.removeLayer(heatmaps.capture);
      heatmaps.capture = null;
    }}
    if (e.target.checked) {{
      heatmaps.capture = createHeatmap(
        s => s.category === 'Capture',
        competitorsGradient,
        3.0
      );
      heatmaps.capture.addTo(map);
    }}
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
    ap.add_argument("--out", default="BioCO2 Expansion Map 2025.html", help="Output HTML file")
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
    capacity_kt_col = "capacity_kt_per_year" if "capacity_kt_per_year" in df.columns else None
    municipality_col = "municipality" if "municipality" in df.columns else None
    eiffel_col = "eiffel" if "eiffel" in df.columns else None
    eiffel_project_col = "eiffel_project_name" if "eiffel_project_name" in df.columns else None

    # Clean numeric inputs
    if capacity_col in df.columns:
        df[capacity_col] = to_num_series(df[capacity_col])
    if co2_col and (co2_col in df.columns):
        df[co2_col] = to_num_series(df[co2_col])
    if capacity_kt_col and (capacity_kt_col in df.columns):
        df[capacity_kt_col] = to_num_series(df[capacity_kt_col])
    if eiffel_col and (eiffel_col in df.columns):
        df[eiffel_col] = pd.to_numeric(df[eiffel_col], errors="coerce").fillna(0).astype(int)

    # Build Layer-Category-Techno mapping
    layer_col = "Layer" if "Layer" in df.columns else None
    category_col = "category" if "category" in df.columns else None
    
    if not layer_col or not category_col:
        raise ValueError("CSV must contain 'Layer' and 'category' columns")
    
    # Build the hierarchical structure
    layer_category_map = {}
    for layer in df[layer_col].dropna().unique():
        layer_category_map[layer] = {}
        layer_df = df[df[layer_col] == layer]
        for category in layer_df[category_col].dropna().unique():
            technos = sorted(layer_df[layer_df[category_col] == category][techno_col].dropna().unique().tolist())
            layer_category_map[layer][category] = technos
    
    # Colors - assign to all technos in order
    palette = [
        "#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd",
        "#8c564b","#e377c2","#7f7f7f","#bcbd22","#17becf",
        "#393b79","#637939","#8c6d31","#843c39","#7b4173",
        "#3182bd","#e6550d","#31a354","#756bb1","#636363"
    ]
    all_technos = []
    for layer in ['Supply', 'Offtake', 'Competitors']:
        if layer in layer_category_map:
            for category in layer_category_map[layer]:
                all_technos.extend(layer_category_map[layer][category])
    color_map = {t: palette[i % len(palette)] for i, t in enumerate(all_technos)}
    # Add Greenhouses to color map (will be added later from ghg_intensity.csv)
    color_map['Greenhouses'] = '#FF6B35'  # Rainbow color (red-yellow mix) for greenhouses

    # Sizing strategy
    cap_scaler = make_scaler(df[capacity_col], args.min_radius, args.max_radius) if capacity_col in df.columns else (lambda v: (args.min_radius + args.max_radius)/2)
    co2_scaler = make_scaler(df[co2_col], args.min_radius, args.max_radius) if co2_col and (co2_col in df.columns) else (lambda v: (args.min_radius + args.max_radius)/2)
    kt_scaler = make_scaler(df[capacity_kt_col], args.min_radius, args.max_radius) if capacity_kt_col and (capacity_kt_col in df.columns) else (lambda v: (args.min_radius + args.max_radius)/2)

    def pick_radius(layer: str, category: str, cap_val, co2_val, kt_val):
        if args.size_by == "capacity":
            if category in ['E-methanol', 'E-SAF']:
                return kt_scaler(kt_val)
            else:
                return cap_scaler(cap_val)
        if args.size_by == "co2":
            return co2_scaler(co2_val)
        # auto
        if layer == 'Supply':
            return cap_scaler(cap_val)
        elif layer == 'Offtake':
            if category in ['E-methanol', 'E-SAF']:
                return kt_scaler(kt_val)
            else:
                return co2_scaler(co2_val)
        elif layer == 'Competitors':
            return co2_scaler(co2_val)
        else:
            return cap_scaler(cap_val)

    # Compose site data
    site_data = []
    for r in df.to_dict(orient="records"):
        t = r.get(techno_col, "")
        layer = r.get(layer_col, "")
        category = r.get(category_col, "")
        cap = r.get(capacity_col, None) if capacity_col in r else None
        co2 = r.get(co2_col, None) if co2_col and (co2_col in r) else None
        kt = r.get(capacity_kt_col, None) if capacity_kt_col and (capacity_kt_col in r) else None
        is_eiffel = bool(r.get(eiffel_col, 0)) if eiffel_col else False
        eiffel_project = r.get(eiffel_project_col, "") if eiffel_project_col else ""

        radius = float(pick_radius(layer, category, cap, co2, kt))
        
        if args.size_by == "capacity":
            if category in ['E-methanol', 'E-SAF']:
                metric_label = "Capacity (kt/year)"
                metric_value = kt
            else:
                metric_label = "Capacity (GWh/year)"
                metric_value = cap
        elif args.size_by == "co2":
            metric_label = "bioCO₂ injection potential (t/y)"
            metric_value = co2
        else:  # auto
            if layer == 'Supply':
                metric_label = "Capacity (GWh/year)"
                metric_value = cap
            elif layer == 'Offtake':
                if category in ['E-methanol', 'E-SAF']:
                    metric_label = "Capacity (kt/year)"
                    metric_value = kt
                else:
                    metric_label = "bioCO₂ injection potential (t/y)"
                    metric_value = co2
            elif layer == 'Competitors':
                metric_label = "CO₂ capacity (t/y)"
                metric_value = co2
            else:
                metric_label = "Capacity (GWh/year)"
                metric_value = cap

        site_data.append({
            "techno": t,
            "layer": layer,
            "category": category,
            "status": r.get(status_col, ""),
            "lat": float(r.get(lat_col)),
            "lon": float(r.get(lon_col)),
            "operator": r.get("operator", ""),
            "production_demand": r.get("production/demand", ""),
            "capacity_gwh_year": cap,
            "capacity_kt_per_year": kt,
            "co2_injection_potential_tpy": co2,
            "site_info": r.get("site_info", ""),
            "municipality": r.get(municipality_col, "") if municipality_col else "",
            "color": color_map.get(t, "#000000"),
            "radius": radius,
            "size_metric_label": metric_label,
            "size_metric_value": metric_value,
            "is_eiffel": is_eiffel,
            "eiffel_project_name": eiffel_project
        })

    # Read Greenhouses data
    greenhouses_data = []
    try:
        greenhouse_df = pd.read_csv("ghg_intensity.csv", sep=";", encoding="latin-1")
        # Create transformer from EPSG:3035 (ETRS89 / LAEA Europe) to WGS84 (EPSG:4326)
        transformer = Transformer.from_crs("EPSG:3035", "EPSG:4326", always_xy=True)
        
        # Find min/max prob_mean for color scaling
        prob_values = []
        for _, row in greenhouse_df.iterrows():
            prob_mean = float(str(row['prob_mean']).replace(',', '.'))
            prob_values.append(prob_mean)
        
        min_prob = min(prob_values)
        max_prob = max(prob_values)
        
        for idx, row in greenhouse_df.iterrows():
            x_center = float(str(row['x_center_m']).replace(',', '.'))
            y_center = float(str(row['y_center_m']).replace(',', '.'))
            prob_mean = float(str(row['prob_mean']).replace(',', '.'))
            
            # Transform from EPSG:3035 to lat/lon (WGS84)
            lon, lat = transformer.transform(x_center, y_center)
            
            # Calculate color based on prob_mean (higher = darker)
            # Normalize prob_mean to 0-1 range
            normalized = (prob_mean - min_prob) / (max_prob - min_prob) if max_prob > min_prob else 0.5
            
            # Generate color from light yellow to dark red
            if normalized < 0.25:
                color = '#FFEB3B'  # Light yellow
            elif normalized < 0.5:
                color = '#FFA726'  # Orange
            elif normalized < 0.75:
                color = '#FF5722'  # Deep orange
            else:
                color = '#B71C1C'  # Dark red
            
            greenhouses_data.append({
                "techno": "Greenhouses",
                "layer": "Offtake",
                "category": "Greenhouses",
                "status": "",
                "lat": lat,
                "lon": lon,
                "operator": "",
                "production_demand": "",
                "capacity_gwh_year": 0,
                "capacity_kt_per_year": 0,
                "co2_injection_potential_tpy": 0,
                "site_info": f"Probability: {prob_mean:.2f}",
                "municipality": "",
                "color": color,
                "radius": 6,
                "size_metric_label": "Probability",
                "size_metric_value": prob_mean,
                "is_eiffel": False,
                "eiffel_project_name": ""
            })
        
        # Add greenhouses to site_data
        site_data.extend(greenhouses_data)
        
        # Add Greenhouses to layer_category_map
        if 'Offtake' not in layer_category_map:
            layer_category_map['Offtake'] = {}
        layer_category_map['Offtake']['Greenhouses'] = ['Greenhouses']
        
        print(f"Loaded {len(greenhouses_data)} Greenhouses points")
    except Exception as e:
        print(f"Warning: Could not load Greenhouses data: {e}")
        import traceback
        traceback.print_exc()
        greenhouses_data = []

    # Heatmap datasets
    biogaz_points = [[s["lat"], s["lon"], 1] for s in site_data if norm(s["techno"]) == "biogaz"]
    biomethane_points = [[s["lat"], s["lon"], 1] for s in site_data if norm(s["techno"]) == "biomethane"]
    
    # Layer-based heatmaps
    supply_points = [[s["lat"], s["lon"], 1] for s in site_data if s["layer"] == "Supply"]
    offtake_points = [[s["lat"], s["lon"], 1] for s in site_data if s["layer"] == "Offtake"]
    competitors_points = [[s["lat"], s["lon"], 1] for s in site_data if s["layer"] == "Competitors"]

    # Bounds
    min_lat, max_lat = float(df[lat_col].min()), float(df[lat_col].max())
    min_lon, max_lon = float(df[lon_col].min()), float(df[lon_col].max())

    html = build_html(
        site_data=site_data,
        color_map=color_map,
        layer_category_map=layer_category_map,
        bounds=(min_lat, min_lon, max_lat, max_lon),
        biogaz_points=biogaz_points,
        biomethane_points=biomethane_points,
        supply_points=supply_points,
        offtake_points=offtake_points,
        competitors_points=competitors_points,
        preselect_status_all=(args.preselect_status == "all"),
        preselect_techno_all=(args.preselect_techno == "all"),
        visibility_mode=args.visibility_mode,
        heat_radius=args.heat_radius,
        heat_blur=args.heat_blur,
    )

    Path(args.out).write_text(html, encoding="utf-8")
    print(f"✅ Wrote {args.out}")


if __name__ == "__main__":
    main()
