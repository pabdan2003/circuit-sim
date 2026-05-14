from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QDialog, QFrame, QGraphicsItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout,
)


PREVIEW_DEFAULT_VALUES = {
    'R': 1000.0,
    'V': 5.0,
    'VAC': 120.0,
    'I': 0.001,
    'C': 1e-6,
    'L': 1e-3,
    'Z': 0.0,
    'D': 1e-14,
    'BJT_NPN': 100.0,
    'BJT_PNP': 100.0,
    'NMOS': 1e-3,
    'PMOS': 1e-3,
    'OPAMP': 1e5,
    'GND': 0.0,
    'NODE': 0.0,
    'FGEN': 5.0,
    'OSC': 0.0,
    'MULTIMETER': 0.0,
    'TL082': 1e5,
}


class ComponentPickerDialog(QDialog):
    """
    Ventana emergente que muestra una lista de componentes de una categoría
    y una preview gráfica del componente seleccionado.
    """

    def __init__(self, category_name: str, components: list[tuple],
                 component_item_cls, colors, parent=None):
        """
        components: lista de tuplas (comp_type, label, symbol_ascii)
        component_item_cls: clase visual usada para renderizar la preview.
        """
        super().__init__(parent)
        self.colors = colors
        self.component_item_cls = component_item_cls
        self.setWindowTitle(f"Seleccionar componente — {category_name}")
        self._components = components
        self._selected_type = None
        self._build_ui()
        self._apply_style()

    def _color(self, key: str, fallback: str) -> str:
        return self.colors.get(key, fallback)

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setSpacing(12)

        left = QVBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.setFont(QFont('Consolas', 10))
        for ctype, label, sym in self._components:
            item = QListWidgetItem(f"{sym}   {label}")
            item.setData(Qt.ItemDataRole.UserRole, ctype)
            self.list_widget.addItem(item)
        self.list_widget.currentRowChanged.connect(self._update_preview)
        self.list_widget.itemDoubleClicked.connect(self.accept)
        left.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        self.place_btn = QPushButton("Colocar")
        self.place_btn.setDefault(True)
        self.place_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self.place_btn)
        btn_row.addWidget(cancel_btn)
        left.addLayout(btn_row)
        layout.addLayout(left, 1)

        right = QVBoxLayout()
        preview_title = QLabel("Vista previa")
        preview_title.setFont(QFont('Consolas', 9, QFont.Weight.Bold))
        preview_title.setStyleSheet(f"color: {self._color('component', '#e94560')};")
        right.addWidget(preview_title)

        self.preview_scene = QGraphicsScene()
        self.preview_view = QGraphicsView(self.preview_scene)
        self.preview_view.setFixedSize(280, 220)
        self.preview_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.preview_view.setBackgroundBrush(
            QBrush(QColor(self._color('bg', '#1a1a2e'))))
        self.preview_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_view.setFrameShape(QFrame.Shape.StyledPanel)
        right.addWidget(self.preview_view)
        right.addStretch()
        layout.addLayout(right, 2)

        self.list_widget.setCurrentRow(0)

    def _update_preview(self, row: int):
        self.preview_scene.clear()
        if row < 0 or row >= self.list_widget.count():
            return
        ctype = self.list_widget.item(row).data(Qt.ItemDataRole.UserRole)
        self._selected_type = ctype

        val = PREVIEW_DEFAULT_VALUES.get(ctype, 1.0)
        item = self.component_item_cls(ctype, f"{ctype}1", val, '', '', '')
        if ctype == 'Z':
            item.z_real = 100.0
            item.z_imag = 50.0

        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, False)

        self.preview_scene.addItem(item)
        br = item.boundingRect()
        self.preview_scene.setSceneRect(br.adjusted(-30, -30, 30, 30))
        self.preview_view.centerOn(item)

    def _apply_style(self):
        panel = self._color('panel', '#16213e')
        text = self._color('text', '#e0e0e0')
        body = self._color('comp_body', '#16213e')
        border = self._color('panel_brd', '#0f3460')
        accent = self._color('component', '#e94560')
        hover = self._color('comp_sel', '#f5a623')
        self.setStyleSheet(f"""
            QDialog {{
                background: {panel};
                color: {text};
            }}
            QLabel {{
                color: {text};
            }}
            QListWidget {{
                background: {body};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 4px;
            }}
            QListWidget::item {{
                padding: 6px;
                border-bottom: 1px solid {border};
            }}
            QListWidget::item:selected {{
                background: {accent};
                color: white;
            }}
            QPushButton {{
                background: {accent};
                color: white;
                border-radius: 4px;
                padding: 6px 16px;
            }}
            QPushButton:hover {{
                background: {hover};
            }}
        """)

    def get_selected_type(self) -> Optional[str]:
        return self._selected_type
