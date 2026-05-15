"""
TL082UnitDialog — selector de unidad (A / B) al colocar un TL082 en el canvas.
Imita el comportamiento del selector de componentes de Multisim para CIs duales.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QRadioButton, QPushButton, QButtonGroup, QFrame,
)
from PyQt6.QtCore import Qt


class TL082UnitDialog(QDialog):
    """
    Pequeño diálogo modal que pregunta qué unidad del TL082 colocar.

    El TL082 es un CI de 8 pines con dos op-amps idénticos (A y B)
    que comparten los pines de alimentación (V+ = pin 8, V− = pin 4).

    Pinout por unidad
    -----------------
    Unidad A : OUT=1, IN−=2, IN+=3, V−=4(compartido), V+=8(compartido)
    Unidad B : OUT=7, IN−=6, IN+=5, V−=4(compartido), V+=8(compartido)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TL082 — Seleccionar unidad")
        self.setModal(True)
        self.setFixedSize(320, 160)
        self.selected_unit: str = 'A'

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(18, 16, 18, 14)

        # ── Texto informativo ────────────────────────────────────────────
        lbl = QLabel(
            "El <b>TL082</b> contiene <b>dos op-amps</b> en el mismo CI.<br>"
            "¿Qué unidad desea colocar en el esquemático?"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # ── Radios ───────────────────────────────────────────────────────
        radio_row = QHBoxLayout()
        radio_row.setSpacing(24)
        self._grp = QButtonGroup(self)

        self._ra = QRadioButton("Unidad A  (pines 1 · 2 · 3)")
        self._rb = QRadioButton("Unidad B  (pines 7 · 6 · 5)")
        self._ra.setChecked(True)
        self._grp.addButton(self._ra, 0)
        self._grp.addButton(self._rb, 1)
        radio_row.addWidget(self._ra)
        radio_row.addWidget(self._rb)
        layout.addLayout(radio_row)

        # ── Botón ────────────────────────────────────────────────────────
        btn_ok = QPushButton("Colocar")
        btn_ok.setDefault(True)
        btn_ok.setFixedHeight(28)
        btn_ok.clicked.connect(self._accept)
        layout.addWidget(btn_ok)

    # ------------------------------------------------------------------ #
    def _accept(self):
        self.selected_unit = 'A' if self._ra.isChecked() else 'B'
        self.accept()
