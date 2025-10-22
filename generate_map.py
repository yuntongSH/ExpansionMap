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


### >>> OPPORTUNITY HEATMAP ADDITION <<<
def compute_opportunity_points(supply_points, offtake_points, competitors_points):
    """
    Compute opportunity heatmap points based on supply + offtake - competitors density.
    
    Args:
        supply_points: List of [lat, lon, weight] for supply sites
        offtake_points: List of [lat, lon, weight] for offtake sites
        competitors_points: List of [lat, lon, weight] for competitor sites
    
    Returns:
        List of [lat_center, lon_center, value] for non-zero opportunity cells
    """
    if not supply_points and not offtake_points:
        return []
    
    # Grid resolution: ~0.15 degrees (~15 km at mid-latitudes) - denser grid for more detail
    GRID_SIZE = 0.15
    
    # Combine all points to determine bounds
    all_points = supply_points + offtake_points + competitors_points
    if not all_points:
        return []
    
    lats = [p[0] for p in all_points]
    lons = [p[1] for p in all_points]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    
    # Add padding
    min_lat -= GRID_SIZE
    max_lat += GRID_SIZE
    min_lon -= GRID_SIZE
    max_lon += GRID_SIZE
    
    # Create grid
    lat_bins = np.arange(min_lat, max_lat + GRID_SIZE, GRID_SIZE)
    lon_bins = np.arange(min_lon, max_lon + GRID_SIZE, GRID_SIZE)
    
    # Initialize density grids
    supply_grid = np.zeros((len(lat_bins)-1, len(lon_bins)-1))
    offtake_grid = np.zeros((len(lat_bins)-1, len(lon_bins)-1))
    competitors_grid = np.zeros((len(lat_bins)-1, len(lon_bins)-1))
    
    # Helper to bin points into grid
    def bin_points(points, grid):
        for lat, lon, weight in points:
            lat_idx = int((lat - min_lat) / GRID_SIZE)
            lon_idx = int((lon - min_lon) / GRID_SIZE)
            if 0 <= lat_idx < grid.shape[0] and 0 <= lon_idx < grid.shape[1]:
                grid[lat_idx, lon_idx] += weight
    
    # Bin all point sets
    bin_points(supply_points, supply_grid)
    bin_points(offtake_points, offtake_grid)
    bin_points(competitors_points, competitors_grid)
    
    # Normalize each grid to 0-1 range
    def normalize_grid(grid):
        max_val = grid.max()
        if max_val > 0:
            return grid / max_val
        return grid
    
    supply_norm = normalize_grid(supply_grid)
    offtake_norm = normalize_grid(offtake_grid)
    competitors_norm = normalize_grid(competitors_grid)
    
    # Compute opportunity: supply + offtake - competitors
    opportunity_grid = supply_norm + offtake_norm - competitors_norm
    
    # Clip negative values to 0
    opportunity_grid = np.maximum(opportunity_grid, 0)
    
    # >>> CONTRAST ENHANCEMENT: Apply power transform for better visibility <<<
    # Apply moderate power transform (1.3) to emphasize high-opportunity areas while keeping more zones visible
    opportunity_grid = np.power(opportunity_grid, 1.3)
    
    # Renormalize to 0-1 range after power transform
    max_val = opportunity_grid.max()
    if max_val > 0:
        opportunity_grid = opportunity_grid / max_val
    
    # Amplify mid-range values to boost contrast and make zones more vivid
    opportunity_grid = np.power(opportunity_grid, 0.8)
    
    # Boost color contrast: exaggerate higher values by raising upper tail
    opportunity_grid = np.power(opportunity_grid, 0.6)
    
    # Renormalize again after contrast boost
    max_val = opportunity_grid.max()
    if max_val > 0:
        opportunity_grid = opportunity_grid / max_val
    
    # Remove background noise: clip values below 5% threshold to zero
    threshold = 0.05
    opportunity_grid[opportunity_grid < threshold] = 0
    
    # --- 🔧 FINAL CONTRAST & SCALING BOOST ---
    # Force top intensities to reach orange/red range in gradient
    # 1️⃣ Boost top intensities non-linearly (brighten the top end even more)
    opportunity_grid = np.power(opportunity_grid, 0.5)
    
    # 2️⃣ Stretch values so the map actually reaches red (multiply by gain factor)
    opportunity_grid *= 1.8
    opportunity_grid = np.clip(opportunity_grid, 0, 1)  # cap to [0,1] range
    
    # 3️⃣ Optional: re-normalize if needed (commented out to preserve the stretch)
    # opportunity_grid /= opportunity_grid.max()
    # --- END FINAL BOOST ---
    # >>> END CONTRAST ENHANCEMENT <<<
    
    # Convert to list of [lat, lon, value] for non-zero cells
    result = []
    for i in range(opportunity_grid.shape[0]):
        for j in range(opportunity_grid.shape[1]):
            value = opportunity_grid[i, j]
            if value > 0:
                # Cell center coordinates
                lat_center = min_lat + (i + 0.5) * GRID_SIZE
                lon_center = min_lon + (j + 0.5) * GRID_SIZE
                result.append([lat_center, lon_center, float(value)])
    
    return result
### <<< END OPPORTUNITY HEATMAP ADDITION <<<


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
    opportunity_points,  # >>> OPPORTUNITY HEATMAP ADDITION <<<
    grid_nodes,  # >>> ELECTRICITY NETWORK ADDITION <<<
    grid_edges,  # >>> ELECTRICITY NETWORK ADDITION <<<
    gas_pipelines,  # >>> GAS NETWORK ADDITION <<<
    preselect_status_all: bool,
    preselect_techno_all: bool,
    visibility_mode: str,
    heat_radius: int,
    heat_blur: int,
    ors_api_key: str = "",
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
  .layer-section-title {{ font-weight: 700; font-size: 17px; color: #222; margin-bottom: 10px; padding-bottom: 6px; border-bottom: 2px solid #ccc; text-transform: uppercase; letter-spacing: 0.5px; display: flex; align-items: center; justify-content: space-between; cursor: pointer; user-select: none; }}
  .layer-section-title:hover {{ opacity: 0.8; }}
  .dropdown-arrow {{ font-size: 18px; transition: transform 0.3s ease; display: inline-block; font-weight: bold; }}
  .dropdown-arrow.collapsed {{ transform: rotate(-90deg); }}
  .layer-section-content {{ overflow: hidden; transition: max-height 0.3s ease; }}
  .layer-section-content.collapsed {{ max-height: 0 !important; }}
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
  .layer-grid {{ border-color: #FF9800; background: linear-gradient(135deg, #fff3e0 0%, #ffe0b2 100%); }}
  .layer-grid .layer-section-title {{ color: #E65100; border-bottom-color: #FF9800; }}
  .layer-gas {{ border-color: #2196F3; background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%); }}
  .layer-gas .layer-section-title {{ color: #0D47A1; border-bottom-color: #2196F3; }}
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
  .btns {{ display:flex; gap:6px; flex-wrap:wrap; margin:4px 0; }}
  .btn {{ font-size:13px; padding:4px 10px; border:1px solid #ddd; border-radius:6px; background:#f8f8f8; cursor:pointer; }}
  .btn-eiffel {{ background:#ffd700; border-color:#cc9900; font-weight: 600; }}
  .btn-eiffel.active {{ background:#ffeb3b; box-shadow: 0 0 8px rgba(255,215,0,0.6); }}
  #controls-body {{ overflow: auto; max-height: calc(75vh - 60px); }}
  .controls.collapsed #controls-body {{ display: none; }}
  /* Search bar styles */
  .search-container {{ margin: 10px 0; padding: 8px; background: linear-gradient(135deg, #f0f4ff 0%, #e8f0fe 100%); border-radius: 8px; border: 1px solid #cce0ff; }}
  #operator-search {{ width: 100%; padding: 10px 12px; border: 2px solid #4285f4; border-radius: 6px; font-size: 14px; outline: none; transition: all 0.2s; box-sizing: border-box; }}
  #operator-search:focus {{ border-color: #1a73e8; box-shadow: 0 0 8px rgba(66, 133, 244, 0.3); }}
  #search-results-info {{ margin-top: 6px; font-size: 12px; color: #1967d2; font-weight: 500; min-height: 18px; }}
  .search-highlight {{ animation: pulse 0.5s ease-in-out; }}
  @keyframes pulse {{ 0%, 100% {{ transform: scale(1); }} 50% {{ transform: scale(1.15); }} }}
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
<div class="controls" id="controls">
  <div class="controls-header">
    <div class="controls-title">Legend & Filters</div>
    <button id="toggle-controls" class="btn" title="Collapse">–</button>
  </div>
  <div id="controls-body">
  
  <!-- Search Bar -->
  <div class="search-container">
    <input type="text" id="operator-search" placeholder="🔍 Search by operator name..." autocomplete="off">
    <div id="search-results-info"></div>
  </div>
  
  <div class="section-title">Techno & Legend</div>
  <div class="subtle"><em>By default, no sites are shown. Select a techno below (statuses are {('preselected' if preselect_status_all else 'not preselected')}). Toggle categories to visualize site locations.</em></div>

  <div class="layer-section layer-opportunity">
    <div class="layer-section-title" onclick="toggleSection('opportunity')">
      <span>🎯 Opportunity Zones</span>
      <span class="dropdown-arrow" id="opportunity-arrow">▼</span>
    </div>
    <div class="layer-section-content" id="opportunity-content">
    
    <!-- >>> OPPORTUNITY HEATMAP ADDITION <<< -->
    <!-- Opportunity Heatmap (composite: supply + offtake - competitors) -->
    <div class="heatmap-control">
      <label class="heatmap-label" for="toggle-opportunity-heat">
        <div class="heatmap-indicator" style="background: linear-gradient(135deg, lime 0%, yellow 40%, orange 70%, red 100%);"></div>
        <span>Opportunity Heatmap</span>
      </label>
      <input type="checkbox" id="toggle-opportunity-heat">
    </div>
    <!-- <<< END OPPORTUNITY HEATMAP ADDITION >>> -->
    
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
  </div>

  <div class="layer-section layer-supply">
    <div class="layer-section-title" onclick="toggleSection('supply')">
      <span>Supply</span>
      <span class="dropdown-arrow" id="supply-arrow">▼</span>
    </div>
    <div class="layer-section-content" id="supply-content">
      <div class="btns">
        <button id="supply-all" class="btn">Select All</button>
        <button id="supply-none" class="btn">Clear All</button>
      </div>
      <div id="layer-supply-content"></div>
    </div>
  </div>

  <div class="layer-section layer-offtake">
    <div class="layer-section-title" onclick="toggleSection('offtake')">
      <span>Offtake</span>
      <span class="dropdown-arrow" id="offtake-arrow">▼</span>
    </div>
    <div class="layer-section-content" id="offtake-content">
      <div class="btns">
        <button id="offtake-all" class="btn">Select All</button>
        <button id="offtake-none" class="btn">Clear All</button>
      </div>
      <div id="layer-offtake-content"></div>
    </div>
  </div>

  <div class="layer-section layer-competitors">
    <div class="layer-section-title" onclick="toggleSection('competitors')">
      <span>Competitors</span>
      <span class="dropdown-arrow" id="competitors-arrow">▼</span>
    </div>
    <div class="layer-section-content" id="competitors-content">
      <div class="btns">
        <button id="competitors-all" class="btn">Select All</button>
        <button id="competitors-none" class="btn">Clear All</button>
      </div>
      <div id="layer-competitors-content"></div>
    </div>
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

  <div class="layer-section layer-grid">
    <div class="layer-section-title" onclick="toggleSection('grid')">
      <span>⚡ Electricity Network</span>
      <span class="dropdown-arrow" id="grid-arrow">▼</span>
    </div>
    <div class="layer-section-content" id="grid-content">
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-grid">
          <div class="heatmap-indicator" style="background: linear-gradient(135deg, #4CAF50 0%, #FF9800 100%);"></div>
          <span>ENTSO-E Transmission System</span>
        </label>
        <input type="checkbox" id="toggle-grid">
      </div>
      <div style="font-size: 11px; color: #666; margin-top: 6px; padding: 0 10px;">
        European electricity transmission grid
      </div>
    </div>
  </div>

  <!-- >>> GAS NETWORK ADDITION >>> -->
  <div class="layer-section layer-gas">
    <div class="layer-section-title" onclick="toggleSection('gas')">
      <span>⛽ Gas Network</span>
      <span class="dropdown-arrow" id="gas-arrow">▼</span>
    </div>
    <div class="layer-section-content" id="gas-content">
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-gas-operating">
          <div class="heatmap-indicator" style="background: linear-gradient(135deg, #1B5E20 0%, #4A148C 100%);"></div>
          <span>Operating Pipelines</span>
        </label>
        <input type="checkbox" id="toggle-gas-operating">
      </div>
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-gas-construction">
          <div class="heatmap-indicator" style="background: linear-gradient(135deg, #2E7D32 0%, #6A1B9A 100%);"></div>
          <span>Under Construction</span>
        </label>
        <input type="checkbox" id="toggle-gas-construction">
      </div>
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-gas-proposed">
          <div class="heatmap-indicator" style="background: linear-gradient(135deg, #388E3C 0%, #7B1FA2 100%);"></div>
          <span>Proposed Projects</span>
        </label>
        <input type="checkbox" id="toggle-gas-proposed">
      </div>
      <div class="heatmap-control">
        <label class="heatmap-label" for="toggle-gas-other">
          <div class="heatmap-indicator" style="background: #757575;"></div>
          <span>Other Status</span>
        </label>
        <input type="checkbox" id="toggle-gas-other">
      </div>
      <div class="layer-info">
        European gas transmission network with hydrogen-ready infrastructure
      </div>
    </div>
  </div>
  <!-- <<< END GAS NETWORK ADDITION <<< -->

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
  const OPPORTUNITY_POINTS = {json.dumps(opportunity_points or [])};  // >>> OPPORTUNITY HEATMAP ADDITION <<<
  const GRID_NODES = {json.dumps(grid_nodes or [])};  // >>> ELECTRICITY NETWORK ADDITION <<<
  const GRID_EDGES = {json.dumps(grid_edges or [])};  // >>> ELECTRICITY NETWORK ADDITION <<<
  const GAS_PIPELINES = {json.dumps(gas_pipelines or [])};  // >>> GAS NETWORK ADDITION <<<

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

  // >>> ELECTRICITY NETWORK ADDITION <<<
  // Electricity Grid Toggle
  let gridLayerGroup = null;
  let gridVisible = false;
  
  // Helper function to extract voltage from symbol string
  function extractVoltage(symbol) {{
    const match = symbol.match(/(\d+)-(\d+)\s*kV/i) || symbol.match(/(\d+)\s*kV/i);
    if (match) {{
      return parseInt(match[1]);
    }}
    // Check for specific high voltage patterns
    if (symbol.includes('380') || symbol.includes('400') || symbol.includes('500') || symbol.includes('700')) {{
      return 400; // High voltage
    }}
    return 150; // Default to medium voltage
  }}
  
  // Color scheme for nodes based on type
  function getNodeColor(symbol) {{
    if (symbol.includes('Hydro')) return '#2196F3';  // Blue for hydro
    if (symbol.includes('Wind')) return '#006660';   // Cyan for wind
    if (symbol.includes('Solar')) return '#D18F41';  // Amber for solar
    if (symbol.includes('Nuclear')) return '#9C27B0'; // Purple for nuclear
    if (symbol.includes('Thermal') || symbol.includes('Gas') || symbol.includes('Coal')) return '#F44336'; // Red for thermal
    if (symbol.includes('Substation')) return '#FF9800'; // Orange for substations
    return '#9E9E9E'; // Gray for other/unknown
  }}
  
  function createGridLayer() {{
    try {{
      console.log(`🔧 Creating grid layer with ${{GRID_NODES.length}} nodes and ${{GRID_EDGES.length}} edges...`);
      const layerGroup = L.layerGroup();
      
      // Statistics for legend
      const stats = {{
        lowVoltage: 0,
        highVoltage: 0,
        nodeTypes: {{}}
      }};
      
      // Add edges (transmission lines)
      let edgeCount = 0;
      GRID_EDGES.forEach(edge => {{
        try {{
          const voltage = extractVoltage(edge.symbol);
          const isHighVoltage = voltage >= 380;
          
          // Color based on voltage: green <380kV, orange ≥380kV
          const lineColor = isHighVoltage ? '#FF6B00' : '#4CAF50';
          const lineWeight = isHighVoltage ? 2 : 1.5;
          
          if (isHighVoltage) stats.highVoltage++;
          else stats.lowVoltage++;
          
          const line = L.polyline(
            [[edge.start_lat, edge.start_lon], [edge.end_lat, edge.end_lon]],
            {{
              color: lineColor,
              weight: lineWeight,
              opacity: 0.7,
              interactive: true
            }}
          );
          line.bindPopup(`<b>Transmission Line</b><br>${{edge.symbol}}<br><b>Voltage:</b> ${{voltage}} kV`);
          layerGroup.addLayer(line);
          edgeCount++;
        }} catch (err) {{
          console.error('Error creating edge:', err, edge);
        }}
      }});
      console.log(`✅ Added ${{edgeCount}} transmission lines (${{stats.highVoltage}} high voltage, ${{stats.lowVoltage}} medium/low voltage)`);
      
      // Add nodes (substations, power plants)
      let nodeCount = 0;
      GRID_NODES.forEach(node => {{
        try {{
          const nodeColor = getNodeColor(node.symbol);
          const nodeSize = node.symbol.includes('Hydro') || node.symbol.includes('Nuclear') ? 5 : 3;
          
          // Track node types for legend
          if (!stats.nodeTypes[node.symbol]) {{
            stats.nodeTypes[node.symbol] = 0;
          }}
          stats.nodeTypes[node.symbol]++;
          
          const marker = L.circleMarker([node.lat, node.lon], {{
            radius: nodeSize,
            color: nodeColor,
            fillColor: nodeColor,
            fillOpacity: 0.8,
            weight: 1
          }});
          marker.bindPopup(`<b>${{node.symbol}}</b><br>Lat: ${{node.lat.toFixed(4)}}, Lon: ${{node.lon.toFixed(4)}}`);
          layerGroup.addLayer(marker);
          nodeCount++;
        }} catch (err) {{
          console.error('Error creating node:', err, node);
        }}
      }});
      console.log(`✅ Added ${{nodeCount}} nodes`);
      
      console.log(`✅ Grid layer created successfully`);
      return layerGroup;
    }} catch (error) {{
      console.error('❌ Error in createGridLayer:', error);
      return null;
    }}
  }}
  
  const toggleGridCheckbox = document.getElementById('toggle-grid');
  console.log('🔍 Toggle grid checkbox:', toggleGridCheckbox);
  
  if (toggleGridCheckbox) {{
    toggleGridCheckbox.addEventListener('change', (e) => {{
      console.log('🖱️ Grid toggle checkbox changed!');
      gridVisible = e.target.checked;
      
      try {{
        if (gridVisible) {{
          if (!gridLayerGroup) {{
            console.log('🔌 Creating electricity grid layer...');
            gridLayerGroup = createGridLayer();
            if (!gridLayerGroup) {{
              console.error('❌ Failed to create grid layer');
              return;
            }}
          }}
          gridLayerGroup.addTo(map);
          // Show legend
          const legend = document.getElementById('grid-legend');
          if (legend) legend.style.display = 'block';
          console.log(`✅ Electricity grid visible (${{GRID_NODES.length}} nodes, ${{GRID_EDGES.length}} edges)`);
        }} else {{
          if (gridLayerGroup) {{
            map.removeLayer(gridLayerGroup);
          }}
          // Hide legend
          const legend = document.getElementById('grid-legend');
          if (legend) legend.style.display = 'none';
          console.log('❌ Electricity grid hidden');
        }}
      }} catch (error) {{
        console.error('❌ Error toggling grid:', error);
      }}
    }});
    console.log('✅ Grid toggle checkbox event listener attached');
  }} else {{
    console.error('❌ Toggle grid checkbox not found!');
  }}
  // <<< END ELECTRICITY NETWORK ADDITION <<<

  // >>> GAS NETWORK ADDITION <<<
  // Gas Network Toggle with separate status controls
  let gasLayerGroups = {{
    operating: null,
    construction: null,
    proposed: null,
    other: null
  }};
  
  // Helper function to get pipeline color based on fuel type (dark colors)
  function getGasPipelineColor(fuel) {{
    return fuel === 'Hydrogen' ? '#4A148C' : '#1B5E20';  // Dark purple for H2, Dark green for Gas
  }}
  
  // Helper function to categorize pipeline status
  function getStatusCategory(status) {{
    if (status === 'operating') return 'operating';
    if (status === 'construction') return 'construction';
    if (status === 'proposed') return 'proposed';
    return 'other';  // cancelled, shelved, mothballed, retired, idle
  }}
  
  function createGasLayerByStatus(statusCategory) {{
    try {{
      console.log(`🔧 Creating gas network layer for status: ${{statusCategory}}...`);
      const layerGroup = L.layerGroup();
      
      let pipelineCount = 0;
      GAS_PIPELINES.forEach(pipeline => {{
        try {{
          if (getStatusCategory(pipeline.status) !== statusCategory) return;
          
          // Use gray color for "other" status, otherwise use fuel-based color
          const color = statusCategory === 'other' ? '#757575' : getGasPipelineColor(pipeline.fuel);
          const weight = statusCategory === 'operating' ? 2.5 : 2;
          const opacity = statusCategory === 'operating' ? 0.8 : 0.6;
          const dashArray = statusCategory === 'proposed' ? '8, 4' : null;
          
          const line = L.polyline(pipeline.coordinates, {{
            color: color,
            weight: weight,
            opacity: opacity,
            dashArray: dashArray,
            interactive: true
          }});
          
          // Build popup content
          let popupContent = `<div style="max-width: 350px;">`;
          popupContent += `<b style="font-size: 14px; color: #0D47A1;">${{pipeline.name}}</b>`;
          if (pipeline.segment && pipeline.segment !== 'N/A') {{
            popupContent += `<br><i style="color: #666;">${{pipeline.segment}}</i>`;
          }}
          popupContent += `<hr style="margin: 8px 0; border: none; border-top: 1px solid #ddd;">`;
          
          // Status and Fuel
          popupContent += `<div style="margin: 6px 0;"><b>Status:</b> ${{pipeline.status}}</div>`;
          popupContent += `<div style="margin: 6px 0;"><b>Fuel Type:</b> ${{pipeline.fuel}}</div>`;
          
          // Countries
          if (pipeline.countries && pipeline.countries !== 'N/A') {{
            popupContent += `<div style="margin: 6px 0;"><b>Countries:</b> ${{pipeline.countries}}</div>`;
          }}
          
          // Ownership
          if (pipeline.owner && pipeline.owner !== 'N/A') {{
            popupContent += `<div style="margin: 6px 0;"><b>Owner:</b> ${{pipeline.owner}}</div>`;
          }}
          if (pipeline.parent && pipeline.parent !== 'N/A') {{
            popupContent += `<div style="margin: 6px 0;"><b>Parent Company:</b> ${{pipeline.parent}}</div>`;
          }}
          
          // Start Year
          if (pipeline.start_year && pipeline.start_year !== 'N/A') {{
            popupContent += `<div style="margin: 6px 0;"><b>Start Year:</b> ${{pipeline.start_year}}</div>`;
          }}
          
          // Capacity
          if (pipeline.capacity && pipeline.capacity !== 'N/A') {{
            const capacityUnit = pipeline.capacity_units ? ` ${{pipeline.capacity_units}}` : '';
            popupContent += `<div style="margin: 6px 0;"><b>Capacity:</b> ${{pipeline.capacity}}${{capacityUnit}}</div>`;
          }}
          
          // Length
          if (pipeline.length && pipeline.length !== 'N/A') {{
            popupContent += `<div style="margin: 6px 0;"><b>Length:</b> ${{pipeline.length}} km</div>`;
          }}
          
          // Diameter
          if (pipeline.diameter && pipeline.diameter !== 'N/A') {{
            const diameterUnit = pipeline.diameter_units ? ` ${{pipeline.diameter_units}}` : '';
            popupContent += `<div style="margin: 6px 0;"><b>Diameter:</b> ${{pipeline.diameter}}${{diameterUnit}}</div>`;
          }}
          
          // Fuel Source
          if (pipeline.fuel_source && pipeline.fuel_source !== 'N/A') {{
            popupContent += `<div style="margin: 6px 0;"><b>Fuel Source:</b> ${{pipeline.fuel_source}}</div>`;
          }}
          
          // Start Location
          if (pipeline.start_location && pipeline.start_location !== 'N/A') {{
            let startText = pipeline.start_location;
            if (pipeline.start_country && pipeline.start_country !== 'N/A') {{
              startText += ` (${{pipeline.start_country}})`;
            }}
            popupContent += `<div style="margin: 6px 0;"><b>Start Location:</b> ${{startText}}</div>`;
          }}
          
          // End Location
          if (pipeline.end_location && pipeline.end_location !== 'N/A') {{
            let endText = pipeline.end_location;
            if (pipeline.end_country && pipeline.end_country !== 'N/A') {{
              endText += ` (${{pipeline.end_country}})`;
            }}
            popupContent += `<div style="margin: 6px 0;"><b>End Location:</b> ${{endText}}</div>`;
          }}
          
          popupContent += `</div>`;
          
          line.bindPopup(popupContent);
          layerGroup.addLayer(line);
          pipelineCount++;
        }} catch (err) {{
          console.error('Error creating pipeline:', err, pipeline);
        }}
      }});
      console.log(`✅ Added ${{pipelineCount}} gas pipeline segments for ${{statusCategory}}`);
      
      return layerGroup;
    }} catch (error) {{
      console.error(`❌ Error in createGasLayerByStatus(${{statusCategory}}):`, error);
      return null;
    }}
  }}
  
  function toggleGasStatus(statusCategory, checkbox) {{
    try {{
      const isChecked = checkbox.checked;
      console.log(`🖱️ Gas ${{statusCategory}} toggle: ${{isChecked}}`);
      
      if (isChecked) {{
        if (!gasLayerGroups[statusCategory]) {{
          console.log(`⛽ Creating gas network layer for ${{statusCategory}}...`);
          gasLayerGroups[statusCategory] = createGasLayerByStatus(statusCategory);
          if (!gasLayerGroups[statusCategory]) {{
            console.error(`❌ Failed to create gas layer for ${{statusCategory}}`);
            return;
          }}
        }}
        gasLayerGroups[statusCategory].addTo(map);
        console.log(`✅ Gas ${{statusCategory}} pipelines visible`);
      }} else {{
        if (gasLayerGroups[statusCategory]) {{
          map.removeLayer(gasLayerGroups[statusCategory]);
        }}
        console.log(`❌ Gas ${{statusCategory}} pipelines hidden`);
      }}
      
      // Update legend visibility
      updateGasLegendVisibility();
    }} catch (error) {{
      console.error(`❌ Error toggling gas ${{statusCategory}}:`, error);
    }}
  }}
  
  function updateGasLegendVisibility() {{
    const legend = document.getElementById('gas-legend');
    if (!legend) return;
    
    // Show legend if any gas layer is visible
    const anyVisible = Object.values(gasLayerGroups).some(layer => layer && map.hasLayer(layer));
    legend.style.display = anyVisible ? 'block' : 'none';
  }}
  
  // Setup event listeners for all gas status toggles
  const gasStatusToggles = {{
    operating: document.getElementById('toggle-gas-operating'),
    construction: document.getElementById('toggle-gas-construction'),
    proposed: document.getElementById('toggle-gas-proposed'),
    other: document.getElementById('toggle-gas-other')
  }};
  
  Object.entries(gasStatusToggles).forEach(([status, checkbox]) => {{
    if (checkbox) {{
      checkbox.addEventListener('change', () => toggleGasStatus(status, checkbox));
      console.log(`✅ Gas ${{status}} toggle event listener attached`);
      
      // Initialize if checked by default
      if (checkbox.checked) {{
        toggleGasStatus(status, checkbox);
      }}
    }} else {{
      console.error(`❌ Toggle gas ${{status}} checkbox not found!`);
    }}
  }});
  // <<< END GAS NETWORK ADDITION <<<

  function getSelected(prefix) {{
    return Array.from(document.querySelectorAll(`input[id^="${{prefix}}-"]`))
      .filter(cb => cb.checked)
      .map(cb => cb.value);
  }}

  function updateVisibleCount() {{
    document.getElementById('visible-count').textContent = markersLayer.getLayers().length;
  }}

  // Toggle collapsible sections
  function toggleSection(sectionName) {{
    const content = document.getElementById(`${{sectionName}}-content`);
    const arrow = document.getElementById(`${{sectionName}}-arrow`);
    
    if (content.classList.contains('collapsed')) {{
      content.classList.remove('collapsed');
      arrow.classList.remove('collapsed');
      content.style.maxHeight = content.scrollHeight + 'px';
    }} else {{
      content.classList.add('collapsed');
      arrow.classList.add('collapsed');
      content.style.maxHeight = '0';
    }}
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

  // OLD ZOOM MODE VARIABLES REMOVED - Now using isochrone mode

  // OLD ZOOM MODE CODE REMOVED - Now using isochrone mode instead

  // Individual heatmap layers
  const heatmaps = {{
    opportunity: null,  // >>> OPPORTUNITY HEATMAP ADDITION <<<
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

  // >>> OPPORTUNITY HEATMAP ADDITION <<<
  // Opportunity Heatmap (composite: supply + offtake - competitors)
  // Adjusted gradient: red appears sooner to match boosted contrast (power 0.5 + 1.8x gain)
  // Orange and red zones now trigger at lower thresholds for better visibility
  document.getElementById('toggle-opportunity-heat').addEventListener('change', (e) => {{
    const opportunityLegend = document.getElementById('opportunity-legend');
    
    if (heatmaps.opportunity) {{
      map.removeLayer(heatmaps.opportunity);
      heatmaps.opportunity = null;
    }}
    if (e.target.checked) {{
      heatmaps.opportunity = L.heatLayer(OPPORTUNITY_POINTS, {{
        radius: 26,           // Slightly larger coverage for visible zones
        blur: 9,              // Crisper definition of peaks
        maxZoom: 12,
        max: 1.0,             // Fixed max for consistent color mapping
        gradient: {{
          0.0: 'rgba(0,0,0,0)',  // Fully transparent background
          0.25: '#ADFF2F',       // Yellow-green (mild opportunity appears sooner)
          0.45: '#FFFF00',       // Bright yellow (moderate)
          0.6: '#FFA500',        // Orange appears sooner (good opportunity)
          0.75: '#FF4500',       // Red-orange (high opportunity)
          1.0: '#FF0000'         // Pure red (maximum opportunity)
        }}
      }});
      heatmaps.opportunity.addTo(map);
      if (opportunityLegend) opportunityLegend.style.display = 'block';
      console.log(`🎯 Opportunity heatmap enabled (${{OPPORTUNITY_POINTS.length}} cells)`);
    }} else {{
      if (opportunityLegend) opportunityLegend.style.display = 'none';
      console.log('🎯 Opportunity heatmap disabled');
    }}
  }});
  // <<< END OPPORTUNITY HEATMAP ADDITION >>>

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

  // ========== ISOCHRONE FUNCTIONALITY ==========
  // Heavy truck driving distance isochrones using OpenRouteService API
  
  const ORS_API_KEY = "{ors_api_key}";
  let zoomMode = false;  // True when zoomed in on a site
  let isochroneEnabled = false;  // True when user toggles isochrone ON
  let isochroneLayers = [];
  let currentFocusSite = null;  // The site currently zoomed in on
  let savedFilteredMarkers = [];  // Store filtered markers before showing all
  
  // Fetch multiple isochrones in a single API call (Heavy Goods Vehicle profile)
  async function fetchTruckIsochronesMultiple(lat, lon, timeMinutesArray) {{
    if (!ORS_API_KEY || ORS_API_KEY === "") {{
      console.error("❌ No OpenRouteService API key provided");
      return null;
    }}
    
    console.log(`🚛 Fetching truck isochrones for: [${{lat}}, ${{lon}}], times: ${{timeMinutesArray.join(', ')}}min`);
    
    const apiUrl = `https://api.openrouteservice.org/v2/isochrones/driving-hgv`;
    
    const body = {{
      locations: [[lon, lat]], // ORS uses [lon, lat] order
      range: timeMinutesArray.map(m => m * 60) // Convert minutes to seconds
    }};
    
    console.log(`📤 Request body: ${{JSON.stringify(body)}}`);
    
    // Try direct API call first (works when served via HTTP/HTTPS)
    try {{
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 30000); // 30 second timeout
      
      const startTime = Date.now();
      const response = await fetch(apiUrl, {{
        method: 'POST',
        headers: {{
          'Authorization': ORS_API_KEY,
          'Content-Type': 'application/json',
          'Accept': 'application/json, application/geo+json'
        }},
        body: JSON.stringify(body),
        signal: controller.signal
      }});
      
      clearTimeout(timeoutId);
      const elapsed = Date.now() - startTime;
      
      console.log(`📥 Response status: ${{response.status}} ${{response.statusText}} (${{elapsed}}ms)`);
      
      if (!response.ok) {{
        const errorText = await response.text();
        console.error(`❌ API error after ${{elapsed}}ms: HTTP ${{response.status}}`);
        console.error(`   Response:`, errorText);
        return null;
      }}
      
      const data = await response.json();
      console.log(`✅ Truck isochrones received (${{elapsed}}ms), features: ${{data.features ? data.features.length : 0}}`);
      return data;
    }} catch (error) {{
      // If direct call fails (likely CORS from file://), try with CORS proxy
      if (error.name === 'TypeError' && error.message.includes('Failed to fetch')) {{
        console.warn(`⚠️ Direct API call failed (likely CORS), trying with proxy...`);
        return await fetchWithProxy(apiUrl, body);
      }}
      console.error(`❌ Exception fetching isochrones:`, error);
      console.error(`   Error: ${{error.message}}`);
      return null;
    }}
  }}
  
  // Fallback: Fetch via CORS proxy (for file:// protocol)
  async function fetchWithProxy(apiUrl, body) {{
    try {{
      const corsProxy = 'https://corsproxy.io/?';
      // Construct full URL with API key as query parameter for proxy
      const urlWithKey = `${{apiUrl}}?api_key=${{ORS_API_KEY}}`;
      const proxyUrl = corsProxy + encodeURIComponent(urlWithKey);
      
      console.log(`📤 Using CORS proxy...`);
      
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 30000);
      
      const startTime = Date.now();
      const response = await fetch(proxyUrl, {{
        method: 'POST',
        headers: {{
          'Content-Type': 'application/json',
          'Accept': 'application/json'
        }},
        body: JSON.stringify(body),
        signal: controller.signal
      }});
      
      clearTimeout(timeoutId);
      const elapsed = Date.now() - startTime;
      
      console.log(`📥 Proxy response status: ${{response.status}} ${{response.statusText}} (${{elapsed}}ms)`);
      
      if (!response.ok) {{
        const errorText = await response.text();
        console.error(`❌ Proxy API error: HTTP ${{response.status}}`);
        console.error(`   Response:`, errorText);
        return null;
      }}
      
      const data = await response.json();
      console.log(`✅ Isochrones via proxy (${{elapsed}}ms), features: ${{data.features ? data.features.length : 0}}`);
      return data;
    }} catch (error) {{
      console.error(`❌ Proxy fetch failed:`, error);
      return null;
    }}
  }}
  
  // Extrapolate a 2-hour isochrone from 30min and 60min data using directional analysis
  function extrapolateIsochrone(iso30, iso60, centerLat, centerLon) {{
    try {{
      if (!iso30.features || !iso60.features || 
          iso30.features.length === 0 || iso60.features.length === 0) {{
        console.error('❌ Invalid isochrone data for extrapolation');
        return null;
      }}
      
      const coords30 = iso30.features[0].geometry.coordinates[0];
      const coords60 = iso60.features[0].geometry.coordinates[0];
      
      console.log(`📐 Extrapolating 2h from ${{coords30.length}} points (30min) and ${{coords60.length}} points (60min)`);
      
      // Helper: Calculate distance between two points
      const distance = (lon1, lat1, lon2, lat2) => {{
        const dx = lon2 - lon1;
        const dy = lat2 - lat1;
        return Math.sqrt(dx * dx + dy * dy);
      }};
      
      // Helper: Get angle from center to point
      const angle = (lon, lat) => Math.atan2(lat - centerLat, lon - centerLon);
      
      // For each point in the 60min isochrone, find its growth pattern
      const coords120 = coords60.map((coord60, i) => {{
        const lon60 = coord60[0];
        const lat60 = coord60[1];
        const bearing = angle(lon60, lat60);
        
        // Find closest point in 30min isochrone with similar bearing
        let closestIdx = 0;
        let minAngleDiff = Infinity;
        
        coords30.forEach((coord30, j) => {{
          const bearing30 = angle(coord30[0], coord30[1]);
          let angleDiff = Math.abs(bearing - bearing30);
          // Handle angle wrapping (-π to π)
          if (angleDiff > Math.PI) angleDiff = 2 * Math.PI - angleDiff;
          
          if (angleDiff < minAngleDiff) {{
            minAngleDiff = angleDiff;
            closestIdx = j;
          }}
        }});
        
        const coord30 = coords30[closestIdx];
        const lon30 = coord30[0];
        const lat30 = coord30[1];
        
        // Calculate distances from center
        const dist30 = distance(centerLon, centerLat, lon30, lat30);
        const dist60 = distance(centerLon, centerLat, lon60, lat60);
        
        // Calculate growth in this specific direction
        // Using linear extrapolation: dist(t) follows a pattern
        // We observe dist(30) and dist(60), extrapolate to dist(120)
        
        // Method: Assume diminishing growth rate (more realistic for roads)
        // Growth from 0->30: dist30
        // Growth from 30->60: (dist60 - dist30)
        // Growth from 60->90: (dist60 - dist30) * 0.9  (diminishing)
        // Growth from 90->120: (dist60 - dist30) * 0.8 (further diminishing)
        
        const growth30to60 = dist60 - dist30;
        const growth60to90 = growth30to60 * 0.85;  // 85% of previous growth
        const growth90to120 = growth30to60 * 0.70; // 70% of initial growth
        
        const dist120 = dist60 + growth60to90 + growth90to120;
        
        // Calculate new coordinates maintaining the bearing
        const lon120 = centerLon + (lon60 - centerLon) * (dist120 / dist60);
        const lat120 = centerLat + (lat60 - centerLat) * (dist120 / dist60);
        
        return [lon120, lat120];
      }});
      
      // Calculate some statistics for logging
      const avgDist30 = coords30.reduce((sum, c) => 
        sum + distance(centerLon, centerLat, c[0], c[1]), 0) / coords30.length;
      const avgDist60 = coords60.reduce((sum, c) => 
        sum + distance(centerLon, centerLat, c[0], c[1]), 0) / coords60.length;
      const avgDist120 = coords120.reduce((sum, c) => 
        sum + distance(centerLon, centerLat, c[0], c[1]), 0) / coords120.length;
      
      const ratio60_30 = avgDist60 / avgDist30;
      const ratio120_60 = avgDist120 / avgDist60;
      
      console.log(`📊 Extrapolation stats:`);
      console.log(`   30min avg: ${{avgDist30.toFixed(4)}}, 60min avg: ${{avgDist60.toFixed(4)}}, 120min avg: ${{avgDist120.toFixed(4)}}`);
      console.log(`   Growth 30→60: ${{ratio60_30.toFixed(2)}}x, Growth 60→120: ${{ratio120_60.toFixed(2)}}x (diminishing)`);
      
      // Create GeoJSON structure matching API response
      return {{
        type: 'FeatureCollection',
        bbox: iso60.bbox,
        features: [{{
          type: 'Feature',
          geometry: {{
            type: 'Polygon',
            coordinates: [coords120]
          }},
          properties: {{
            value: 7200,
            center: [centerLon, centerLat],
            extrapolated: true,
            method: 'directional_diminishing'
          }}
        }}],
        metadata: {{
          attribution: 'Extrapolated from OpenRouteService data using directional analysis',
          query: {{ profile: 'driving-hgv', range: [7200] }}
        }}
      }};
    }} catch (error) {{
      console.error('❌ Error extrapolating isochrone:', error);
      return null;
    }}
  }}
  
  // Enter zoom mode when clicking a site
  function enterZoomMode(site, latlng, marker) {{
    if (zoomMode && currentFocusSite === site) {{
      // Already zoomed on this site, just show popup
      console.log(`📍 Viewing site: ${{site.name}}`);
      marker.openPopup();
      return;
    }}
    
    zoomMode = true;
    currentFocusSite = site;
    console.log(`🔍 Zooming to site: ${{site.name}}`);
    
    // Save current filtered markers
    savedFilteredMarkers = [];
    markersLayer.eachLayer(m => savedFilteredMarkers.push(m));
    
    // Zoom to site (zoom level 11 for close-up view)
    map.setView(latlng, 11, {{ animate: true, duration: 0.6 }});
    
    // Open popup
    setTimeout(() => marker.openPopup(), 700);
    
    // Show toggle button
    const toggle = document.getElementById('isochrone-toggle');
    if (toggle) toggle.style.display = 'block';
  }}
  
  // Fetch and display isochrones for the current focus site
  async function fetchAndShowIsochrones() {{
    if (!currentFocusSite) return;
    
    const site = currentFocusSite;
    console.log(`🚛 Fetching isochrones for: ${{site.name}}`);
    
    // Show ALL sites (so user can explore neighbors)
    // Don't clear - just add any missing markers to preserve click handlers
    const currentMarkers = new Set();
    markersLayer.eachLayer(m => currentMarkers.add(m));
    allMarkers.forEach(m => {{
      if (!currentMarkers.has(m)) {{
        markersLayer.addLayer(m);
      }}
    }});
    updateVisibleCount();
    console.log(`   Showing all sites for exploration`);
    
    // Show legend
    const legend = document.getElementById('isochrone-legend');
    if (legend) legend.style.display = 'block';
    
    // Fetch both isochrones in a single API call (30min, 60min - API limit is 1h)
    const fetchStart = Date.now();
    const isoData = await fetchTruckIsochronesMultiple(site.lat, site.lon, [30, 60]);
    console.log(`⏱️ Fetch completed in ${{Date.now() - fetchStart}}ms`);
    
    // Clear old isochrone layers
    isochroneLayers.forEach(layer => map.removeLayer(layer));
    isochroneLayers = [];
    
    // Draw isochrones if API succeeded
    if (isoData && isoData.features && isoData.features.length > 0) {{
      console.log(`✅ Drawing ${{isoData.features.length}} truck isochrones`);
      
      // Sort features by range value (smallest to largest)
      const sortedFeatures = isoData.features.sort((a, b) => 
        (a.properties.value || 0) - (b.properties.value || 0)
      );
      
      // Draw each isochrone with appropriate styling
      sortedFeatures.forEach((feature, index) => {{
        const timeMinutes = (feature.properties.value || 0) / 60;
        let style;
        
        if (timeMinutes <= 35) {{ // 30min isochrone (light purple)
          style = {{
            color: '#BA68C8',
            fillColor: '#BA68C8',
            fillOpacity: 0.15,
            weight: 2,
            dashArray: '5, 5'
          }};
        }} else {{ // 60min (1h) isochrone (medium purple)
          style = {{
            color: '#9C27B0',
            fillColor: '#9C27B0',
            fillOpacity: 0.15,
            weight: 2,
            dashArray: '5, 5'
          }};
        }}
        
        const layer = L.geoJSON({{
          type: 'FeatureCollection',
          features: [feature]
        }}, {{ 
          style: style,
          interactive: false  // Allow clicks to pass through to markers below
        }}).addTo(map);
        isochroneLayers.push(layer);
      }});
      
      // Extrapolate 120min (2h) isochrone from 30min and 60min data
      if (sortedFeatures.length >= 2) {{
        console.log('📐 Extrapolating 2h isochrone from 30min and 60min data...');
        const iso30 = {{ type: 'FeatureCollection', features: [sortedFeatures[0]] }};
        const iso60 = {{ type: 'FeatureCollection', features: [sortedFeatures[1]] }};
        const iso120 = extrapolateIsochrone(iso30, iso60, site.lat, site.lon);
        
        if (iso120) {{
          const layer120 = L.geoJSON(iso120, {{
            style: {{
              color: '#7B1FA2',
              fillColor: '#7B1FA2',
              fillOpacity: 0.2,
              weight: 3,
              dashArray: '10, 8'
            }},
            interactive: false  // Allow clicks to pass through to markers below
          }}).addTo(map);
          isochroneLayers.push(layer120);
          console.log('✅ 2h extrapolation complete');
        }}
      }}
    }} else {{
      console.warn('❌ Isochrone API call failed');
      alert('Unable to fetch truck isochrones. API may be unavailable or rate limited.');
    }}
  }}
  
  // Hide isochrones
  function hideIsochrones() {{
    console.log('� Hiding isochrones');
    isochroneEnabled = false;
    
    // Remove isochrone layers
    isochroneLayers.forEach(layer => map.removeLayer(layer));
    isochroneLayers = [];
    
    // Hide legend
    const legend = document.getElementById('isochrone-legend');
    if (legend) legend.style.display = 'none';
    
    // Restore filtered markers (hide unselected categories)
    if (savedFilteredMarkers.length > 0) {{
      markersLayer.clearLayers();
      savedFilteredMarkers.forEach(m => markersLayer.addLayer(m));
      updateVisibleCount();
    }}
    
    // Update checkbox
    updateIsochroneToggleUI();
  }}
  
  // Exit zoom mode completely
  function exitZoomMode() {{
    if (!zoomMode) return;
    
    console.log('👋 Exiting zoom mode');
    zoomMode = false;
    currentFocusSite = null;
    
    // Remove isochrone layers
    if (isochroneEnabled) {{
      isochroneEnabled = false;
      isochroneLayers.forEach(layer => map.removeLayer(layer));
      isochroneLayers = [];
      
      // Hide legend
      const legend = document.getElementById('isochrone-legend');
      if (legend) legend.style.display = 'none';
      
      // Update checkbox
      updateIsochroneToggleUI();
    }}
    
    // Hide toggle button
    const toggle = document.getElementById('isochrone-toggle');
    if (toggle) toggle.style.display = 'none';
    
    // Clear saved markers
    savedFilteredMarkers = [];
    
    // Restore filtered view
    applyFilters();
  }}
  
  // Update isochrone toggle UI
  function updateIsochroneToggleUI() {{
    const checkbox = document.getElementById('isochrone-checkbox');
    const toggle = document.getElementById('isochrone-toggle');
    
    if (!checkbox || !toggle) return;
    
    if (isochroneEnabled) {{
      checkbox.innerHTML = '✓';
      checkbox.style.background = '#7B1FA2';
      checkbox.style.color = 'white';
      toggle.style.borderColor = '#7B1FA2';
    }} else {{
      checkbox.innerHTML = '';
      checkbox.style.background = 'white';
      toggle.style.borderColor = '#ccc';
    }}
  }}
  
  // Toggle isochrone on/off
  async function toggleIsochrone() {{
    if (!zoomMode || !currentFocusSite) return;
    
    isochroneEnabled = !isochroneEnabled;
    updateIsochroneToggleUI();
    
    if (isochroneEnabled) {{
      await fetchAndShowIsochrones();
    }} else {{
      hideIsochrones();
    }}
  }}
  
  // Add click handlers to markers
  allMarkers.forEach(m => {{
    m.on('click', (e) => {{
      const site = m._props;
      const latlng = e.latlng;
      enterZoomMode(site, latlng, m);
    }});
  }});
  
  // Exit zoom mode when zooming out
  map.on('zoomend', () => {{
    if (zoomMode && map.getZoom() < 8) {{
      console.log(`🔍 Zoom level ${{map.getZoom()}} < 8: exiting zoom mode`);
      exitZoomMode();
    }}
  }});
  
  // Initial count
  updateVisibleCount();
  
  // ========== SEARCH FUNCTIONALITY ==========
  const searchInput = document.getElementById('operator-search');
  const searchResultsInfo = document.getElementById('search-results-info');
  let searchActive = false;
  let matchingMarkers = [];
  
  function performSearch() {{
    const query = searchInput.value.trim().toLowerCase();
    
    if (query === '') {{
      // Clear search - restore normal filtering
      searchActive = false;
      matchingMarkers = [];
      searchResultsInfo.textContent = '';
      applyFilters();
      return;
    }}
    
    // Search for matching operators
    searchActive = true;
    matchingMarkers = allMarkers.filter(m => {{
      const operator = (m._props.operator || '').toLowerCase();
      return operator.includes(query);
    }});
    
    // Update info
    const count = matchingMarkers.length;
    if (count === 0) {{
      searchResultsInfo.innerHTML = '❌ No sites found';
      searchResultsInfo.style.color = '#d32f2f';
    }} else {{
      // Get unique operators
      const operators = [...new Set(matchingMarkers.map(m => m._props.operator))].filter(o => o && o !== 'N/A');
      const operatorCount = operators.length;
      searchResultsInfo.innerHTML = `✅ Found ${{count}} site(s) from ${{operatorCount}} operator(s)`;
      searchResultsInfo.style.color = '#1b5e20';
    }}
    
    // Show matching sites
    allMarkers.forEach(m => {{
      const matches = matchingMarkers.includes(m);
      if (matches) {{
        if (!markersLayer.hasLayer(m)) {{
          markersLayer.addLayer(m);
        }}
        // Add highlight animation
        const element = m.getElement ? m.getElement() : m._icon;
        if (element) {{
          element.classList.add('search-highlight');
          setTimeout(() => element.classList.remove('search-highlight'), 500);
        }}
      }} else {{
        if (markersLayer.hasLayer(m)) {{
          markersLayer.removeLayer(m);
        }}
      }}
    }});
    
    updateVisibleCount();
    
    // Zoom to matching markers if found
    if (matchingMarkers.length > 0) {{
      const group = L.featureGroup(matchingMarkers);
      map.fitBounds(group.getBounds().pad(0.1));
    }}
  }}
  
  // Search on input (with debouncing for better performance)
  let searchTimeout;
  searchInput.addEventListener('input', () => {{
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(performSearch, 300);
  }});
  
  // Clear search when clicking outside or pressing Escape
  searchInput.addEventListener('keydown', (e) => {{
    if (e.key === 'Escape') {{
      searchInput.value = '';
      performSearch();
    }}
  }});
  
  // Modify applyFilters to respect search mode
  const originalApplyFilters = applyFilters;
  applyFilters = function() {{
    if (searchActive) {{
      // In search mode, don't apply normal filters
      return;
    }}
    originalApplyFilters();
  }};
  // ========== END SEARCH FUNCTIONALITY ==========
  
  // Initialize collapsible sections to be collapsed by default
  ['opportunity', 'supply', 'offtake', 'competitors', 'grid', 'gas'].forEach(section => {{
    const content = document.getElementById(`${{section}}-content`);
    const arrow = document.getElementById(`${{section}}-arrow`);
    if (content && arrow) {{
      content.classList.add('collapsed');
      arrow.classList.add('collapsed');
      content.style.maxHeight = '0';
    }}
  }});
  
  // Create isochrone toggle button
  const isochroneToggle = document.createElement('div');
  isochroneToggle.id = 'isochrone-toggle';
  isochroneToggle.style.cssText = `
    position: absolute;
    bottom: 20px;
    right: 20px;
    z-index: 1000;
    background: white;
    padding: 12px 16px;
    border-radius: 8px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.3);
    cursor: pointer;
    user-select: none;
    transition: all 0.3s ease;
    border: 2px solid #ccc;
    display: none;
  `;
  isochroneToggle.innerHTML = `
    <div style="display: flex; align-items: center; gap: 10px;">
      <div id="isochrone-checkbox" style="
        width: 20px;
        height: 20px;
        border: 2px solid #7B1FA2;
        border-radius: 4px;
        display: flex;
        align-items: center;
        justify-content: center;
        background: white;
        transition: all 0.3s ease;
      "></div>
      <div style="font-weight: 600; color: #333;">Show Isochrone Area</div>
    </div>
  `;
  document.body.appendChild(isochroneToggle);
  
  // Add click handler to the isochrone toggle button
  isochroneToggle.addEventListener('click', toggleIsochrone);
  
  // Create isochrone legend
  const isochroneLegend = document.createElement('div');
  isochroneLegend.id = 'isochrone-legend';
  isochroneLegend.style.cssText = `
    position: absolute;
    bottom: 80px;
    right: 20px;
    z-index: 1000;
    background: rgba(255, 255, 255, 0.95);
    padding: 15px;
    border-radius: 8px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.3);
    font-size: 13px;
    min-width: 200px;
    display: none;
    border: 2px solid #7B1FA2;
  `;
  isochroneLegend.innerHTML = `
    <div style="font-weight: 700; font-size: 15px; color: #7B1FA2; margin-bottom: 10px; border-bottom: 2px solid #7B1FA2; padding-bottom: 5px;">
      🚛 Heavy Truck Drive Time
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 8px 0;">
      <div style="width: 20px; height: 20px; background: rgba(186, 104, 200, 0.3); border: 2px solid #BA68C8; border-radius: 50%;"></div>
      <div>30 minutes</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 8px 0;">
      <div style="width: 20px; height: 20px; background: rgba(156, 39, 176, 0.3); border: 2px solid #9C27B0; border-radius: 50%;"></div>
      <div>1 hour</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 8px 0;">
      <div style="width: 20px; height: 20px; background: rgba(123, 31, 162, 0.3); border: 2px dashed #7B1FA2; border-radius: 50%;"></div>
      <div><strong>2 hours</strong> <span style="font-size: 10px; color: #666;">(extrapolated)</span></div>
    </div>
    <div style="margin-top: 10px; padding-top: 8px; border-top: 1px solid #ddd; font-size: 11px; color: #666; font-style: italic;">
      <span style="font-size: 10px;">2h estimated from 30min & 1h data</span>
    </div>
  `;
  document.body.appendChild(isochroneLegend);
  
  // Create grid legend
  const gridLegend = document.createElement('div');
  gridLegend.id = 'grid-legend';
  gridLegend.style.cssText = `
    position: absolute;
    bottom: 80px;
    right: 20px;
    z-index: 1000;
    background: rgba(255, 255, 255, 0.95);
    padding: 15px;
    border-radius: 8px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.3);
    font-size: 13px;
    min-width: 220px;
    display: none;
    border: 2px solid #FF9800;
  `;
  gridLegend.innerHTML = `
    <div style="font-weight: 700; font-size: 15px; color: #E65100; margin-bottom: 10px; border-bottom: 2px solid #FF9800; padding-bottom: 5px;">
      ⚡ ENTSO-E Transmission Grid
    </div>
    <div style="font-weight: 600; font-size: 12px; color: #555; margin: 10px 0 6px 0;">Transmission Lines:</div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 30px; height: 3px; background: #4CAF50;"></div>
      <div><span style="font-size: 12px;">&lt;380 kV</span> <span style="font-size: 10px; color: #666;">(132-330 kV)</span></div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 30px; height: 3px; background: #FF6B00;"></div>
      <div><span style="font-size: 12px;">≥380 kV</span> <span style="font-size: 10px; color: #666;">(380-700 kV)</span></div>
    </div>
    <div style="font-weight: 600; font-size: 12px; color: #555; margin: 12px 0 6px 0;">Network Nodes:</div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 12px; height: 12px; background: #FF9800; border-radius: 50%;"></div>
      <div style="font-size: 12px;">Substation</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 12px; height: 12px; background: #2196F3; border-radius: 50%;"></div>
      <div style="font-size: 12px;">Hydro Power</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 12px; height: 12px; background: #00BCD4; border-radius: 50%;"></div>
      <div style="font-size: 12px;">Wind Power</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 12px; height: 12px; background: #FFC107; border-radius: 50%;"></div>
      <div style="font-size: 12px;">Solar Power</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 12px; height: 12px; background: #9C27B0; border-radius: 50%;"></div>
      <div style="font-size: 12px;">Nuclear Power</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 12px; height: 12px; background: #F44336; border-radius: 50%;"></div>
      <div style="font-size: 12px;">Thermal Power</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 12px; height: 12px; background: #9E9E9E; border-radius: 50%;"></div>
      <div style="font-size: 12px;">Other/Unknown</div>
    </div>
    <div style="margin-top: 10px; padding-top: 8px; border-top: 1px solid #ddd; font-size: 10px; color: #666; font-style: italic;">
      Data source: ENTSO-E European Network
    </div>
  `;
  document.body.appendChild(gridLegend);
  
  // Create gas network legend
  const gasLegend = document.createElement('div');
  gasLegend.id = 'gas-legend';
  gasLegend.style.cssText = `
    position: absolute;
    bottom: 80px;
    right: 20px;
    z-index: 1000;
    background: rgba(255, 255, 255, 0.95);
    padding: 15px;
    border-radius: 8px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.3);
    font-size: 13px;
    min-width: 240px;
    display: none;
    border: 2px solid #2196F3;
  `;
  gasLegend.innerHTML = `
    <div style="font-weight: 700; font-size: 15px; color: #0D47A1; margin-bottom: 10px; border-bottom: 2px solid #2196F3; padding-bottom: 5px;">
      ⛽ Europe Gas Pipeline Network
    </div>
    <div style="font-weight: 600; font-size: 12px; color: #555; margin: 10px 0 8px 0;">Pipeline Types:</div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 35px; height: 3px; background: #1B5E20;"></div>
      <div style="font-size: 12px;">Natural Gas</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 35px; height: 3px; background: #4A148C;"></div>
      <div style="font-size: 12px;">Hydrogen (H₂)</div>
    </div>
    <div style="font-weight: 600; font-size: 12px; color: #555; margin: 14px 0 8px 0;">Status Legend:</div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 35px; height: 3px; background: #1B5E20; opacity: 0.8;"></div>
      <div style="font-size: 12px;">Operating (solid, thick)</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 35px; height: 2.5px; background: #1B5E20; opacity: 0.6;"></div>
      <div style="font-size: 12px;">Under Construction</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 35px; height: 0; border-top: 2px dashed #1B5E20; opacity: 0.6;"></div>
      <div style="font-size: 12px;">Proposed (dashed)</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 6px 0;">
      <div style="width: 35px; height: 2.5px; background: #757575; opacity: 0.6;"></div>
      <div style="font-size: 12px; color: #666;">Other Status (gray)</div>
    </div>
    <div style="margin-top: 12px; padding-top: 8px; border-top: 1px solid #ddd; font-size: 10px; color: #666; font-style: italic;">
      Data source: Global Energy Monitor<br>Europe Gas Tracker (Jan 2025)
    </div>
  `;
  document.body.appendChild(gasLegend);
  
  // Create opportunity heatmap legend
  const opportunityLegend = document.createElement('div');
  opportunityLegend.id = 'opportunity-legend';
  opportunityLegend.style.cssText = `
    position: absolute;
    bottom: 20px;
    right: 20px;
    z-index: 1000;
    background: rgba(255, 255, 255, 0.95);
    padding: 15px;
    border-radius: 8px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.3);
    font-size: 13px;
    min-width: 200px;
    display: none;
    border: 2px solid #00AA00;
  `;
  opportunityLegend.innerHTML = `
    <div style="font-weight: 700; font-size: 15px; color: #00AA00; margin-bottom: 10px; border-bottom: 2px solid #00AA00; padding-bottom: 5px;">
      🎯 Opportunity Zones
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 8px 0;">
      <div style="width: 20px; height: 20px; background: #ADFF2F; border: 2px solid #9ACD32; border-radius: 50%;"></div>
      <div>Mild Opportunity</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 8px 0;">
      <div style="width: 20px; height: 20px; background: #FFFF00; border: 2px solid #CCCC00; border-radius: 50%;"></div>
      <div>Moderate Opportunity</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 8px 0;">
      <div style="width: 20px; height: 20px; background: #FFA500; border: 2px solid #CC8400; border-radius: 50%;"></div>
      <div>Good Opportunity</div>
    </div>
    <div style="display: flex; align-items: center; gap: 10px; margin: 8px 0;">
      <div style="width: 20px; height: 20px; background: #FF0000; border: 2px solid #CC0000; border-radius: 50%;"></div>
      <div><strong>High Opportunity</strong></div>
    </div>
    <div style="margin-top: 10px; padding-top: 8px; border-top: 1px solid #ddd; font-size: 11px; color: #666; font-style: italic;">
      <span style="font-size: 10px;">Supply + Offtake - Competitors</span>
    </div>
  `;
  document.body.appendChild(opportunityLegend);
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
    
    # OpenRouteService API key for isochrones
    default_ors_key = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjJlZGRmYTM3NTExNzRmMmZhY2U5NWE4YzQ5ZjIwMjI5IiwiaCI6Im11cm11cjY0In0="
    ap.add_argument("--ors-api-key", default=default_ors_key, help="OpenRouteService API key for truck isochrones")
    
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

    ### >>> OPPORTUNITY HEATMAP ADDITION <<<
    # Compute opportunity heatmap: supply + offtake - competitors
    opportunity_points = compute_opportunity_points(
        supply_points, offtake_points, competitors_points
    )
    print(f"Computed {len(opportunity_points)} opportunity heatmap cells")
    ### <<< END OPPORTUNITY HEATMAP ADDITION <<<

    ### >>> ELECTRICITY NETWORK ADDITION <<<
    # Load electricity network nodes and edges
    grid_nodes = []
    grid_edges = []
    try:
        # Load nodes
        nodes_df = pd.read_csv("entsoe_Node.csv", encoding="latin-1")
        for _, row in nodes_df.iterrows():
            grid_nodes.append({
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "symbol": row.get("Symbol", "Substation")
            })
        
        # Load edges
        edges_df = pd.read_csv("entsoe_Edge.csv", encoding="latin-1")
        for _, row in edges_df.iterrows():
            grid_edges.append({
                "start_lat": float(row["start_lat"]),
                "start_lon": float(row["start_lon"]),
                "end_lat": float(row["end_lat"]),
                "end_lon": float(row["end_lon"]),
                "symbol": row.get("Symbol", "Transmission Line")
            })
        
        print(f"Loaded {len(grid_nodes)} electricity grid nodes")
        print(f"Loaded {len(grid_edges)} electricity grid edges")
    except Exception as e:
        print(f"Warning: Could not load electricity network data: {e}")
        grid_nodes = []
        grid_edges = []
    ### <<< END ELECTRICITY NETWORK ADDITION <<<

    ### >>> GAS NETWORK ADDITION <<<
    # Load gas pipeline network from GeoJSON
    gas_pipelines = []
    try:
        import json
        geojson_path = "Europe Gas Tracker/GEM-EGT-Gas-Hydrogen-Pipelines-2025-01.geojson"
        with open(geojson_path, 'r', encoding='utf-8') as f:
            gas_data = json.load(f)
        
        for feature in gas_data['features']:
            props = feature['properties']
            geom = feature['geometry']
            geom_type = geom['type']
            
            # Skip features with no geometry
            if geom_type == 'GeometryCollection' and not geom.get('geometries'):
                continue
            
            # Extract coordinates based on geometry type
            coords_list = []
            if geom_type == 'LineString':
                coords_list = [geom['coordinates']]
            elif geom_type == 'MultiLineString':
                coords_list = geom['coordinates']
            elif geom_type == 'GeometryCollection':
                for sub_geom in geom.get('geometries', []):
                    if sub_geom['type'] == 'LineString':
                        coords_list.append(sub_geom['coordinates'])
                    elif sub_geom['type'] == 'MultiLineString':
                        coords_list.extend(sub_geom['coordinates'])
            
            # Store pipeline data
            for coords in coords_list:
                if coords and len(coords) > 1:
                    gas_pipelines.append({
                        "coordinates": [[lat, lon] for lon, lat in coords],  # Swap to [lat, lon]
                        "name": props.get('PipelineName', 'N/A'),
                        "segment": props.get('SegmentName', 'N/A'),
                        "status": props.get('Status', 'N/A'),
                        "fuel": props.get('Fuel', 'N/A'),
                        "countries": props.get('Countries', 'N/A'),
                        "owner": props.get('Owner', 'N/A'),
                        "parent": props.get('Parent', 'N/A'),
                        "start_year": props.get('StartYear1', 'N/A'),
                        "capacity": props.get('Capacity', 'N/A'),
                        "capacity_units": props.get('CapacityUnits', ''),
                        "length": props.get('LengthMergedKm', 'N/A'),
                        "diameter": props.get('Diameter', 'N/A'),
                        "diameter_units": props.get('DiameterUnits', ''),
                        "fuel_source": props.get('FuelSource', 'N/A'),
                        "start_location": props.get('StartLocation', 'N/A'),
                        "start_country": props.get('StartCountry', 'N/A'),
                        "end_location": props.get('EndLocation', 'N/A'),
                        "end_country": props.get('EndCountry', 'N/A')
                    })
        
        print(f"Loaded {len(gas_pipelines)} gas pipeline segments")
    except Exception as e:
        print(f"Warning: Could not load gas network data: {e}")
        gas_pipelines = []
    ### <<< END GAS NETWORK ADDITION <<<

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
        opportunity_points=opportunity_points,  # >>> OPPORTUNITY HEATMAP ADDITION <<<
        grid_nodes=grid_nodes,  # >>> ELECTRICITY NETWORK ADDITION <<<
        grid_edges=grid_edges,  # >>> ELECTRICITY NETWORK ADDITION <<<
        gas_pipelines=gas_pipelines,  # >>> GAS NETWORK ADDITION <<<
        preselect_status_all=(args.preselect_status == "all"),
        preselect_techno_all=(args.preselect_techno == "all"),
        visibility_mode=args.visibility_mode,
        heat_radius=args.heat_radius,
        heat_blur=args.heat_blur,
        ors_api_key=args.ors_api_key,
    )

    Path(args.out).write_text(html, encoding="utf-8")
    print(f"✅ Wrote {args.out}")


if __name__ == "__main__":
    main()
