from qgis.core import QgsFieldProxyModel, QgsMapLayerProxyModel, QgsProject
from qgis.gui import QgsFieldComboBox, QgsFileWidget, QgsMapLayerComboBox
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .thiessen_core import (
    ThiessenError,
    build_thiessen_layer,
    compute_area_weighting,
    save_layer_to_disk,
)


class ThiessenSulamaDialog(QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.result_layer = None
        self.setWindowTitle("Irrigation Area Weighting Using the Thiessen Polygon Method")
        self.setMinimumWidth(560)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.point_layer_combo = QgsMapLayerComboBox()
        self.point_layer_combo.setFilters(QgsMapLayerProxyModel.PointLayer)
        self.point_layer_combo.layerChanged.connect(self._on_point_layer_changed)
        form.addRow("Station (point) layer:", self.point_layer_combo)

        self.field_combo = QgsFieldComboBox()
        self.field_combo.setFilters(QgsFieldProxyModel.Numeric)
        form.addRow("Value field to weight:", self.field_combo)

        self.boundary_combo = QgsMapLayerComboBox()
        self.boundary_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.boundary_combo.setAllowEmptyLayer(True)
        self.boundary_combo.setCurrentIndex(0)
        form.addRow("Irrigation area boundary (optional):", self.boundary_combo)

        self.buffer_spin = QDoubleSpinBox()
        self.buffer_spin.setSuffix(" %")
        self.buffer_spin.setRange(0, 100)
        self.buffer_spin.setValue(0)
        self.buffer_spin.setToolTip(
            "Buffer percentage added around the Voronoi diagram so outer "
            "polygons are not left unbounded. You can leave this at 0 if "
            "you selected a boundary layer."
        )
        form.addRow("Outer buffer:", self.buffer_spin)

        self.output_widget = QgsFileWidget()
        self.output_widget.setStorageMode(QgsFileWidget.SaveFile)
        self.output_widget.setFilter("GeoPackage (*.gpkg)")
        self.output_widget.setDialogTitle("Save result layer")
        form.addRow("Output file (optional):", self.output_widget)

        layout.addLayout(form)

        self.add_to_map_check = QCheckBox("Add result layer to map")
        self.add_to_map_check.setChecked(True)
        layout.addWidget(self.add_to_map_check)

        run_row = QHBoxLayout()
        self.run_button = QPushButton("Calculate")
        self.run_button.clicked.connect(self._run)
        run_row.addStretch()
        run_row.addWidget(self.run_button)
        layout.addLayout(run_row)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Feature ID", "Value", "Area (ha)", "Weight (%)"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table)

        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        button_box.button(QDialogButtonBox.Close).clicked.connect(self.reject)
        layout.addWidget(button_box)

        self._on_point_layer_changed(self.point_layer_combo.currentLayer())

    def _on_point_layer_changed(self, layer):
        self.field_combo.setLayer(layer)

    def _run(self):
        point_layer = self.point_layer_combo.currentLayer()
        value_field = self.field_combo.currentField()
        boundary_layer = self.boundary_combo.currentLayer()
        buffer_percent = self.buffer_spin.value()
        output_path = self.output_widget.filePath()

        if point_layer is None:
            QMessageBox.warning(self, "Missing information", "Select a station (point) layer.")
            return
        if not value_field:
            QMessageBox.warning(
                self, "Missing information",
                "Select a numeric field to weight."
            )
            return

        self.run_button.setEnabled(False)
        try:
            voronoi_layer = build_thiessen_layer(
                point_layer,
                buffer_percent=buffer_percent,
                boundary_layer=boundary_layer,
            )
            weighted_avg, total_area_ha, rows = compute_area_weighting(
                voronoi_layer, value_field
            )

            if output_path:
                final_layer = save_layer_to_disk(voronoi_layer, output_path)
            else:
                voronoi_layer.setName("Thiessen_Result")
                final_layer = voronoi_layer

            self.result_layer = final_layer

            if self.add_to_map_check.isChecked():
                QgsProject.instance().addMapLayer(final_layer)
                self.iface.mapCanvas().setExtent(final_layer.extent())
                self.iface.mapCanvas().refresh()

            self._populate_results(weighted_avg, total_area_ha, value_field, rows)

        except ThiessenError as exc:
            QMessageBox.critical(self, "Error", str(exc))
        except Exception as exc:  # noqa: BLE001 - also surface unhandled errors to the user
            QMessageBox.critical(self, "Unexpected error", str(exc))
        finally:
            self.run_button.setEnabled(True)

    def _populate_results(self, weighted_avg, total_area_ha, value_field, rows):
        self.summary_label.setText(
            f"Area-weighted average ({value_field}): {weighted_avg:.4f}\n"
            f"Total area: {total_area_ha:.2f} ha "
            f"({total_area_ha / 100:.2f} km²)  |  Station count: {len(rows)}"
        )

        self.table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            self.table.setItem(row_idx, 0, QTableWidgetItem(str(row["id"])))
            self.table.setItem(row_idx, 1, QTableWidgetItem(f"{row['value']:.3f}"))
            self.table.setItem(row_idx, 2, QTableWidgetItem(f"{row['area_ha']:.2f}"))
            self.table.setItem(row_idx, 3, QTableWidgetItem(f"{row['percent']:.2f}"))
