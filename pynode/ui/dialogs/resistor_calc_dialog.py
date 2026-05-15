"""Calculadora de código de colores de resistencias.

Soporta 3, 4, 5 y 6 bandas según el estándar IEC 60062 / EIA RS-279.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QRadioButton, QSizePolicy, QVBoxLayout, QWidget,
)


# ──────────────────────────────────────────────────────────────────────
# Tabla maestra de colores (estándar IEC 60062)
#
#   key:        identificador interno
#   label:      nombre en español
#   hex:        color RGB para el dibujo (None = sin banda)
#   digit:      valor cuando se usa como banda dígito (None si no aplica)
#   mult:       multiplicador (None si no aplica)
#   tol:        tolerancia en % (None si no aplica)
#   tempco:     coef. temperatura en ppm/°C (None si no aplica)
# ──────────────────────────────────────────────────────────────────────
ColorRow = Tuple[str, str, Optional[str], Optional[int], Optional[float],
                 Optional[float], Optional[int]]

COLOR_TABLE: List[ColorRow] = [
    # key       label       hex        digit  mult       tol     tempco
    ('black',   'Negro',    '#1a1a1a', 0,     1e0,       None,   250),
    ('brown',   'Marrón',   '#7a3a00', 1,     1e1,       1.0,    100),
    ('red',     'Rojo',     '#c8222b', 2,     1e2,       2.0,    50),
    ('orange',  'Naranja',  '#ff8c00', 3,     1e3,       None,   15),
    ('yellow',  'Amarillo', '#f0d000', 4,     1e4,       None,   25),
    ('green',   'Verde',    '#1ea71e', 5,     1e5,       0.5,    20),
    ('blue',    'Azul',     '#1e6cd0', 6,     1e6,       0.25,   10),
    ('violet',  'Violeta',  '#8c1eff', 7,     1e7,       0.1,    5),
    ('gray',    'Gris',     '#7a7a7a', 8,     1e8,       0.05,   1),
    ('white',   'Blanco',   '#f0f0f0', 9,     1e9,       None,   None),
    ('gold',    'Dorado',   '#cc9c2c', None,  0.1,       5.0,    None),
    ('silver',  'Plateado', '#c0c0c0', None,  0.01,      10.0,   None),
    ('none',    '(ninguno)', None,     None,  None,      20.0,   None),
]

_BY_KEY = {row[0]: row for row in COLOR_TABLE}


def _format_resistance(R: float) -> str:
    """Devuelve R con prefijo SI razonable: 1.5 kΩ, 470 Ω, 2.2 MΩ…"""
    if R is None:
        return "—"
    abs_R = abs(R)
    if abs_R >= 1e9:
        return f"{R/1e9:.3g} GΩ"
    if abs_R >= 1e6:
        return f"{R/1e6:.3g} MΩ"
    if abs_R >= 1e3:
        return f"{R/1e3:.3g} kΩ"
    if abs_R >= 1:
        return f"{R:.3g} Ω"
    if abs_R >= 1e-3:
        return f"{R*1e3:.3g} mΩ"
    return f"{R:.3g} Ω"


def _make_color_icon(hex_color: Optional[str], size: int = 20) -> QIcon:
    """Crea un QIcon cuadrado del color dado. None = patrón rayado."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if hex_color is None:
        # Banda ausente: patrón de rayas diagonales
        p.fillRect(0, 0, size, size, QColor('#2a2a2a'))
        p.setPen(QPen(QColor('#888888'), 2))
        for off in range(-size, size * 2, 6):
            p.drawLine(off, 0, off + size, size)
    else:
        c = QColor(hex_color)
        p.setBrush(QBrush(c))
        p.setPen(QPen(c.darker(140), 1))
        p.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
    p.end()
    return QIcon(pix)


# ──────────────────────────────────────────────────────────────────────
# Widget gráfico de la resistencia con bandas
# ──────────────────────────────────────────────────────────────────────
class ResistorWidget(QWidget):
    """Dibuja una resistencia con sus bandas. Pasivo: la lógica vive en
    el diálogo, este widget solo renderiza."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(440, 130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self._bands: List[Optional[str]] = []   # lista de hex (None = sin banda)
        self._has_tempco_gap = False             # 6 bandas: gap antes de tempco

    def set_bands(self, bands_hex: List[Optional[str]],
                  has_tempco_gap: bool = False):
        self._bands = list(bands_hex)
        self._has_tempco_gap = has_tempco_gap
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        cy = h / 2

        # ── Cuerpo de la resistencia (cilindro tan/beige) ─────────────
        body_w = w * 0.78
        body_h = h * 0.50
        body_x = (w - body_w) / 2
        body_y = cy - body_h / 2

        # Leads (alambres)
        p.setPen(QPen(QColor('#9a9a9a'), 3))
        p.drawLine(0, int(cy), int(body_x + body_h * 0.3), int(cy))
        p.drawLine(int(body_x + body_w - body_h * 0.3), int(cy), w, int(cy))

        # Cuerpo con gradiente
        body_main = QColor('#d8b48a')
        body_dark = QColor('#a8845a')
        grad = QLinearGradient(0, body_y, 0, body_y + body_h)
        grad.setColorAt(0.0, body_main.darker(115))
        grad.setColorAt(0.5, body_main.lighter(105))
        grad.setColorAt(1.0, body_dark)
        p.setBrush(QBrush(grad))
        p.setPen(QPen(body_dark.darker(120), 1))
        rect = QRectF(body_x, body_y, body_w, body_h)
        p.drawRoundedRect(rect, body_h * 0.45, body_h * 0.45)

        if not self._bands:
            return

        # ── Bandas ────────────────────────────────────────────────────
        # Distribución estándar:
        #   • Bandas dígito + multiplicador: agrupadas a la izquierda,
        #     equiespaciadas en la primera mitad del cuerpo.
        #   • Banda(s) de tolerancia/tempco: a la derecha, separadas por
        #     un gap visual del grupo principal.
        n = len(self._bands)
        usable_x = body_x + body_h * 0.55
        usable_w = body_w - body_h * 1.10
        band_w   = body_h * 0.32   # ancho fijo de cada banda
        band_h   = body_h * 0.92
        band_y   = cy - band_h / 2

        # Cuántas bandas a la izquierda (dígitos + multiplicador):
        n_right = 2 if (n == 6 and self._has_tempco_gap) else 1
        n_left  = n - n_right
        if n == 3:
            # 3 bandas: todas izquierda, sin tolerancia
            n_left, n_right = 3, 0

        # Posiciones izquierda: equiespaciadas en el primer ~60%
        left_zone_w = usable_w * 0.58
        if n_left > 0:
            step_l = left_zone_w / n_left
            for i in range(n_left):
                x = usable_x + step_l * (i + 0.5)
                self._draw_band(p, x, band_y, band_w, band_h, self._bands[i])

        # Posiciones derecha
        if n_right >= 1:
            right_zone_x0 = usable_x + usable_w - usable_w * 0.30
            right_zone_w  = usable_w * 0.30
            if n_right == 1:
                x = right_zone_x0 + right_zone_w * 0.5
                self._draw_band(p, x, band_y, band_w, band_h, self._bands[-1])
            else:  # 2 (tol + tempco)
                x_tol = right_zone_x0 + right_zone_w * 0.30
                x_tc  = right_zone_x0 + right_zone_w * 0.85
                self._draw_band(p, x_tol, band_y, band_w, band_h, self._bands[-2])
                self._draw_band(p, x_tc,  band_y, band_w, band_h, self._bands[-1])

    def _draw_band(self, p: QPainter, cx: float, y: float,
                   w: float, h: float, hex_color: Optional[str]):
        if hex_color is None:
            return  # banda ausente: no dibujar nada
        color = QColor(hex_color)
        # Gradiente vertical para sensación de cilindro
        grad = QLinearGradient(0, y, 0, y + h)
        if color.value() < 80:
            grad.setColorAt(0.0, color.lighter(150))
            grad.setColorAt(0.5, color.lighter(115))
            grad.setColorAt(1.0, color)
        else:
            grad.setColorAt(0.0, color.lighter(120))
            grad.setColorAt(0.5, color)
            grad.setColorAt(1.0, color.darker(125))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(cx - w / 2, y, w, h))


# ──────────────────────────────────────────────────────────────────────
# Diálogo principal
# ──────────────────────────────────────────────────────────────────────
class ResistorCalcDialog(QDialog):
    """Calculadora de código de colores. Se abre desde Herramientas."""

    def __init__(self, colors=None, parent=None):
        super().__init__(parent)
        self.colors = colors or {}
        self.setWindowTitle("Calculadora de Código de Colores")
        self.setMinimumSize(580, 540)
        self._n_bands = 4
        self._combos: List[Tuple[QLabel, QComboBox, str]] = []
        self._build_ui()
        self._apply_style()
        self._update_bands_ui()

    # ── Theming (mismo patrón que PowerTriangleDialog) ────────────────
    def _color(self, key: str, fallback: str) -> str:
        return self.colors.get(key, fallback)

    def _apply_style(self):
        bg     = self._color('panel',     '#16213e')
        field  = self._color('bg',        '#1a1a2e')
        text   = self._color('text',      '#e0e0e0')
        dim    = self._color('text_dim',  '#7f8c8d')
        border = self._color('panel_brd', '#0f3460')
        accent = self._color('component', '#e94560')
        self.setStyleSheet(f"""
            QDialog {{ background:{bg}; color:{text}; }}
            QLabel  {{ color:{text}; }}
            QRadioButton {{ color:{text}; padding:4px; }}
            QRadioButton::indicator {{ width:14px; height:14px; }}
            QComboBox {{
                background:{field}; color:{text};
                border:1px solid {border}; border-radius:3px;
                padding:4px 8px; min-height:24px;
            }}
            QComboBox QAbstractItemView {{
                background:{field}; color:{text};
                selection-background-color:{accent};
            }}
            QPushButton {{
                background:{accent}; color:white; border:none;
                border-radius:3px; padding:6px 16px; min-width:90px;
                font-weight:bold;
            }}
            QPushButton:hover {{ background:{self._color('comp_sel', '#f5a623')}; }}
        """)
        self._result_bg     = bg
        self._result_border = border
        self._result_accent = accent
        self._result_dim    = dim

    # ── Construcción UI ───────────────────────────────────────────────
    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(16, 16, 16, 16)
        main.setSpacing(12)

        # Selector de número de bandas
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("<b>Número de bandas:</b>"))
        self._band_btn_group = QButtonGroup(self)
        for n in (3, 4, 5, 6):
            rb = QRadioButton(str(n))
            rb.setProperty('n_bands', n)
            if n == self._n_bands:
                rb.setChecked(True)
            rb.toggled.connect(self._on_band_count_changed)
            self._band_btn_group.addButton(rb)
            sel_row.addWidget(rb)
        sel_row.addStretch(1)
        main.addLayout(sel_row)

        # Resistencia gráfica
        self.resistor_widget = ResistorWidget(self)
        main.addWidget(self.resistor_widget)

        # Combos por banda (se rellena dinámicamente)
        self._combos_layout = QGridLayout()
        self._combos_layout.setHorizontalSpacing(12)
        self._combos_layout.setVerticalSpacing(6)
        main.addLayout(self._combos_layout)

        # Resultado
        self.result_label = QLabel()
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_label.setMinimumHeight(86)
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.TextFormat.RichText)
        # El estilo se inyecta dinámicamente para usar los colores del tema
        self.result_label.setStyleSheet(
            f"background:{self._color('bg', '#1a1a2e')};"
            f"border:1px solid {self._color('panel_brd', '#0f3460')};"
            f"border-radius:4px; padding:10px;"
        )
        main.addWidget(self.result_label)

        main.addStretch(1)

        # Cerrar
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        main.addLayout(btn_row)

    def _on_band_count_changed(self):
        rb = self.sender()
        if rb is None or not rb.isChecked():
            return
        self._n_bands = int(rb.property('n_bands'))
        self._update_bands_ui()

    def _update_bands_ui(self):
        # Limpiar grid actual
        while self._combos_layout.count():
            item = self._combos_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        self._combos.clear()

        # Roles según número de bandas (estándar IEC 60062)
        if self._n_bands == 3:
            roles = ['digit', 'digit', 'mult']
        elif self._n_bands == 4:
            roles = ['digit', 'digit', 'mult', 'tol']
        elif self._n_bands == 5:
            roles = ['digit', 'digit', 'digit', 'mult', 'tol']
        else:  # 6
            roles = ['digit', 'digit', 'digit', 'mult', 'tol', 'tempco']

        # Defaults sensatos: termina en ~1 kΩ ±5%
        digit_defaults_by_n = {
            3: ['brown', 'black'],            # 1, 0   → 10 × mult
            4: ['brown', 'black'],            # 1, 0
            5: ['brown', 'black', 'black'],   # 1, 0, 0
            6: ['brown', 'black', 'black'],
        }
        defaults_other = {
            'mult':   'red'   if self._n_bands <= 4 else 'brown',  # 4-band:×100, 5/6:×10
            'tol':    'gold',                                       # ±5%
            'tempco': 'brown',                                      # 100 ppm
        }

        digit_seq = digit_defaults_by_n[self._n_bands]
        digit_idx = 0
        for i, role in enumerate(roles):
            label = QLabel(self._role_label(role, i, roles))
            combo = QComboBox()
            combo.setIconSize(combo.iconSize())  # asegurar tamaño
            self._populate_combo(combo, role)

            # Default
            if role == 'digit':
                if digit_idx < len(digit_seq):
                    self._select_combo(combo, digit_seq[digit_idx])
                digit_idx += 1
            else:
                self._select_combo(combo, defaults_other[role])

            combo.currentIndexChanged.connect(self._update_result)
            self._combos_layout.addWidget(label, i, 0)
            self._combos_layout.addWidget(combo, i, 1)
            self._combos.append((label, combo, role))

        self._update_result()

    def _role_label(self, role: str, idx: int, roles: List[str]) -> str:
        if role == 'digit':
            digit_count = roles.count('digit')
            digit_idx = roles[:idx + 1].count('digit')
            return f"<b>Banda {idx + 1}</b> – Dígito {digit_idx} de {digit_count}"
        if role == 'mult':
            return f"<b>Banda {idx + 1}</b> – Multiplicador"
        if role == 'tol':
            return f"<b>Banda {idx + 1}</b> – Tolerancia"
        if role == 'tempco':
            return f"<b>Banda {idx + 1}</b> – Coef. Temperatura"
        return f"Banda {idx + 1}"

    def _populate_combo(self, combo: QComboBox, role: str):
        """Llena el combo con todas las opciones válidas para `role`."""
        for key, label, hex_color, digit, mult, tol, tc in COLOR_TABLE:
            if role == 'digit'  and digit  is None: continue
            if role == 'mult'   and mult   is None: continue
            if role == 'tol'    and tol    is None: continue
            if role == 'tempco' and tc     is None: continue
            descr = self._describe_value(role, digit, mult, tol, tc)
            text  = f"{label}    {descr}"
            combo.addItem(_make_color_icon(hex_color), text, key)

    def _describe_value(self, role, digit, mult, tol, tc) -> str:
        if role == 'digit':
            return f"({digit})"
        if role == 'mult':
            return f"× {self._format_mult(mult)}"
        if role == 'tol':
            return f"±{tol}%"
        if role == 'tempco':
            return f"{tc} ppm/°C"
        return ""

    @staticmethod
    def _format_mult(m: float) -> str:
        if m >= 1e9:  return f"{m/1e9:g}G"
        if m >= 1e6:  return f"{m/1e6:g}M"
        if m >= 1e3:  return f"{m/1e3:g}k"
        if m >= 1:    return f"{int(m)}"
        return f"{m:g}"

    def _select_combo(self, combo: QComboBox, key: str):
        for i in range(combo.count()):
            if combo.itemData(i) == key:
                combo.setCurrentIndex(i)
                return

    # ── Cálculo ────────────────────────────────────────────────────────
    def _update_result(self):
        # Recoger keys actuales
        bands_hex: List[Optional[str]] = []
        digits: List[int] = []
        mult: Optional[float] = None
        tol:  Optional[float] = None
        tc:   Optional[int] = None

        for label, combo, role in self._combos:
            key = combo.currentData()
            row = _BY_KEY.get(key)
            if row is None:
                continue
            _, _, hex_color, digit, m, t, ppm = row
            bands_hex.append(hex_color)
            if role == 'digit':
                digits.append(int(digit) if digit is not None else 0)
            elif role == 'mult':
                mult = m
            elif role == 'tol':
                tol = t
            elif role == 'tempco':
                tc = ppm

        # 3-band: tolerancia implícita ±20%
        if self._n_bands == 3 and tol is None:
            tol = 20.0

        # Calcular R
        if not digits or mult is None:
            R = None
        else:
            base = 0
            for d in digits:
                base = base * 10 + d
            R = base * mult

        # Refrescar dibujo
        self.resistor_widget.set_bands(
            bands_hex,
            has_tempco_gap=(self._n_bands == 6),
        )

        # Refrescar texto
        self.result_label.setText(self._format_result(R, tol, tc))

    def _format_result(self, R: Optional[float], tol: Optional[float],
                       tc: Optional[int]) -> str:
        accent = self._result_accent
        dim    = self._result_dim
        if R is None:
            return (f"<div style='color:{dim}; font-size:14pt;'>"
                    "Selecciona los colores…</div>")
        val_str = _format_resistance(R)
        tol_str = f"±{tol:g}%" if tol is not None else "(sin especificar)"
        # Rango por tolerancia
        if tol is not None:
            R_min = R * (1 - tol / 100)
            R_max = R * (1 + tol / 100)
            range_str = (f"<span style='color:{dim}; font-size:9pt;'>"
                         f"Rango: {_format_resistance(R_min)} … "
                         f"{_format_resistance(R_max)}</span>")
        else:
            range_str = ""
        tc_str = ""
        if tc is not None:
            tc_str = (f"<br><span style='color:{dim}; font-size:9pt;'>"
                      f"Coef. temperatura: {tc} ppm/°C</span>")
        return (
            f"<div style='font-family:Consolas,monospace;'>"
            f"<span style='color:{accent}; font-size:20pt; font-weight:bold;'>"
            f"{val_str}</span>"
            f"  <span style='font-size:14pt;'>{tol_str}</span>"
            f"<br>{range_str}{tc_str}"
            f"</div>"
        )
