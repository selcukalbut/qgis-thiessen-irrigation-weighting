"""Thiessen (Voronoi) area-weighting computation logic.

This module is independent of the QGIS UI; it only uses the PyQGIS core
API, so it can be tested and reused separately from the dialog code.
"""

from qgis.core import (
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsUnitTypes,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant
import processing

_GHOST_FIELD = "__thiessen_ghost__"


class ThiessenError(Exception):
    pass


def _boundary_extent_in_point_crs(point_layer, boundary_layer):
    extent = boundary_layer.extent()
    if boundary_layer.crs() != point_layer.crs():
        transform = QgsCoordinateTransform(
            boundary_layer.crs(), point_layer.crs(), QgsProject.instance()
        )
        extent = transform.transformBoundingBox(extent)
    return extent


def _layer_with_ghost_corner_points(point_layer, boundary_layer):
    """Returns a copy of point_layer with 4 extra "ghost" points added far
    outside the area of interest, so that the Voronoi diagram's bounding
    extent fully covers the boundary layer before clipping. Without this,
    QGIS's Voronoi algorithm only builds the diagram within the input
    points' own bounding box (expanded by the BUFFER %), which can be
    much smaller than the boundary and silently truncate the result to a
    rectangle instead of the real boundary shape.
    """
    points_extent = QgsRectangle(point_layer.extent())
    combined = QgsRectangle(points_extent)
    combined.combineExtentWith(_boundary_extent_in_point_crs(point_layer, boundary_layer))

    margin = max(combined.width(), combined.height(), 1e-9) * 0.5
    expanded = QgsRectangle(
        combined.xMinimum() - margin,
        combined.yMinimum() - margin,
        combined.xMaximum() + margin,
        combined.yMaximum() + margin,
    )

    augmented = QgsVectorLayer(
        f"Point?crs={point_layer.crs().authid()}", "augmented_stations", "memory"
    )
    prov = augmented.dataProvider()
    prov.addAttributes(point_layer.fields())
    prov.addAttributes([QgsField(_GHOST_FIELD, QVariant.Bool)])
    augmented.updateFields()

    ghost_field_idx = augmented.fields().indexOf(_GHOST_FIELD)

    real_feats = []
    for feat in point_layer.getFeatures():
        f = QgsFeature(augmented.fields())
        f.setGeometry(feat.geometry())
        for fld in point_layer.fields():
            f.setAttribute(fld.name(), feat[fld.name()])
        f.setAttribute(ghost_field_idx, False)
        real_feats.append(f)

    ghost_coords = [
        (expanded.xMinimum(), expanded.yMinimum()),
        (expanded.xMaximum(), expanded.yMinimum()),
        (expanded.xMaximum(), expanded.yMaximum()),
        (expanded.xMinimum(), expanded.yMaximum()),
    ]
    ghost_feats = []
    for x, y in ghost_coords:
        f = QgsFeature(augmented.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        f.setAttribute(ghost_field_idx, True)
        ghost_feats.append(f)

    prov.addFeatures(real_feats + ghost_feats)
    augmented.updateExtents()
    return augmented


def _voronoi_algorithm_id():
    """Finds the Voronoi algorithm id, which can vary by QGIS version."""
    from qgis.core import QgsApplication

    registry = QgsApplication.processingRegistry()
    for alg_id in ("native:voronoipolygons", "qgis:voronoipolygons"):
        if registry.algorithmById(alg_id) is not None:
            return alg_id
    raise ThiessenError(
        "The Voronoi Polygons algorithm could not be found in QGIS Processing."
    )


def build_thiessen_layer(point_layer, buffer_percent=0, boundary_layer=None,
                          feedback=None):
    """Generates Thiessen (Voronoi) polygons from a point layer and
    optionally clips them to a boundary polygon.

    Returns: a QgsVectorLayer (memory layer) with the unclipped/clipped
    Voronoi polygons.
    """
    if point_layer is None:
        raise ThiessenError("You must select a point (station) layer.")

    if QgsWkbTypes.geometryType(point_layer.wkbType()) != QgsWkbTypes.PointGeometry:
        raise ThiessenError("The selected layer does not have point geometry.")

    if point_layer.featureCount() < 3:
        raise ThiessenError(
            "At least 3 stations (points) are required to generate Thiessen polygons."
        )

    alg_id = _voronoi_algorithm_id()

    if boundary_layer is not None:
        # Make sure the Voronoi diagram's bounding extent fully covers the
        # boundary before clipping, otherwise the diagram gets truncated to
        # the points' own bounding box (see _layer_with_ghost_corner_points).
        voronoi_source = _layer_with_ghost_corner_points(point_layer, boundary_layer)
    else:
        voronoi_source = point_layer

    result = processing.run(
        alg_id,
        {"INPUT": voronoi_source, "BUFFER": buffer_percent, "OUTPUT": "memory:"},
        feedback=feedback,
    )
    voronoi_layer = result["OUTPUT"]

    if boundary_layer is not None:
        ghost_idx = voronoi_layer.fields().indexOf(_GHOST_FIELD)
        if ghost_idx != -1:
            ghost_ids = [
                feat.id() for feat in voronoi_layer.getFeatures() if feat[_GHOST_FIELD]
            ]
            voronoi_layer.startEditing()
            voronoi_layer.deleteFeatures(ghost_ids)
            voronoi_layer.deleteAttribute(ghost_idx)
            voronoi_layer.commitChanges()

        clip_result = processing.run(
            "native:clip",
            {"INPUT": voronoi_layer, "OVERLAY": boundary_layer, "OUTPUT": "memory:"},
            feedback=feedback,
        )
        voronoi_layer = clip_result["OUTPUT"]

    return voronoi_layer


def compute_area_weighting(voronoi_layer, value_field):
    """Computes the area (ha) of each polygon in the Voronoi layer, adds
    AREA_HA / AREA_PCT / WEIGHT_CONTRIB fields, and returns the
    area-weighted average.

    Returns: (weighted_average, total_area_ha, rows)
      rows: [{"id": fid, "value": v, "area_ha": a, "percent": p, "contribution": c}, ...]
    """
    if value_field not in [f.name() for f in voronoi_layer.fields()]:
        raise ThiessenError(
            f"Field '{value_field}' was not found in the selected layer. "
            "The point layer's attributes may not have been carried over "
            "to the Voronoi output."
        )

    da = QgsDistanceArea()
    da.setSourceCrs(voronoi_layer.crs(), QgsProject.instance().transformContext())
    da.setEllipsoid(QgsProject.instance().ellipsoid())

    areas_ha = {}
    values = {}
    total_area_ha = 0.0

    for feat in voronoi_layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        area_m2 = da.measureArea(geom)
        area_ha = da.convertAreaMeasurement(area_m2, QgsUnitTypes.AreaHectares)
        val = feat[value_field]
        if val is None:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        areas_ha[feat.id()] = area_ha
        values[feat.id()] = val
        total_area_ha += area_ha

    if total_area_ha <= 0:
        raise ThiessenError("Total area is zero; calculation cannot be performed.")

    field_names = [f.name() for f in voronoi_layer.fields()]
    voronoi_layer.startEditing()
    for name, ftype in (
        ("AREA_HA", QVariant.Double),
        ("AREA_PCT", QVariant.Double),
        ("WEIGHT_CONTRIB", QVariant.Double),
    ):
        if name not in field_names:
            voronoi_layer.addAttribute(QgsField(name, ftype))
    voronoi_layer.updateFields()

    idx_area = voronoi_layer.fields().indexOf("AREA_HA")
    idx_pct = voronoi_layer.fields().indexOf("AREA_PCT")
    idx_contrib = voronoi_layer.fields().indexOf("WEIGHT_CONTRIB")

    weighted_sum = 0.0
    rows = []
    for fid, area_ha in areas_ha.items():
        val = values[fid]
        pct = (area_ha / total_area_ha) * 100.0
        contribution = (area_ha * val) / total_area_ha
        weighted_sum += contribution

        voronoi_layer.changeAttributeValue(fid, idx_area, round(area_ha, 4))
        voronoi_layer.changeAttributeValue(fid, idx_pct, round(pct, 4))
        voronoi_layer.changeAttributeValue(fid, idx_contrib, round(contribution, 6))

        rows.append(
            {
                "id": fid,
                "value": val,
                "area_ha": area_ha,
                "percent": pct,
                "contribution": contribution,
            }
        )

    voronoi_layer.commitChanges()
    rows.sort(key=lambda r: r["area_ha"], reverse=True)

    return weighted_sum, total_area_ha, rows


def save_layer_to_disk(layer, output_path):
    """Writes the memory layer to disk as GeoPackage/Shapefile and
    returns a new QgsVectorLayer opened from the written file."""
    save_options = QgsVectorFileWriter.SaveVectorOptions()
    save_options.driverName = "GPKG" if output_path.lower().endswith(".gpkg") else "ESRI Shapefile"

    error = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer,
        output_path,
        QgsProject.instance().transformContext(),
        save_options,
    )
    if error[0] != QgsVectorFileWriter.NoError:
        raise ThiessenError(f"Failed to write layer to disk: {error[1]}")

    saved_layer = QgsVectorLayer(output_path, "Thiessen_Result", "ogr")
    if not saved_layer.isValid():
        raise ThiessenError("The saved layer is invalid.")
    return saved_layer
