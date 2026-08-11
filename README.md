# Thiessen Irrigation Weighting (QGIS Plugin)

Generates Thiessen (Voronoi) polygons from point station data (rainfall,
irrigation water requirement, discharge, etc.), optionally clips them to an
irrigation area boundary, and computes the **area-weighted average** based
on each polygon's area.

Lets you run the Thiessen method from your web-based irrigation app inside
QGIS, with a GUI, on your own vector layers.

## What it does

1. Generates Thiessen polygons from the selected point layer
   (`Processing → native:voronoipolygons`).
2. Optionally clips them to a boundary (irrigation area) polygon
   (`native:clip`).
3. Computes the true area of each cell (ellipsoidal, via
   `QgsDistanceArea`) and adds `AREA_HA`, `AREA_PCT`, `WEIGHT_CONTRIB`
   fields.
4. Computes the area-weighted average as
   `Σ(area_i × value_i) / Σarea_i` and shows it in the UI.
5. Can save the result as a GeoPackage and/or add it directly to the map.

## Installation (for testing)

1. Copy this `thiessen_sulama` folder into your QGIS profile's plugins
   directory:
   - macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   - Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
2. Open QGIS → **Plugins → Manage and Install Plugins** → enable
   **Thiessen Irrigation Weighting** in the "Installed" tab.
3. Click the icon that appears on the toolbar, or use
   **Vector menu → Thiessen Irrigation Weighting**.

Alternative: zip the folder and install it in QGIS via
**Plugins → Install from ZIP**:

```bash
cd qgis-thiessen-sulama-plugin
zip -r thiessen_sulama.zip thiessen_sulama -x "*.pyc" -x "*__pycache__*"
```

## Usage

- **Station (point) layer**: a point layer with a numeric field such as
  rainfall or irrigation water requirement (at least 3 stations).
- **Value field to weight**: a numeric field from the layer above.
- **Irrigation area boundary (optional)**: the polygon the Voronoi cells
  will be clipped to. If left empty, outer cells remain unbounded — in
  that case increasing the **Outer buffer** value is helpful.
- **Output file (optional)**: if left empty, the result is only added as
  a memory layer; if you provide a `.gpkg` path, it is written to disk.

Clicking **Calculate** lists each cell's value, area (ha) and weight (%)
in the table below, and shows the overall area-weighted average at the
top.

## Notes / limitations

- Depending on the QGIS version, the Voronoi algorithm may be registered
  as `native:voronoipolygons` or `qgis:voronoipolygons`; the plugin tries
  both.
- Area is computed ellipsoidally based on the layer's CRS (correct even
  for geographic CRSs), so no extra projection/CRS handling is needed.
- This version was prepared for testing; validation with real QGIS
  environments (different versions, different CRSs, real station data)
  is strongly recommended.

## Publishing to plugins.qgis.org

Update the `repository`, `tracker`, `homepage` fields in `metadata.txt`
with a real Git repository URL, bump the version, test thoroughly before
setting `experimental=False`, and use
[Plugin Reloader](https://plugins.qgis.org/plugins/plugin_reloader/) for
fast reloads during development. Before release, upload the zip via your
developer account on `plugins.qgis.org` and submit it for review.
