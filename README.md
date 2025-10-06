# Interactive Biogas, Biomethane, eFuels & CO₂ Demand Map

This tool generates an interactive HTML map showing production and demand sites for biogas, biomethane, eFuels (e-methanol, e-ammonia), and CO₂ demand sectors across various regions.

## Features

### Visual Differentiation by Technology Family
- **Gas Family** (biomethane, biogaz, bioCO₂): Displayed as **circles** ⚪
- **eFuels Family** (e-methanol, e-ammonia): Displayed as **triangles** 🔺
- **Demand Sectors**: Displayed as **diamonds** 💎

### Sizing Strategy
- **Gas family**: Sized by capacity (GWh/year)
- **eFuels family**: Sized by capacity (kt/year)
- **Demand sectors**: Sized by bioCO₂ injection potential (t/year)

### Eiffel Investment Highlighting
- Click the **"Highlight Eiffel plants"** button to highlight investments made by Eiffel's fund
- Eiffel-invested plants are shown with a gold glow
- Project names are displayed in popup information

### Interactive Controls
- **Collapsible legend panel** (click – / + to collapse/expand)
- **Technology filters**: Select/clear by group (Gas, eFuels, Demand sectors)
- **Status filters**: Filter by operational status
- **Heatmaps**: Toggle biogaz and biomethane density layers
- **Visibility modes**: Control when sites are shown based on techno/status filters

## Usage

```powershell
# Basic usage
python generate_map.py --csv map.csv

# Custom output
python generate_map.py --csv map.csv --out my_map.html

# Full options
python generate_map.py --csv map.csv --out interactive_biogas_map.html `
    --min-radius 5 --max-radius 18 `
    --size-by auto `
    --visibility-mode both `
    --preselect-status all --preselect-techno none `
    --heat-radius 20 --heat-blur 15
```

## Command-Line Options

- `--csv`: Path to input CSV file (required)
- `--out`: Output HTML filename (default: "interactive_biogas_map.html")
- `--min-radius`: Minimum marker radius in pixels (default: 5.0)
- `--max-radius`: Maximum marker radius in pixels (default: 16.0)
- `--size-by`: Sizing metric - "auto", "capacity", or "co2" (default: "auto")
- `--visibility-mode`: Show sites when - "both", "techno", "status", or "either" (default: "both")
- `--preselect-status`: Preselect all statuses - "all" or "none" (default: "all")
- `--preselect-techno`: Preselect technos - "none" or "all" (default: "none")
- `--heat-radius`: Heatmap radius (default: 20)
- `--heat-blur`: Heatmap blur (default: 15)

## CSV Data Format

Required columns:
- `latitude`, `longitude`: Site coordinates
- `techno`: Technology type (biomethane, biogaz, bioCO₂, e-methanol, e-ammonia, etc.)
- `operational_status`: Current status
- `capacite_gwh_year`: Capacity for gas family (GWh/year)
- `capacity_kt_per_year`: Capacity for eFuels family (kt/year)
- `co2_injection_potential_tpy`: CO₂ injection potential for demand sectors (t/year)
- `eiffel`: Binary indicator (1 = Eiffel investment, 0 = not)
- `eiffel_project_name`: Name of Eiffel's internal project (optional)

## Key Improvements
- **Robust numeric parsing**: Handles ranges like "370-450", units, prefixes (>, ~), decimal commas
- **Three marker shapes**: Circles, triangles, and diamonds for visual distinction
- **Eiffel investment tracking**: Highlight and identify plants in Eiffel's portfolio
- **Compact, collapsible UI**: Adjustable legend panel size with collapse toggle
- **Responsive design**: Works on various screen sizes

## Dependencies
- Python 3.7+
- pandas
- numpy

## Generated Output
The tool produces a standalone HTML file with:
- Leaflet.js for mapping
- Leaflet.heat for density overlays
- Interactive filters and controls
- Popup information for each site
- No external dependencies (all libraries loaded from CDN)

---

Created for Eiffel IG - Biogas, Biomethane, eFuels & CO₂ Market Analysis
