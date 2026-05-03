from __future__ import annotations

import os

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)


class SettingsDialog(QDialog):
    """
    Ventana de configuración general de la app.

    Está organizada en secciones:
      • Apariencia → Tema (combo + carpeta de temas externos).
    """

    def __init__(self, theme_manager, colors, parent=None,
                 current_theme_id: str = 'dark',
                 on_theme_change=None):
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.colors = colors
        self.setWindowTitle("Configuración")
        self.setMinimumSize(560, 380)
        self._current_theme_id = current_theme_id
        self._on_theme_change = on_theme_change
        self._build_ui()

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setSpacing(10)

        gb_theme = QGroupBox("Apariencia")
        gl = QVBoxLayout(gb_theme)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Tema:"))
        self.theme_combo = QComboBox()
        self.theme_combo.setMinimumWidth(240)
        self.theme_combo.setToolTip(
            "Cambia el esquema de colores de la app.\n"
            "Se aplica al instante y se recuerda entre sesiones.")
        self._populate_theme_combo()
        self.theme_combo.currentIndexChanged.connect(self._on_combo_changed)
        row1.addWidget(self.theme_combo)
        row1.addStretch()
        gl.addLayout(row1)

        self.theme_desc = QLabel("")
        self.theme_desc.setWordWrap(True)
        self.theme_desc.setFont(QFont('Consolas', 9))
        self.theme_desc.setStyleSheet(f"color: {self.colors['text_dim']};")
        gl.addWidget(self.theme_desc)

        row2 = QHBoxLayout()
        btn_open = QPushButton("📁  Abrir carpeta de temas")
        btn_open.setToolTip(
            "Abre la carpeta donde puedes dejar archivos .json\n"
            "para añadir tus propios temas.")
        btn_open.clicked.connect(self._open_themes_folder)
        row2.addWidget(btn_open)

        btn_reload = QPushButton("🔄  Recargar lista")
        btn_reload.setToolTip(
            "Vuelve a escanear las carpetas de temas tras añadir\n"
            "o quitar archivos .json sin reiniciar la app.")
        btn_reload.clicked.connect(self._reload_themes)
        row2.addWidget(btn_reload)

        btn_export = QPushButton("💾  Exportar tema actual…")
        btn_export.setToolTip(
            "Guarda el tema seleccionado como plantilla .json para\n"
            "que puedas modificarlo y crear el tuyo.")
        btn_export.clicked.connect(self._export_current_theme)
        row2.addWidget(btn_export)

        row2.addStretch()
        gl.addLayout(row2)

        hint = QLabel(
            "Para añadir un tema instalable por separado, deja un archivo .json\n"
            "con el formato indicado en themes/README.md dentro de la carpeta\n"
            "y pulsa «Recargar lista» (o reinicia la app)."
        )
        hint.setWordWrap(True)
        hint.setFont(QFont('Consolas', 8))
        hint.setStyleSheet(f"color: {self.colors['text_dim']};")
        gl.addWidget(hint)

        main.addWidget(gb_theme)
        main.addStretch()

        bbox = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bbox.rejected.connect(self.accept)
        bbox.accepted.connect(self.accept)
        main.addWidget(bbox)

        self._refresh_theme_description()

    def _populate_theme_combo(self):
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        for entry in self.theme_manager.list_themes():
            label = entry['name']
            if entry['source'] != 'builtin':
                label += '  (externo)'
            self.theme_combo.addItem(label, entry['id'])
        idx = self.theme_combo.findData(self._current_theme_id)
        if idx < 0:
            idx = 0
        self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.blockSignals(False)

    def _on_combo_changed(self, _index: int):
        tid = self.theme_combo.currentData()
        if not tid or tid == self._current_theme_id:
            self._refresh_theme_description()
            return
        self._current_theme_id = tid
        if self._on_theme_change:
            self._on_theme_change(tid)
        self._refresh_theme_description()

    def _refresh_theme_description(self):
        tid = self.theme_combo.currentData()
        meta = self.theme_manager.get_theme_meta(tid) if tid else None
        if meta is None:
            self.theme_desc.setText("")
            return
        src = ("Origen: built-in" if meta['source'] == 'builtin'
               else f"Origen: {meta['source']}")
        desc = meta.get('description', '')
        self.theme_desc.setText(f"  {desc}\n  {src}" if desc else f"  {src}")

    def _open_themes_folder(self):
        path = self.theme_manager.ensure_user_themes_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _reload_themes(self):
        self.theme_manager.refresh()
        self._populate_theme_combo()
        self._refresh_theme_description()
        QMessageBox.information(
            self, "Temas recargados",
            f"Se descubrieron {len(self.theme_manager.list_themes())} temas en total."
        )

    def _export_current_theme(self):
        tid = self.theme_combo.currentData()
        if not tid:
            return
        suggested = f"{tid}_copia.json"
        default_dir = self.theme_manager.ensure_user_themes_dir()
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar tema como plantilla",
            os.path.join(default_dir, suggested),
            "Tema JSON (*.json)")
        if not path:
            return
        ok = self.theme_manager.export_theme_template(tid, path)
        if ok:
            QMessageBox.information(
                self, "Tema exportado",
                f"Plantilla guardada en:\n{path}\n\n"
                "Edita los colores y pulsa «Recargar lista» para verlo en el selector.")
        else:
            QMessageBox.warning(
                self, "Error",
                f"No se pudo guardar el archivo:\n{path}")
