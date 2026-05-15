"""
PyNode — Simulador de circuitos open source
GUI principal con canvas drag-and-drop, PyQt6
"""

import sys
import math
import json
import os
import re
import html
import numpy as np
from itertools import combinations
from typing import Optional, List, Dict, Tuple

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGraphicsScene, QGraphicsView, QGraphicsItem, QGraphicsLineItem,
    QGraphicsEllipseItem, QGraphicsTextItem, QGraphicsRectItem,
    QToolBar, QLabel, QDockWidget, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QSplitter, QDialog,
    QLineEdit, QDialogButtonBox, QMessageBox, QStatusBar,
    QGraphicsPathItem, QPushButton, QComboBox, QDoubleSpinBox,
    QGroupBox, QTextEdit, QFileDialog, QCheckBox, QFormLayout,
    QListWidget, QTabWidget, QInputDialog, QStyledItemDelegate, QMenu
)
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath, QPolygonF,
    QAction, QTransform
)
from PyQt6.QtCore import (
    Qt, QPointF, QRectF, QLineF, pyqtSignal, QObject, QSize
)

# Motor MNA
sys.path.insert(0, os.path.dirname(__file__))
from engine import Resistor, VoltageSource, VoltageSourceAC, CurrentSource, Capacitor, Inductor
from engine import Diode, BJT, MOSFET, OpAmp, Impedance, MNASolver
from circuit_analyzer import (
    CircuitAnalyzer, ImplicitBridgeDetector,
    LOGIC_STANDARDS, DEFAULT_STANDARD, AnalysisFlags,
)
from ui.component_metadata import (
    COMPONENT_NODE_LABELS,
    DEFAULT_NODE_LABELS,
    DIGITAL_FLIPFLOP_TYPES,
    DIGITAL_GATE_TYPES,
    FOUR_PIN_NODE_LABELS,
)
from ui.dialogs.component_dialog import ComponentDialog
from ui.dialogs.component_picker_dialog import ComponentPickerDialog
from ui.dialogs.power_triangle_dialog import PowerTriangleDialog
from ui.dialogs.resistor_calc_dialog import ResistorCalcDialog
from ui.dialogs.settings_dialog import SettingsDialog


# ══════════════════════════════════════════════════════════════
# CONSTANTES DE ESTILO Y RECURSOS COMPARTIDOS
# ══════════════════════════════════════════════════════════════
# Reexportados desde ui.style para mantener compatibilidad con el resto
# del código de main.py (y para que el import de este módulo dispare la
# carga del tema inicial).
from ui import style as _style
from ui.style import (
    GRID_SIZE, COMP_W, COMP_H, PIN_RADIUS,
    DEFAULT_LOGIC_STANDARD,
    COLORS, THEME_MANAGER, apply_theme_to_colors,
    _qfont, theme_revision,
    _INITIAL_THEME_ID,
)


# ══════════════════════════════════════════════════════════════
# ÍTEMS GRÁFICOS DEL CANVAS (extraídos)
# ══════════════════════════════════════════════════════════════
from ui.items.component_item import ComponentItem
from ui.items.wire_item import WireItem

        
# ══════════════════════════════════════════════════════════════
# ESCENA DEL CIRCUITO (extraída)
# ══════════════════════════════════════════════════════════════
from ui.scene import CircuitScene, build_engine_components_for_item

# ══════════════════════════════════════════════════════════════
# VENTANA PRINCIPAL
# ══════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    # ── Constantes de simulación live ────────────────────────────────
    _LIVE_TIME_SCALE         = 0.1   # 10x slow-motion deseado a baja frecuencia
    _LIVE_TICK_MS            = 50    # 20 Hz refresh visual
    _LIVE_PANEL_REFRESH_TICKS = 5    # Texto del panel cada N ticks (~250 ms)

    # Tope duro de pasos del solver por tick. Limita el CPU por tick a
    # ~80-150 ms en Python puro, lo que mantiene la UI fluida incluso en
    # circuitos con elementos no-lineales (LED, diodos) que requieren
    # varias iteraciones de Newton-Raphson por paso.
    _LIVE_MAX_STEPS_PER_TICK = 600
    # Muestras por período de la onda más rápida. 12 es suficiente para
    # que el ojo vea una senoidal limpia; más resolución solo gasta CPU.
    _LIVE_SAMPLES_PER_PERIOD = 12

    # Tolerancias del control adaptativo de paso (LTE) en modo live.
    # Estrictas (1e-6, 1e-3) hacen que el solver achique dt en cascada
    # cuando un LED conmuta — relajadas dan <1% de error visual pero
    # corren ~50x más rápido. Display en vivo prioriza fluidez.
    _LIVE_TOL_ABS = 1e-3
    _LIVE_TOL_REL = 5e-2
    _LIVE_NR_TOL  = 1e-4
    _DC_TICK_MS              = 200   # Intervalo del tick DC (igual que antes)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyNode — Simulador de Circuitos")
        self.resize(1280, 800)
        self.solver = MNASolver()
        self._sim_running = False
        self._sim_mode    = 'idle'   # 'idle' | 'dc_tick' | 'live_transient'
        self._sim_all_comps = None
        self._sim_pin_node = None

        # Estado live transient (rellenado en _start_live_transient)
        self._live_state       = None   # dict {x, cap_states, ind_states, t}
        self._live_components  = None   # lista de componentes del motor
        self._live_pin_node    = None
        self._live_freq        = 60.0
        self._live_tick_count  = 0

        # Timer para simulación continua (actualiza LEDs y canvas en vivo)
        from PyQt6.QtCore import QTimer
        self._sim_timer = QTimer(self)
        self._sim_timer.setInterval(self._DC_TICK_MS)
        self._sim_timer.timeout.connect(self._tick_simulation)

        # ── Reloj global para componentes CLK ──────────────────────────────
        # Cada CLK con clk_running=True conmuta su valor cada medio período
        # de _clk_freq_hz. La frecuencia es ajustable desde Herramientas.
        self._clk_freq_hz: float = 1.0
        self._clk_timer = QTimer(self)
        self._clk_timer.timeout.connect(self._tick_clk)
        self._update_clk_timer_interval()

        self._build_ui()
        self._apply_style()
        self._load_demo_circuit()
        self._install_global_shortcuts()

    # ── Atajos globales (funcionan sin importar qué widget tenga foco) ──
    def _install_global_shortcuts(self):
        """Atajos a nivel de aplicación.

        Ctrl+C/X/V/Z se registran como `QShortcut` (funcionan bien con el
        parser de strings de Qt). La ROTACIÓN (Ctrl++ / Ctrl+-) se maneja
        con un `eventFilter` instalado sobre la `QApplication` porque las
        combinaciones con `+` y `-` confunden al parser de QKeySequence
        en algunos backends y dependiendo del layout del teclado pueden
        no disparar el QShortcut. El event filter ve los eventos al
        nivel más bajo y los enruta a la escena activa.
        """
        from PyQt6.QtGui import QShortcut, QKeySequence

        def _msg(text):
            self.statusBar().showMessage(text)

        def _bind_string(seq, fn):
            sh = QShortcut(QKeySequence(seq), self)
            sh.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sh.activated.connect(fn)

        def do_copy():
            sc = self.scene
            if sc is not None and sc.copy_selected():
                _msg("Selección copiada (Ctrl+C)")

        def do_cut():
            sc = self.scene
            if sc is not None and sc.cut_selected():
                _msg("Selección cortada (Ctrl+X)")

        def do_paste():
            sc = self.scene
            if sc is None:
                return
            if sc.paste():
                _msg("Pegado (Ctrl+V)")
            else:
                _msg("Portapapeles vacío")

        def do_undo():
            sc = self.scene
            if sc is None:
                return
            if sc.undo():
                _msg("Acción deshecha (Ctrl+Z)")
            else:
                _msg("Nada que deshacer")

        _bind_string("Ctrl+C", do_copy)
        _bind_string("Ctrl+X", do_cut)
        _bind_string("Ctrl+V", do_paste)
        _bind_string("Ctrl+Z", do_undo)

        # Rotación: event filter a nivel de QApplication.
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event):
        """Filtro unificado:
          • Captura Ctrl+- y Ctrl++ ANTES de que cualquier widget procese el
            KeyPress (instalado a nivel de QApplication).
          • Muestra el menú de herramientas al pasar el cursor sobre
            ``_tools_button``.
        """
        from PyQt6.QtCore import QEvent
        et = event.type()
        if et == QEvent.Type.KeyPress:
            mods = event.modifiers()
            if mods & Qt.KeyboardModifier.ControlModifier:
                k = event.key()
                if k in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                    sc = self.scene
                    if sc is not None and sc.rotate_selected(delta=-90):
                        self.statusBar().showMessage(
                            "Rotado 90° a la izquierda (Ctrl+-)")
                    return True
                if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                    sc = self.scene
                    if sc is not None and sc.rotate_selected(delta=90):
                        self.statusBar().showMessage(
                            "Rotado 90° a la derecha (Ctrl++)")
                    return True
        elif et == QEvent.Type.Enter:
            if getattr(self, '_tools_button', None) is obj:
                obj.showMenu()
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        """Segunda red de seguridad: si por algún motivo el event filter
        no recibe el evento (envío sintético desde QTest, etc.), aún
        capturamos las rotaciones aquí."""
        mod = event.modifiers()
        if mod & Qt.KeyboardModifier.ControlModifier:
            sc = self.scene
            if sc is not None:
                k = event.key()
                if k in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                    if sc.rotate_selected(delta=-90):
                        self.statusBar().showMessage(
                            "Rotado 90° a la izquierda (Ctrl+-)")
                    event.accept()
                    return
                if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                    if sc.rotate_selected(delta=90):
                        self.statusBar().showMessage(
                            "Rotado 90° a la derecha (Ctrl++)")
                    event.accept()
                    return
        super().keyPressEvent(event)

    # ── CLK (oscilador global) ────────────────────────────────────────────
    def _update_clk_timer_interval(self):
        """Configura el intervalo del timer según _clk_freq_hz.
        Half-period en ms: 1/(2f) * 1000.  Mínimo 10 ms para no saturar la GUI.
        """
        f = max(0.001, float(self._clk_freq_hz))
        half_period_ms = max(10, int(1000.0 / (2.0 * f)))
        self._clk_timer.setInterval(half_period_ms)

    def _tick_clk(self):
        """Conmuta los CLK que están en modo automático."""
        any_active = False
        for sheet in self._sheets:
            for it in sheet['scene'].components:
                if it.comp_type == 'CLK' and it.clk_running:
                    it.value = 0.0 if it.value else 1.0
                    it.update()
                    any_active = True
        if not any_active:
            self._clk_timer.stop()
            return
        # Re-disparar simulación en vivo si está activa
        if self._sim_running:
            self._run_simulation_dc(silent=True)

    def _toggle_clk_running(self):
        """Atajo Ctrl+K: invierte el estado oscilando de los CLK seleccionados.
        Si no hay ninguno seleccionado, conmuta todos los CLK del canvas.
        """
        sel = [it for it in self.scene.selectedItems()
               if isinstance(it, ComponentItem) and it.comp_type == 'CLK']
        targets = sel if sel else [
            it for it in self.scene.components if it.comp_type == 'CLK']
        if not targets:
            self.statusBar().showMessage(
                "No hay componentes CLK en la hoja activa.")
            return
        # Si al menos uno está corriendo, los detenemos todos. Si ninguno
        # corre, los iniciamos todos.
        any_running = any(it.clk_running for it in targets)
        for it in targets:
            it.clk_running = not any_running
            it.update()
        # Iniciar/parar el timer global
        any_running_now = any(
            it.clk_running for sheet in self._sheets
            for it in sheet['scene'].components if it.comp_type == 'CLK')
        if any_running_now:
            self._update_clk_timer_interval()
            self._clk_timer.start()
            self.statusBar().showMessage(
                f"CLK activado a {self._clk_freq_hz:g} Hz "
                f"({len(targets)} componente{'s' if len(targets)!=1 else ''})")
        else:
            self._clk_timer.stop()
            self.statusBar().showMessage("CLK detenido.")

    def _set_clk_frequency(self):
        """Diálogo Herramientas → Frecuencia CLK."""
        from PyQt6.QtWidgets import QInputDialog
        f, ok = QInputDialog.getDouble(
            self, "Frecuencia CLK",
            "Frecuencia de oscilación del componente CLK (Hz):",
            self._clk_freq_hz, 0.01, 100000.0, 3)
        if not ok:
            return
        self._clk_freq_hz = f
        self._update_clk_timer_interval()
        self.statusBar().showMessage(f"Frecuencia CLK = {f:g} Hz")

    def _open_circuit_analyzer(self):
        """Abre el analizador de circuitos digitales preservando el estado previo."""
        # Import perezoso: el diálogo importa símbolos de main.py (ComponentItem,
        # WireItem, COLORS, etc.), por eso se carga en el momento de uso para
        # evitar una importación circular en tiempo de carga del módulo.
        from ui.dialogs.circuit_analyzer_dialog import CircuitAnalyzerDialog
        state = getattr(self, '_analyzer_state', None)
        dlg = CircuitAnalyzerDialog(parent=self, initial_state=state)
        dlg.exec()

    def _open_resistor_calculator(self):
        """Abre la calculadora de código de colores de resistencias."""
        dlg = ResistorCalcDialog(colors=COLORS, parent=self)
        dlg.exec()

    def _open_bode_analyzer(self):
        """Abre el analizador de Bode (barrido AC + plots de magnitud y fase).
        No-modal: se puede dejar abierto mientras editas el circuito y
        recalcular al gusto."""
        from ui.dialogs.bode_dialog import BodeDialog
        dlg = BodeDialog(self.scene, COLORS, parent=self)
        dlg.show()

    def _build_tools_button(self):
        """Construye el QToolButton 'Herramientas' con menú desplegable.
        Aparece justo después del botón '+ Hoja' en la toolbar principal.
        """
        from PyQt6.QtWidgets import QToolButton, QMenu
        btn = QToolButton(self)
        btn.setText("Herramientas")
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        menu = QMenu(btn)
        act_freq = QAction("Frecuencia CLK…", self)
        act_freq.triggered.connect(self._set_clk_frequency)
        menu.addAction(act_freq)
        act_analyze = QAction("Analizar Circuito…", self)
        act_analyze.triggered.connect(self._open_circuit_analyzer)
        menu.addAction(act_analyze)
        act_bode = QAction("Análisis de Bode / Transferencia…", self)
        act_bode.triggered.connect(self._open_bode_analyzer)
        menu.addAction(act_bode)
        act_resistor_calc = QAction("Código de colores…", self)
        act_resistor_calc.triggered.connect(self._open_resistor_calculator)
        menu.addAction(act_resistor_calc)
        btn.setMenu(menu)
        # Mostrar el menú también al pasar el cursor (hover)
        btn.installEventFilter(self)
        self._tools_button = btn
        return btn

    # ── Construcción UI ──────────────────────────
    def _build_ui(self):
        # Sistema de hojas (tabs) — cada hoja tiene su propia escena y vista
        self._sheets: List[Dict] = []  # [{scene, view, name}]
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_sheet)
        self.tab_widget.currentChanged.connect(self._on_sheet_changed)

        # El botón para agregar hojas vive en la toolbar principal (+ Hoja)
        # Doble-click en la pestaña para renombrar
        self.tab_widget.tabBarDoubleClicked.connect(self._rename_sheet)

        # Crear la primera hoja
        self._add_sheet(name="Hoja 1")

        # Panel derecho (propiedades + resultados)
        self._build_right_panel()

        # Layout central: tabs + panel derecho
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tab_widget)
        splitter.addWidget(self.right_panel)
        splitter.setSizes([1000, 280])

        self.setCentralWidget(splitter)

        # ── Toolbar PRINCIPAL (fila 1: archivo, zoom, etc.) ──────────────
        self._build_main_toolbar()

        # ── Toolbar SECUNDARIA (fila 2: categorías, herramientas, simulación)
        self._build_component_toolbar()

        # Status bar
        self.statusBar().showMessage("Listo — Selecciona una categoría para colocar componentes")

    # ── Propiedades: scene y view apuntan a la hoja activa ─────
    @property
    def scene(self) -> 'CircuitScene':
        idx = self.tab_widget.currentIndex()
        if idx < 0 or idx >= len(self._sheets):
            return self._sheets[0]['scene']
        return self._sheets[idx]['scene']

    @property
    def view(self) -> QGraphicsView:
        idx = self.tab_widget.currentIndex()
        if idx < 0 or idx >= len(self._sheets):
            return self._sheets[0]['view']
        return self._sheets[idx]['view']

    def _create_scene_view(self) -> Tuple[CircuitScene, QGraphicsView]:
        scene = CircuitScene()
        scene.component_selected.connect(self._on_component_selected)
        scene.status_message.connect(self.statusBar().showMessage)
        scene.logic_state_toggled.connect(self._on_logic_state_toggled)
        scene.instrument_changed.connect(self._on_instrument_changed)

        view = QGraphicsView(scene)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        view.wheelEvent = self._wheel_zoom
        return scene, view

    def _add_sheet(self, name: str = ''):
        if not name:
            idx = len(self._sheets) + 1
            name = f"Hoja {idx}"
        scene, view = self._create_scene_view()
        sheet = {'scene': scene, 'view': view, 'name': name}
        self._sheets.append(sheet)
        tab_idx = self.tab_widget.addTab(view, name)
        self.tab_widget.setCurrentIndex(tab_idx)

    def _close_sheet(self, index: int):
        if len(self._sheets) <= 1:
            self.statusBar().showMessage("No se puede cerrar la última hoja")
            return
        reply = QMessageBox.question(
            self, "Cerrar hoja",
            f"¿Cerrar \"{self._sheets[index]['name']}\"? Se perderán los componentes.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._sheets.pop(index)
            self.tab_widget.removeTab(index)

    def _on_sheet_changed(self, index: int):
        if 0 <= index < len(self._sheets):
            self.statusBar().showMessage(f"Hoja activa: {self._sheets[index]['name']}")

    def _rename_sheet(self, index: int):
        if 0 <= index < len(self._sheets):
            old_name = self._sheets[index]['name']
            new_name, ok = QInputDialog.getText(
                self, "Renombrar hoja", "Nuevo nombre:", text=old_name)
            if ok and new_name.strip():
                self._sheets[index]['name'] = new_name.strip()
                self.tab_widget.setTabText(index, new_name.strip())

    def _build_right_panel(self):
        self.right_panel = QWidget()
        self.right_panel.setFixedWidth(260)
        layout = QVBoxLayout(self.right_panel)
        layout.setContentsMargins(8, 8, 8, 8)

        # Propiedades del componente seleccionado
        prop_label = QLabel("PROPIEDADES")
        prop_label.setFont(_qfont('Consolas', 9, QFont.Weight.Bold))
        layout.addWidget(prop_label)

        self.prop_table = QTableWidget(0, 2)
        self.prop_table.setHorizontalHeaderLabels(["Campo", "Valor"])
        self.prop_table.horizontalHeader().setStretchLastSection(True)
        self.prop_table.setMaximumHeight(200)
        layout.addWidget(self.prop_table)

        # ── Slider de potenciómetro (visible sólo cuando hay un POT seleccionado) ──
        from PyQt6.QtWidgets import QSlider
        self.pot_panel = QWidget()
        pot_layout = QVBoxLayout(self.pot_panel)
        pot_layout.setContentsMargins(0, 4, 0, 4)
        self.pot_label = QLabel("CURSOR DEL POTENCIÓMETRO")
        self.pot_label.setFont(_qfont('Consolas', 8, QFont.Weight.Bold))
        pot_layout.addWidget(self.pot_label)

        self.pot_slider = QSlider(Qt.Orientation.Horizontal)
        self.pot_slider.setRange(0, 1000)        # resolución 0.1%
        self.pot_slider.setValue(500)
        self.pot_slider.setToolTip(
            "Mueve el cursor del potenciómetro en tiempo real.\n"
            "Si la simulación está activa, el efecto se ve al instante.")
        self.pot_slider.valueChanged.connect(self._on_pot_slider)
        pot_layout.addWidget(self.pot_slider)

        self.pot_value_label = QLabel("50.0% — R = ----")
        self.pot_value_label.setFont(_qfont('Consolas', 8))
        pot_layout.addWidget(self.pot_value_label)

        self.pot_panel.setVisible(False)
        layout.addWidget(self.pot_panel)
        self._selected_pot = None   # ComponentItem actualmente seleccionado (POT)

        # Resultados de simulación
        res_label = QLabel("RESULTADOS")
        res_label.setFont(_qfont('Consolas', 9, QFont.Weight.Bold))
        layout.addWidget(res_label)

        self.results_text = QTextEdit()
        self.results_text.setReadOnly(True)
        self.results_text.setFont(_qfont('Consolas', 9))
        layout.addWidget(self.results_text)

        # Botón triángulo de potencia (visible solo tras análisis AC)
        self.btn_power_triangle = QPushButton("📐  Ver Triángulo de Potencia")
        self.btn_power_triangle.setFont(_qfont('Consolas', 9))
        self.btn_power_triangle.setVisible(False)
        self.btn_power_triangle.clicked.connect(self._show_power_triangle)
        layout.addWidget(self.btn_power_triangle)
        self._last_ac_result = None   # guardamos el resultado AC para el popup

    def _build_main_toolbar(self):
        """Barra superior (fila 1): archivo, zoom, etc."""
        tb = self.addToolBar("Principal")
        tb.setMovable(False)
        tb.setObjectName("main_toolbar")

        actions = [
            ("Nuevo",           "Ctrl+N",          self._new_circuit),
            ("Abrir",           "Ctrl+O",          self._open_circuit),
            ("Guardar",         "Ctrl+S",          self._save_circuit),
            ("Guardar como…",   "Ctrl+Shift+S",    self._save_circuit_as),
            ("Exportar SPICE",  "Ctrl+E",          self._export_spice),
            ("|", None, None),
            ("+ Hoja",       "Ctrl+T", lambda: self._add_sheet()),
            ("__TOOLS__", None, None),                    # placeholder Herramientas
            ("Limpiar",      "Ctrl+L", self._clear_circuit),
            # Zoom: sin atajo para no chocar con la rotación Ctrl++/Ctrl+-.
            # El zoom sigue disponible vía botón en la toolbar y Ctrl+rueda.
            ("Zoom +",       None,     lambda: self.view.scale(1.2, 1.2)),
            ("Zoom −",       None,     lambda: self.view.scale(1/1.2, 1/1.2)),
            ("Restablecer",  "Ctrl+0", self._reset_zoom),
        ]
        for name, shortcut, fn in actions:
            if name == '|':
                tb.addSeparator()
                continue
            if name == '__TOOLS__':
                tb.addWidget(self._build_tools_button())
                continue
            act = QAction(name, self)
            if shortcut:
                act.setShortcut(shortcut)
            act.triggered.connect(fn)
            tb.addAction(act)

        # ── Atajo global Ctrl+K para conmutar oscilación de CLK ──────────
        clk_act = QAction("Toggle CLK", self)
        clk_act.setShortcut("Ctrl+K")
        clk_act.triggered.connect(self._toggle_clk_running)
        self.addAction(clk_act)

        # ── Botón Configuración (alineado a la derecha) ──────────────────
        tb.addSeparator()
        from PyQt6.QtWidgets import QSizePolicy
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        act_settings = QAction("⚙ Configuración", self)
        act_settings.setShortcut("Ctrl+,")
        act_settings.setToolTip(
            "Abrir ventana de configuración (temas, preferencias…)")
        act_settings.triggered.connect(self._open_settings_dialog)
        tb.addAction(act_settings)

        self._current_file: Optional[str] = None

    # ── Configuración / Tema ──────────────────────────────────────────────
    def _open_settings_dialog(self):
        """Abre el diálogo de configuración."""
        dlg = SettingsDialog(THEME_MANAGER, COLORS,
                             parent=self,
                             current_theme_id=THEME_MANAGER.load_selection(),
                             on_theme_change=self._apply_theme_change)
        dlg.exec()

    def _apply_theme_change(self, theme_id: str):
        """Callback que el SettingsDialog invoca al elegir un tema."""
        applied = apply_theme_to_colors(theme_id)
        THEME_MANAGER.save_selection(applied)
        self._refresh_theme_in_ui()
        meta = THEME_MANAGER.get_theme_meta(applied)
        if meta:
            self.statusBar().showMessage(f"Tema aplicado: {meta['name']}", 3000)

    def _refresh_theme_in_ui(self):
        """Re-aplica stylesheet y fuerza redibujo del canvas tras cambiar tema."""
        self._apply_style()
        for sheet in self._sheets:
            sheet['scene'].setBackgroundBrush(QBrush(QColor(COLORS['bg'])))
            sheet['scene'].update()
            sheet['view'].viewport().update()

    def _build_component_toolbar(self):
        """Barra secundaria (fila 2): categorías de componentes, herramientas y simulación."""
        # ── FORZAR SALTO DE LÍNEA antes de esta toolbar ──────────────────
        self.addToolBarBreak()

        tb = QToolBar("Componentes", self)
        tb.setMovable(False)
        tb.setObjectName("component_toolbar")
        self.addToolBar(tb)

        # ── Categorías de componentes ────────────────────────────────────
        categories = [
            ("Pasivos", [
                ('R',    'Resistor',      '━┤ZZZ├━'),
                ('POT',  'Potenciómetro', '━┤Z↗├━'),
                ('C',    'Capacitor',     '━┤  ├━'),
                ('L',    'Inductor',      '━⌒⌒⌒━'),
                ('Z',    'Impedancia',    '━┤▭├━'),
                ('XFMR', 'Transformador', '⌇⌇'),
            ]),
            ("Fuentes", [
                ('V',   'Fuente VDC',  '━(+)━'),
                ('VAC', 'Fuente VAC',  '━(~)━'),
                ('I',   'Fuente I',    '━(→)━'),
            ]),
            ("Semiconductores", [
                ('D',       'Diodo',                 '━|▷|━'),
                ('LED',     'LED',                   '━|▷|★'),
                ('BRIDGE',  'Puente rectificador',   '◇'),
                ('BJT_NPN', 'BJT NPN',               '━(NPN)'),
                ('BJT_PNP', 'BJT PNP',               '━(PNP)'),
                ('NMOS',    'MOSFET N',              '━[N]━'),
                ('PMOS',    'MOSFET P',              '━[P]━'),
                ('OPAMP',   'Op-Amp (ideal)',         '━[▷]━'),
                ('TL082',   'TL082 (op-amp dual)',   '━[▷²]━'),
            ]),
            ("Referencia", [
                ('GND',          'Tierra',          '⏚'),
                ('NODE',         'Nodo',            '•'),
                ('NET_LABEL_IN',  'Net Label Entrada', '→▷'),
                ('NET_LABEL_OUT', 'Net Label Salida',  '◁→'),
            ]),
            ("Instrumentos", [
                ('FGEN', 'Generador de funciones', '⎍'),
                ('OSC',  'Osciloscopio (2 canales)', '∿▥'),
                ('MULTIMETER', 'Multímetro', '[V/A]'),
            ]),
            ("Digital", [
                ('AND',       'Puerta AND',     '&'),
                ('OR',        'Puerta OR',      '≥1'),
                ('NOT',       'Inversor NOT',   '○'),
                ('NAND',      'Puerta NAND',    '&̄'),
                ('NOR',       'Puerta NOR',     '≥1̄'),
                ('XOR',       'Puerta XOR',     '=1'),
                ('DFF',       'Flip-flop D',    '▣D'),
                ('JKFF',      'Flip-flop JK',   '▣JK'),
                ('TFF',       'Flip-flop T',    '▣T'),
                ('SRFF',      'Flip-flop SR',   '▣SR'),
                ('COUNTER',   'Contador binario','#'),
                ('MUX2',      'Multiplexor 2:1','⊞'),
                ('ADC_BRIDGE','Puente ADC',     'A→D'),
                ('DAC_BRIDGE','Puente DAC',     'D→A'),
                ('COMPARATOR','Comparador',     'CMP'),
                ('PWM',       'Salida PWM',     '⊓⊓'),
                ('LOGIC_STATE','Estado Lógico',  '0/1'),
                ('CLK',       'Reloj (CLK)',    '⏲'),
            ]),
        ]

        for cat_name, items in categories:
            btn = QPushButton(cat_name)
            btn.setFont(_qfont('Consolas', 9))
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, c=cat_name, it=items: self._show_picker(c, it))
            tb.addWidget(btn)

        tb.addSeparator()

        # ── Herramientas ─────────────────────────────────────────────────
        btn_select = QPushButton("↖ Seleccionar")
        btn_select.setFont(_qfont('Consolas', 9))
        btn_select.setFixedHeight(28)
        btn_select.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_select.clicked.connect(self._set_select_mode)
        tb.addWidget(btn_select)

        btn_wire = QPushButton("✏ Cable")
        btn_wire.setFont(_qfont('Consolas', 9))
        btn_wire.setFixedHeight(28)
        btn_wire.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_wire.clicked.connect(self._set_wire_mode)
        tb.addWidget(btn_wire)

        tb.addSeparator()

        # ── Simulación ─────────────────────────────────────────────────────
        # Estándar lógico fijo: CMOS 5 V (no expuesto en la UI)
        self.run_btn = QPushButton("▶  SIMULAR")
        self.run_btn.setFont(_qfont('Consolas', 10, QFont.Weight.Bold))
        self.run_btn.setFixedHeight(28)
        self.run_btn.setCheckable(True)
        self.run_btn.setToolTip("Detecta automáticamente: DC · AC · Digital · Mixto")
        self.run_btn.clicked.connect(self._toggle_simulation)
        tb.addWidget(self.run_btn)

    # ── Estilo ───────────────────────────────────
    def _apply_style(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {COLORS['bg']};
                color: {COLORS['text']};
                font-family: 'Consolas', monospace;
            }}
            QToolBar {{
                background: {COLORS['toolbar']};
                border-bottom: 1px solid {COLORS['panel_brd']};
                padding: 4px;
                spacing: 6px;
            }}
            QToolBar#component_toolbar {{
                background: {COLORS['panel']};
                border-bottom: 2px solid {COLORS['panel_brd']};
            }}
            QToolBar QToolButton {{
                color: {COLORS['text']};
                padding: 4px 10px;
                font-family: 'Consolas';
            }}
            QPushButton {{
                background: {COLORS['comp_body']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['panel_brd']};
                border-radius: 4px;
                padding: 5px 12px;
                text-align: left;
            }}
            QPushButton:hover  {{ background: {COLORS['toolbar']}; }}
            QPushButton:checked {{ background: {COLORS['component']}; color: white; }}
            QPushButton#run    {{ background: {COLORS['component']}; color: white; font-weight: bold; }}
            QTableWidget {{
                background: {COLORS['comp_body']};
                color: {COLORS['text']};
                gridline-color: {COLORS['panel_brd']};
                border: 1px solid {COLORS['panel_brd']};
            }}
            QHeaderView::section {{
                background: {COLORS['toolbar']};
                color: {COLORS['text']};
                border: none; padding: 4px;
            }}
            QTextEdit {{
                background: {COLORS['comp_body']};
                color: {COLORS['current']};
                border: 1px solid {COLORS['panel_brd']};
                border-radius: 4px;
            }}
            QScrollBar:vertical, QScrollBar:horizontal {{
                background: {COLORS['comp_body']}; width: 8px; height: 8px;
            }}
            QScrollBar::handle {{ background: {COLORS['panel_brd']}; border-radius: 4px; }}
            QStatusBar {{ background: {COLORS['toolbar']}; color: {COLORS['text_dim']}; }}
            QSplitter::handle {{ background: {COLORS['panel_brd']}; width: 1px; }}
            QGroupBox {{
                font-family: 'Consolas', monospace;
                margin-top: 6px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 6px;
                padding: 0 4px;
            }}
        """)

    # ── Modos ────────────────────────────────────
    def _show_picker(self, category_name: str, items: List[tuple]):
        """Abre el diálogo de selección y, si se acepta, activa el modo colocación."""
        dialog = ComponentPickerDialog(category_name, items, ComponentItem, COLORS, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            ctype = dialog.get_selected_type()
            if ctype:
                self._set_place_mode(ctype)

    def _set_place_mode(self, comp_type: str):
        self.scene.set_mode(f'place_{comp_type}')
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.statusBar().showMessage(f"Click en el canvas para colocar: {comp_type}")

    def _set_wire_mode(self):
        self.scene.set_mode('wire')
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.statusBar().showMessage("Wire: click para iniciar, click para terminar, ESC para cancelar")

    def _set_select_mode(self):
        self.scene.set_mode('select')
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.statusBar().showMessage("Modo selección")

    def _on_sim_mode_changed(self, mode: str):
        pass  # modo automático — no se usa

    # ── Simulación automática ─────────────────────────────────────────────
    def _merge_all_sheets(self) -> Tuple[List[ComponentItem], Dict[str, str]]:
        """Combina componentes y netlist de todas las hojas para simulación multi-hoja.

        Cada hoja ya resuelve sus propios net labels internamente (via extract_netlist).
        Este método solo necesita unificar los nets ENTRE hojas distintas cuando el
        mismo sheet_label aparece en hojas diferentes.
        """
        all_components: List[ComponentItem] = []
        merged_pin_node: Dict[str, str] = {}

        sheet_netlists = []
        for i, sheet in enumerate(self._sheets):
            sc = sheet['scene']
            pn = sc.extract_netlist()
            prefix = f"_s{i}_"
            prefixed_pn = {}
            for pin_id, net_name in pn.items():
                new_net = (net_name if net_name == '0' or not net_name.startswith('net_')
                           else prefix + net_name)
                prefixed_pn[pin_id] = new_net
            sheet_netlists.append(prefixed_pn)
            all_components.extend(sc.components)
            merged_pin_node.update(prefixed_pn)

        # ── Unificar nets ENTRE hojas por sheet_label ─────────────────────
        # Dentro de cada hoja, extract_netlist ya unió los net labels.
        # Aquí unimos los nets canónicos de cada hoja que comparten label.
        label_canonical: Dict[str, str] = {}  # label → net canónico global

        for i, sheet in enumerate(self._sheets):
            pn = sheet_netlists[i]
            for comp in sheet['scene'].components:
                if comp.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT') and comp.sheet_label:
                    pin_key = f"{comp.name}__p1"
                    if pin_key not in pn:
                        continue
                    net = pn[pin_key]   # net ya prefijado con _sN_
                    aliases = []
                    for lbl in (comp.sheet_label.strip(), comp.name.strip()):
                        if lbl and lbl not in aliases:
                            aliases.append(lbl)
                    for lbl in aliases:
                        if lbl not in label_canonical:
                            label_canonical[lbl] = net
                        elif label_canonical[lbl] != net:
                            # Reemplazar en toda la netlist
                            target = label_canonical[lbl]
                            for k in merged_pin_node:
                                if merged_pin_node[k] == net:
                                    merged_pin_node[k] = target
                            net = target

        return all_components, merged_pin_node

    def _get_sim_context(self):
        """Retorna (all_comps, pin_node) considerando net labels multi-hoja."""
        has_net_labels = any(
            comp.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT')
            for sheet in self._sheets
            for comp in sheet['scene'].components
        )
        if has_net_labels or len(self._sheets) > 1:
            return self._merge_all_sheets()
        return list(self.scene.components), self.scene.extract_netlist()

    def _toggle_simulation(self, checked: bool):
        """Analiza el circuito y despacha automáticamente al solver correcto."""
        if not checked:
            self._stop_simulation()
            return

        all_comps, pin_node = self._get_sim_context()
        self._sim_all_comps = all_comps
        self._sim_pin_node = pin_node

        std_name = DEFAULT_LOGIC_STANDARD
        analyzer = CircuitAnalyzer(logic_standard=std_name)
        flags = analyzer.analyze(all_comps, pin_node)

        self.results_text.setPlainText(flags.summary() + "\n\nAnalizando...")
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        # ── Despacho por modo ────────────────────────────────────────────
        # 1. Mixto (puentes ADC/DAC explícitos o fronteras implícitas):
        #    one-shot mixto. La co-simulación digital-analógica no se
        #    reanuda en vivo por ahora.
        # 2. Cualquier `has_ac` (con o sin DC, lineal o no-lineal):
        #    live transient continuo (estilo Multisim interactivo).
        # 3. Solo DC o digital: tick DC continuo (más rápido — el sistema
        #    converge al instante en cada paso, no necesita transient).
        if flags.has_bridges or bool(flags.implicit_boundary_nodes):
            self.run_btn.setChecked(False)
            self.run_btn.setText("▶  SIMULAR")
            self._run_simulation_auto(flags, pin_node)
        elif flags.has_ac:
            self._start_live_transient(flags, pin_node)
        elif flags.has_dc or flags.has_digital:
            self._sim_running = True
            self._sim_mode    = 'dc_tick'
            self.run_btn.setText("■  DETENER")
            self._sim_timer.setInterval(self._DC_TICK_MS)
            self._sim_timer.start()
            self._run_simulation_dc()
        else:
            self.run_btn.setChecked(False)
            self.run_btn.setText("▶  SIMULAR")
            self.results_text.setPlainText(
                "⚠  No se encontraron componentes para simular.\n"
                "Añade componentes al canvas y conéctalos a tierra.")

    def _stop_simulation(self):
        """Detiene la simulación y apaga todos los LEDs en todas las hojas."""
        self._sim_running = False
        self._sim_mode    = 'idle'
        self._sim_timer.stop()
        self._sim_timer.setInterval(self._DC_TICK_MS)   # restaurar intervalo DC
        self._sim_all_comps = None
        self._sim_pin_node  = None
        # Limpiar estado live
        self._live_state          = None
        self._live_components     = None
        self._live_pin_node       = None
        self._live_tick_count     = 0
        self._live_phasor_summary = ""
        self.run_btn.setChecked(False)
        self.run_btn.setText("▶  SIMULAR")
        for sheet in self._sheets:
            for item in sheet['scene'].components:
                if item.comp_type == 'LED':
                    item.led_on = False
                    item.update()
                elif item.comp_type == 'MULTIMETER':
                    item.meter_reading = None
                    item.update()
        self._refresh_open_multimeter_panels()

    def _tick_simulation(self):
        """Llamado por QTimer: dispatcher por modo."""
        if not self._sim_running:
            return
        if self._sim_mode == 'live_transient':
            self._tick_live_transient()
        else:
            # Modo DC tick: re-corre DC silenciosamente para refrescar LEDs
            all_comps, pin_node = self._get_sim_context()
            self._sim_all_comps = all_comps
            self._sim_pin_node  = pin_node
            self._run_simulation_dc(silent=True)

    def _run_simulation(self):
        """Compatibilidad: despacha al toggle."""
        self._toggle_simulation(True)

    # ── Live transient (Multisim-like) ────────────────────────────────────
    def _start_live_transient(self, flags, pin_node):
        """
        Arranca una simulación transient continua: el solver avanza el
        tiempo simulado en cada tick del QTimer, manteniendo el estado
        de capacitores, inductores y diodos entre llamadas.

        Equivalente al modo "interactivo" de Multisim.
        """
        sim_components = self._sim_all_comps or list(self.scene.components)

        # ── Construir componentes del motor analógico ─────────────────
        analog_comps, build_errors = [], []
        for item in sim_components:
            if item.comp_type in ComponentItem.DIGITAL_TYPES:
                continue
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue
            try:
                analog_comps.extend(build_engine_components_for_item(item, pin_node))
            except Exception as e:
                build_errors.append(f"{item.name}: {e}")

        if not analog_comps:
            self.results_text.setPlainText(
                "⚠  No hay componentes analógicos para simular.")
            self._stop_simulation()
            return

        # Frecuencia más alta entre todas las fuentes periódicas (VAC y FGEN).
        # Rige el dt interno y, junto con _LIVE_MAX_STEPS_PER_TICK, el
        # tiempo simulado por tick.
        freq = self._max_ac_source_frequency(sim_components) or 60.0

        # ── Snapshot fasorial inicial (solo circuitos lineales) ───────
        # El triángulo de potencia y los fasores P/Q/S solo tienen sentido
        # si el circuito NO tiene componentes que generen armónicos.
        # Para no-lineales (diodos, BJT, MOSFET) ocultamos el botón.
        ac_snapshot = None
        self._live_phasor_summary = ""   # se prepended en cada refresh del panel
        if not flags.has_nonlinear:
            ac_snapshot = self.solver.solve_ac_single(analog_comps, freq)
            if ac_snapshot.get('success'):
                self._last_ac_result = ac_snapshot
                self.btn_power_triangle.setVisible(True)
                self._live_phasor_summary = self._format_phasor_summary(
                    ac_snapshot, freq)
            else:
                self.btn_power_triangle.setVisible(False)
        else:
            self.btn_power_triangle.setVisible(False)

        # Estado live: None → el primer tick calcula DC OP en t=0 y arranca
        self._live_state       = None
        self._live_components  = analog_comps
        self._live_pin_node    = pin_node
        self._live_freq        = freq if freq > 0 else 60.0
        self._live_tick_count  = 0
        self._sim_mode         = 'live_transient'
        self._sim_running      = True
        # NOTA sobre adaptive: la trapezoidal es A-estable pero NO L-estable.
        # Para circuitos muy stiff (op-amps con A=1e5) el adaptativo es
        # imprescindible para que la trapezoidal no oscile. No lo desactivamos.
        #
        # Solo el tope de iteraciones NR varía con el circuito: los diodos
        # exponenciales pueden requerir más pasos para converger que un
        # circuito puramente lineal.
        self._live_nr_max = 40 if flags.has_nonlinear else 20

        self.run_btn.setText("■  DETENER")
        self.run_btn.setChecked(True)
        self._sim_timer.setInterval(self._LIVE_TICK_MS)
        self._sim_timer.start()

        msg = [
            f"═══ SIMULACIÓN LIVE (transient continuo, ×{self._LIVE_TIME_SCALE:g}) ═══",
            f"  {flags.summary()}",
            f"  f_AC = {self._live_freq:g} Hz  ·  paso real {self._LIVE_TICK_MS} ms"
            f"  ·  paso simulado {self._LIVE_TICK_MS * self._LIVE_TIME_SCALE:.2f} ms",
            "",
        ]

        # Si el fasorial fue válido, mostrar el resumen P/Q/S/fp arriba —
        # es información estable que no necesita refrescarse cada tick.
        if self._live_phasor_summary:
            msg.append(self._live_phasor_summary)

        msg.append("  Iniciando…")
        if build_errors:
            msg.append("")
            msg.append("── Advertencias ──")
            msg.extend([f"  ⚠ {e}" for e in build_errors])
        self.results_text.setPlainText("\n".join(msg))

    def _tick_live_transient(self):
        """Avanza el solver `dt_sim` segundos y actualiza la UI.

        Estrategia para mantener la UI fluida en cualquier frecuencia:
          1. `dt_internal` se elige para tener ~50 muestras por período
             de la onda más rápida (resolución suficiente para osciloscopio).
          2. `dt_advance` arranca como `tick_ms · TIME_SCALE` (slow-motion
             a baja frecuencia, igual que antes).
          3. Si esa combinación produce más de MAX_STEPS pasos del solver
             por tick, se recorta `dt_advance` para respetar el tope.
             Esto se traduce en "menos tiempo simulado por frame" cuando
             la frecuencia sube — la onda se ve "rápida" pero fluida.
        """
        if self._live_components is None:
            return

        T_freq = 1.0 / max(self._live_freq, 1e-6)
        # dt_internal = T / SAMPLES_PER_PERIOD para evitar aliasing.
        # Antes había un piso de 50 ns "para no exigir absurdos", pero eso
        # provocaba aliasing salvaje para señales >1.67 MHz (T/12=50ns).
        # El piso real lo da MAX_STEPS_PER_TICK: cuanto más alta la freq,
        # menos tiempo simulado por tick — el CPU/tick queda acotado igual.
        dt_internal = max(T_freq / self._LIVE_SAMPLES_PER_PERIOD, 1e-11)

        # Tiempo simulado deseado por tick (slow-motion a baja frecuencia)
        dt_advance_ideal = (self._LIVE_TICK_MS / 1000.0) * self._LIVE_TIME_SCALE

        # Tope por costo de CPU: nunca más de MAX_STEPS pasos del solver
        dt_advance_cap = self._LIVE_MAX_STEPS_PER_TICK * dt_internal
        dt_advance = min(dt_advance_ideal, dt_advance_cap)
        # Al menos 4 pasos por tick (mantiene la integración estable)
        dt_advance = max(dt_advance, dt_internal * 4)

        t_start = float(self._live_state['t']) if self._live_state else 0.0

        tr = self.solver.solve_transient(
            self._live_components,
            t_stop        = dt_advance,
            dt            = dt_internal,
            method        = 'trapezoidal',
            adaptive      = True,
            tol_abs       = self._LIVE_TOL_ABS,
            tol_rel       = self._LIVE_TOL_REL,
            # dt_min bajo: durante la conmutación de un diodo el NR puede
            # necesitar pasos de nanosegundos para converger.
            dt_min        = 1e-10,
            t_start       = t_start,
            initial_state = self._live_state,
            nr_tol        = self._LIVE_NR_TOL,
            nr_max_iter   = self._live_nr_max,
        )

        if not tr.get('success'):
            self.results_text.setPlainText(
                f"✗ Live transient falló:\n  {tr.get('error', 'desconocido')}")
            self._stop_simulation()
            return

        # Guardar estado para el próximo tick
        self._live_state = tr['final_state']
        self._live_tick_count += 1

        # Refresco visual de los items (LEDs y voltajes instantáneos)
        self._update_items_from_live(tr)

        # Texto del panel cada N ticks (no abrumar la UI)
        if self._live_tick_count % self._LIVE_PANEL_REFRESH_TICKS == 0:
            self._refresh_live_panel(tr)

        # Empujar muestras a los instrumentos abiertos (osciloscopios, etc.)
        self._push_to_open_instruments(tr)

    def _push_to_open_instruments(self, tr):
        """Notifica a cada panel de instrumento abierto que llegó un nuevo
        bloque de muestras del solver. Sólo los OSC consumen este flujo
        por ahora."""
        pin_node = self._live_pin_node or {}
        # Si hay varias hojas, recorremos sólo la escena activa para evitar
        # alimentar OSC de otras hojas con voltajes que no corresponden.
        for item in self.scene.components:
            if item.comp_type != 'OSC':
                continue
            dlg = getattr(item, '_panel_dialog', None)
            if dlg is None or not dlg.isVisible():
                continue
            try:
                dlg.push_samples(tr, pin_node)
            except Exception:
                # Un fallo en un instrumento no debe matar el live transient.
                pass

    @staticmethod
    def _estimate_led_current(vd, color: str = 'red'):
        """Corriente directa estimada con los mismos presets del LED MNA."""
        led_params = {
            'red':    (1.0e-18, 2.0, 5.0),
            'orange': (1.0e-19, 2.1, 5.0),
            'yellow': (1.0e-20, 2.2, 5.0),
            'green':  (1.0e-23, 2.5, 5.0),
            'blue':   (1.0e-27, 3.0, 5.0),
            'white':  (1.0e-27, 3.0, 5.0),
        }
        Is, n, vd_max = led_params.get(color, led_params['red'])
        vd_arr = np.asarray(vd, dtype=float)
        vd_arr = np.clip(vd_arr, -50.0, vd_max)
        current = Is * (np.exp(vd_arr / (n * 0.02585)) - 1.0)
        return np.maximum(current, 0.0)

    def _update_items_from_live(self, tr):
        """Actualiza cada componente con el último valor instantáneo.

        Para LEDs en señales AC, decidir el on/off según `arr[-1]` no sirve
        (cae en una fase aleatoria), y usar el pico es demasiado sensible
        a transitorios numéricos. Usamos corriente media estimada con el
        mismo modelo del LED; si supera ~0.1 mA, lo dibujamos encendido.
        """
        v_dict   = tr.get('voltages', {})
        if not v_dict:
            return
        sim_components = self._sim_all_comps or list(self.scene.components)
        pin_node = self._live_pin_node or {}

        def _last(node):
            arr = v_dict.get(node)
            if arr is None or len(arr) == 0:
                return 0.0
            return float(arr[-1])

        def _vd_array(n_a, n_k):
            """Array de Vd = V_a - V_k sobre las muestras del bloque."""
            arr_a = v_dict.get(n_a)
            if arr_a is None or len(arr_a) == 0:
                return None
            arr_a = np.asarray(arr_a)
            if n_k in ('0', 'gnd', 'GND', ''):
                return arr_a
            arr_k = v_dict.get(n_k)
            if arr_k is None:
                return arr_a
            n = min(len(arr_a), len(arr_k))
            return arr_a[-n:] - np.asarray(arr_k[-n:])

        for item in sim_components:
            if item.comp_type in ComponentItem.DIGITAL_TYPES:
                continue
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue

            n1 = item.node1.strip() or pin_node.get(f"{item.name}__p1", "")
            n2 = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")

            v_a = _last(n1) if n1 else None
            v_k = _last(n2) if n2 not in ('0', 'gnd', 'GND', '') else 0.0

            if v_a is not None:
                item.result_voltage = v_a

            if item.comp_type == 'LED' and n1:
                vd = _vd_array(n1, n2)
                if vd is None or len(vd) == 0:
                    item.led_on = False
                else:
                    # Corriente media visible (~0.1 mA como umbral visual).
                    i_led = self._estimate_led_current(
                        vd, getattr(item, 'led_color', 'red'))
                    item.led_on = float(np.mean(i_led)) > 1e-4

            if item.comp_type == 'MULTIMETER' and n1:
                vd = _vd_array(n1, n2)
                if vd is not None and len(vd) > 0:
                    self._update_multimeter_from_array(item, vd)

            item.update()

        self._refresh_open_multimeter_panels()

    # ── Multímetro: actualización de lecturas ─────────────────────────────
    def _multimeter_internal_R(self, item) -> float:
        return 1e-3 if getattr(item, 'meter_quantity', 'V') == 'A' else 1e7

    def _apply_meter_reading(self, item, val: float):
        """Aplica `val` (V o A) al item según su modo, ajustando la unidad
        del display. Para A modo, `val` ya debe ser corriente (no voltaje)."""
        qty = getattr(item, 'meter_quantity', 'V')
        if qty == 'V':
            item.meter_reading = float(val)
            item.meter_reading_unit_hint = 'V'
        elif qty == 'A':
            item.meter_reading = float(val)
            item.meter_reading_unit_hint = 'A'
        else:
            item.meter_reading = None
            item.meter_reading_unit_hint = 'Ω'

    def _update_multimeter_from_array(self, item, vd_arr):
        """Calcula la lectura a partir de una ventana de muestras V_p+ − V_p−.
        DC coupling → media · AC coupling → RMS de la componente alterna."""
        import numpy as _np
        arr = _np.asarray(vd_arr, dtype=float)
        cpl = getattr(item, 'meter_coupling', 'DC')
        if cpl == 'DC':
            val = float(_np.mean(arr))
        else:
            mean = float(_np.mean(arr))
            ac_part = arr - mean
            val = float(_np.sqrt(_np.mean(ac_part * ac_part)))
        qty = getattr(item, 'meter_quantity', 'V')
        if qty == 'A':
            val = val / self._multimeter_internal_R(item)
        self._apply_meter_reading(item, val)

    def _update_multimeter_from_dc(self, item, n1, n2, dc_voltages):
        """Lectura desde un resultado DC. En AC coupling, sólo DC → 0."""
        v1 = float(dc_voltages.get(n1, 0.0)) if n1 not in ('0', 'gnd', 'GND') else 0.0
        v2 = float(dc_voltages.get(n2, 0.0)) if n2 not in ('0', 'gnd', 'GND') else 0.0
        dv = v1 - v2
        cpl = getattr(item, 'meter_coupling', 'DC')
        if cpl == 'AC':
            val = 0.0
        else:
            val = dv
        qty = getattr(item, 'meter_quantity', 'V')
        if qty == 'A':
            val = val / self._multimeter_internal_R(item)
        self._apply_meter_reading(item, val)

    def _update_multimeter_from_ac(self, item, n1, n2, ac_voltages):
        """Lectura desde un snapshot AC (fasores RMS). En DC coupling → 0."""
        V1 = ac_voltages.get(n1, 0.0 + 0.0j) if n1 not in ('0', 'gnd', 'GND') else 0.0 + 0.0j
        V2 = ac_voltages.get(n2, 0.0 + 0.0j) if n2 not in ('0', 'gnd', 'GND') else 0.0 + 0.0j
        cpl = getattr(item, 'meter_coupling', 'DC')
        if cpl == 'DC':
            val = 0.0
        else:
            val = abs(V1 - V2)
        qty = getattr(item, 'meter_quantity', 'V')
        if qty == 'A':
            val = val / self._multimeter_internal_R(item)
        self._apply_meter_reading(item, val)

    def _refresh_open_multimeter_panels(self):
        """Refresca cada panel abierto de multímetro para que muestre la lectura
        recién calculada. Llamado desde los flujos de simulación."""
        for sheet in self._sheets:
            for item in sheet['scene'].components:
                if item.comp_type != 'MULTIMETER':
                    continue
                dlg = getattr(item, '_panel_dialog', None)
                if dlg is None:
                    continue
                try:
                    if dlg.isVisible():
                        dlg._refresh_display()
                except Exception:
                    pass

    def _refresh_live_panel(self, tr):
        """Actualiza el panel de texto con voltajes instantáneos y tiempo."""
        v_dict = tr.get('voltages', {})
        t_arr  = tr.get('time', [])
        if not v_dict or len(t_arr) == 0:
            return

        t_now = float(t_arr[-1])
        out = [
            f"═══ SIMULACIÓN LIVE (×{self._LIVE_TIME_SCALE:g}) ═══",
            f"  t_simulado = {t_now*1000:.2f} ms"
            f"   ·   ticks = {self._live_tick_count}"
            f"   ·   pasos_NR = {tr.get('steps', 0)}",
            "",
        ]

        # Fasores se mantienen visibles durante toda la corrida (snapshot fijo).
        if getattr(self, '_live_phasor_summary', ''):
            out.append(self._live_phasor_summary)

        out.append("── Voltajes instantáneos ──")
        for node, v_arr in sorted(v_dict.items()):
            if len(v_arr) > 0:
                out.append(f"  V({node}) = {float(v_arr[-1]):+.4f} V")

        self.results_text.setPlainText("\n".join(out))

    def _format_phasor_summary(self, ac_result: dict, freq: float) -> str:
        """Formatea fasores nodales y triángulo de potencia en un bloque de
        texto que se preserva mientras dura la simulación live."""
        import cmath as _cmath
        lines = [f"── Fasores AC ({freq:g} Hz, snapshot) ──"]
        for node, V in sorted(ac_result.get('voltages', {}).items()):
            lines.append(f"  V({node}) = {abs(V):.4f} V  "
                         f"∠{_cmath.phase(V)*180/_cmath.pi:.2f}°")
        tot = ac_result.get('total', {})
        if tot:
            lines += [
                "",
                "── Potencia total ──",
                f"  P={tot.get('P',0):+.4f} W  Q={tot.get('Q',0):+.4f} VAR",
                f"  S={tot.get('S',0):.4f} VA  fp={tot.get('fp',0):.4f} "
                f"({tot.get('fp_type','')})",
            ]
        lines.append("")
        return "\n".join(lines)

    def _run_simulation_auto(self, flags=None, pin_node=None):
        """Corre DC + AC + mixto según flags y muestra todo en un panel."""
        from PyQt6.QtWidgets import QApplication
        from engine.digital_engine import (
            DigitalSimulator, Gate, DFF, JKFF, TFF, SRFF, BinaryCounter, MUX,
        )
        from engine.bridges import ADC, DAC, ComparatorBridge, PWMBridge
        from engine.mixed_signal import MixedSignalInterface
        import cmath as _cmath

        if pin_node is None:
            pin_node = getattr(self, '_sim_pin_node', None) or self.scene.extract_netlist()
        if flags is None:
            sim_comps_for_flags = getattr(self, '_sim_all_comps', None) or list(self.scene.components)
            std_name = DEFAULT_LOGIC_STANDARD
            analyzer = CircuitAnalyzer(logic_standard=std_name)
            flags = analyzer.analyze(sim_comps_for_flags, pin_node)

        sim_components = getattr(self, '_sim_all_comps', None) or list(self.scene.components)

        std_name = DEFAULT_LOGIC_STANDARD

        # Detectar y mostrar el modo seleccionado automáticamente
        _modes = []
        if flags.has_dc:      _modes.append("DC")
        if flags.has_ac:
            _modes.append("AC-transient" if flags.has_nonlinear else "AC-fasorial")
        if flags.has_digital: _modes.append("Digital")
        if flags.has_bridges or flags.implicit_boundary_nodes:
            _modes.append("Mixto")
        _mode_str = " + ".join(_modes) if _modes else "—"

        out = [f"═══ SIMULACIÓN ({_mode_str}) ═══",
               f"  {flags.summary()}", ""]
        if flags.warnings:
            out.extend([f"  ⚠ {w}" for w in flags.warnings]); out.append("")

        # ── Construir componentes analógicos ─────────────────────────────
        analog_comps, build_errors = [], []
        for item in sim_components:
            if item.comp_type in ComponentItem.DIGITAL_TYPES:
                continue
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue
            try:
                analog_comps.extend(build_engine_components_for_item(item, pin_node))
            except Exception as e:
                build_errors.append(f"{item.name}: {e}")

        # ── DC ────────────────────────────────────────────────────────────
        if flags.has_dc and analog_comps:
            dc_comps = [VoltageSource(c.name, c.n_pos, c.n_neg, 0.0)
                        if isinstance(c, VoltageSourceAC) else c for c in analog_comps]
            dc = self.solver.solve_dc(dc_comps)
            out.append("── Voltajes DC ──")
            if dc["success"]:
                for node, v in sorted(dc["voltages"].items()):
                    out.append(f"  V({node}) = {v:+.4f} V")
                if dc.get("branch_currents"):
                    out.append(""); out.append("── Corrientes DC ──")
                    for name, i in dc["branch_currents"].items():
                        out.append(f"  I({name}) = {i*1000:+.4f} mA")
                for item in sim_components:
                    n1 = item.node1.strip() or pin_node.get(f"{item.name}__p1", "")
                    n2 = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")
                    item.result_voltage = dc["voltages"].get(n1)
                    if item.comp_type == "LED":
                        item.led_on = False
                        op = dc.get("operating_points", {}).get(item.name, {})
                        Id_op = op.get("Id", op.get("id")) if op else None
                        if Id_op is not None:
                            item.led_on = float(Id_op) > 1e-4
                        else:
                            v_a = dc["voltages"].get(n1, 0)
                            v_k = dc["voltages"].get(n2, 0)
                            vf_min = {'red':1.5,'orange':1.7,'yellow':1.8,
                                      'green':1.9,'blue':2.6,'white':2.6}
                            thr = vf_min.get(getattr(item,'led_color','red'), 1.5)
                            item.led_on = (v_a - v_k) > thr
                    item.update()
            else:
                out.append(f"  ✗ {dc['error']}")
            out.append("")

        # ── AC ────────────────────────────────────────────────────────────
        if flags.has_ac and analog_comps:
            freq = next((it.frequency for it in sim_components
                         if it.comp_type in ("VAC", "FGEN")), 60.0)

            if flags.has_nonlinear:
                # Diodos/BJT/MOSFET con AC → análisis fasorial NO es válido
                # (el componente no-lineal genera armónicos). Corremos
                # transient durante varios ciclos y reportamos forma de onda
                # estabilizada (último ciclo).
                import numpy as _np
                T        = 1.0 / freq if freq > 0 else 1e-3
                n_cycles = 3
                tr = self.solver.solve_transient(
                    analog_comps,
                    t_stop      = n_cycles * T,
                    dt          = T / 200.0,
                    method      = 'trapezoidal',
                    adaptive    = True,
                    nr_tol      = 1e-5,
                    nr_max_iter = 30,
                )
                out.append(
                    f"── Transitorio (no-lineal + AC, f={freq:g} Hz, "
                    f"{n_cycles} ciclos) ──")
                if tr["success"]:
                    t_arr      = tr["time"]
                    v_dict     = tr["voltages"]
                    last_cycle = t_arr >= (n_cycles - 1) * T
                    for node, v_arr in sorted(v_dict.items()):
                        v_last = v_arr[last_cycle]
                        if len(v_last) == 0:
                            continue
                        vmax  = float(v_last.max())
                        vmin  = float(v_last.min())
                        vmean = float(v_last.mean())
                        vrms  = float(_np.sqrt(_np.mean(v_last ** 2)))
                        out.append(
                            f"  V({node}): pk=[{vmin:+.3f}, {vmax:+.3f}] V  "
                            f"DC={vmean:+.3f} V  RMS={vrms:.3f} V")
                    out.append(
                        f"  pasos={tr['steps']}  "
                        f"dt_avg={tr['dt_stats']['mean']*1e6:.1f} µs")

                    # LEDs según corriente promedio en último ciclo (si la
                    # corriente promedio del LED supera ~0.1 mA → encendido)
                    self._update_leds_from_transient(
                        sim_components, tr, last_cycle, pin_node)

                    self._last_transient_result = tr
                    self.btn_power_triangle.setVisible(False)
                else:
                    out.append(f"  ✗ {tr['error']}")
                out.append("")
            else:
                # Circuito lineal: análisis fasorial estándar (rápido y exacto)
                ac = self.solver.solve_ac_single(analog_comps, freq)
                out.append(f"── Fasores AC ({freq} Hz) ──")
                if ac["success"]:
                    for node, V in sorted(ac["voltages"].items()):
                        out.append(f"  V({node}) = {abs(V):.4f} V  ∠{_cmath.phase(V)*180/_cmath.pi:.2f}°")
                    t = ac.get("total", {})
                    if t:
                        out += ["", "── Potencia total ──",
                                f"  P={t.get('P',0):+.4f} W  Q={t.get('Q',0):+.4f} VAR",
                                f"  S={t.get('S',0):.4f} VA  fp={t.get('fp',0):.4f} ({t.get('fp_type','')})"]
                    self._last_ac_result = ac
                    self.btn_power_triangle.setVisible(True)
                else:
                    out.append(f"  ✗ {ac['error']}")
                out.append("")

        # ── Mixto ─────────────────────────────────────────────────────────
        if flags.needs_mixed:
            # Si no hay puentes reales ni nodos frontera, no hace falta
            # co-simulación transitoria: correr DC analógico + digital por separado
            _only_isolated = (
                not flags.has_bridges
                and not flags.implicit_boundary_nodes
            )
            if _only_isolated:
                # Evaluar puertas digitales usando los voltajes DC ya calculados
                # para determinar los niveles lógicos en las entradas
                _dc_voltages = {}
                if flags.has_dc and analog_comps:
                    _dc_res = self.solver.solve_dc(
                        [VoltageSource(c.name, c.n_pos, c.n_neg, 0.0)
                         if isinstance(c, VoltageSourceAC) else c
                         for c in analog_comps])
                    if _dc_res.get("success"):
                        _dc_voltages = _dc_res["voltages"]
                self._evaluate_digital_gates(pin_node, _dc_voltages, out=out)
                out.append("")
                self.results_text.setPlainText("\n".join(out))
                self.scene.update()
                return
            t_stop = 1e-3   # 1 ms por defecto
            dt_chunk = max(t_stop / 100, 1e-6)
            dsim = DigitalSimulator()
            adc_list, dac_list = [], []
            _gmap = {"AND":"AND","OR":"OR","NOT":"NOT","NAND":"NAND","NOR":"NOR","XOR":"XOR"}
            for item in sim_components:
                ct = item.comp_type; tpd = item.dig_tpd_ns * 1e-9
                try:
                    if ct in _gmap:
                        n_in = max(1, item.dig_inputs)
                        dsim.add(Gate(item.name, _gmap[ct],
                                      [f"{item.name}_I{i}" for i in range(n_in)],
                                      f"{item.name}_Y", t_pd=tpd,
                                      input_invert=list(
                                          getattr(item, 'dig_input_neg', []) or [])))
                    elif ct == "DFF":
                        dsim.add(DFF(item.name, d=f"{item.name}_D", clk=item.dig_clk,
                                     q=f"{item.name}_Q", qn=f"{item.name}_Qn", t_pd=tpd))
                    elif ct == "ADC_BRIDGE":
                        nd = item.dig_analog_node or pin_node.get(f"{item.name}__p1","")
                        adc_list.append(ADC(item.name, node=nd, bits=item.dig_bits_adc, vref=item.dig_vref))
                    elif ct == "DAC_BRIDGE":
                        nd = item.dig_analog_node or pin_node.get(f"{item.name}__p1","")
                        dac_list.append(DAC(item.name, bits=item.dig_bits_adc, vref=item.dig_vref, out_node=nd))
                    elif ct == "COMPARATOR":
                        nd = item.dig_analog_node or pin_node.get(f"{item.name}__p1","")
                        adc_list.append(ComparatorBridge(item.name, node_pos=nd))
                except Exception as e:
                    build_errors.append(f"{item.name}: {e}")
            if flags.implicit_boundary_nodes:
                std = LOGIC_STANDARDS.get(std_name, DEFAULT_STANDARD)
                out.append(f"── Fronteras implícitas ({std_name}) ──")
                for node in flags.implicit_boundary_nodes:
                    out.append(f"  Nodo '{node}'")
                    adc_list.append(ADC(f"__impl_{node}", node=node, bits=1, vref=std.Vdd))
                out.append("")
            if analog_comps:
                iface = MixedSignalInterface(self.solver, dsim, analog_comps)
                for a in adc_list: iface.add_adc(a)
                for d in dac_list:
                    if hasattr(d,"pwm_net"): iface.add_pwm(d)
                    elif hasattr(d,"input_nets"): iface.add_dac(d)
                    else: iface.add_comparator(d)
                mr = iface.run_iterative(t_stop=t_stop, dt_chunk=dt_chunk,
                                          dt_analog=min(dt_chunk/10, 1e-6))
                out.append("── Co-simulación mixta ──")
                if mr.success:
                    for nd, arr in sorted(mr.analog_voltages.items()):
                        if len(arr) > 0: out.append(f"  V({nd}) = {arr[-1]:+.4f} V")
                    for net, hist in sorted(mr.digital_waveforms.items()):
                        if hist and not net.startswith("__impl"):
                            out.append(f"  {net} = {hist[-1][1]}")
                else:
                    out.append(f"  ✗ {mr.error}")
                out.append("")

        if build_errors:
            out.append("── Advertencias ──")
            out.extend([f"  ⚠ {e}" for e in build_errors])
        self.results_text.setPlainText("\n".join(out))
        self.scene.update()

    def _update_leds_from_transient(self, sim_components, tr, last_cycle_mask,
                                    pin_node):
        """Enciende LEDs cuya corriente media en el último ciclo supera ~0.1 mA."""
        v_dict = tr.get("voltages", {})
        if not v_dict:
            return
        for item in sim_components:
            if item.comp_type != "LED":
                continue
            n_a = item.node1.strip() or pin_node.get(f"{item.name}__p1", "")
            n_k = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")
            v_a_arr = v_dict.get(n_a)
            v_k_arr = v_dict.get(n_k) if n_k not in ('0','gnd','GND') else None
            if v_a_arr is None:
                continue
            va = v_a_arr[last_cycle_mask] if hasattr(v_a_arr, '__getitem__') else v_a_arr
            vk = (v_k_arr[last_cycle_mask] if v_k_arr is not None
                  else 0.0)
            try:
                vd = va - vk
                i_led = self._estimate_led_current(
                    vd, getattr(item, 'led_color', 'red'))
                item.led_on = float(np.mean(i_led)) > 1e-4
            except Exception:
                item.led_on = False
            item.update()

    def _run_simulation_dc(self, silent: bool = False):
        components = []
        errors = []

        # Usar contexto multi-hoja si disponible (net labels / múltiples hojas)
        sim_comps = getattr(self, '_sim_all_comps', None) or list(self.scene.components)
        pin_node = getattr(self, '_sim_pin_node', None) or self.scene.extract_netlist()

        for item in sim_comps:
            # Net labels y nodos auxiliares no generan componentes de engine
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue

            # Prioridad: nodo manual del usuario > nodo extraido automaticamente
            auto_n1 = pin_node.get(f"{item.name}__p1", f'iso_{item.name}_p')
            auto_n2 = pin_node.get(f"{item.name}__p2", '0')
            auto_n3 = pin_node.get(f"{item.name}__p3", '')

            n1 = item.node1.strip() if item.node1.strip() else auto_n1
            n2 = item.node2.strip() if item.node2.strip() else auto_n2
            n3 = item.node3.strip() if item.node3.strip() else auto_n3

            try:
                # ── Componentes digitales: se ignoran en DC/AC ──────────
                if item.comp_type in ComponentItem.DIGITAL_TYPES:
                    # LOGIC_STATE / CLK: modelar como fuente de voltaje ideal
                    if item.comp_type in ('LOGIC_STATE', 'CLK'):
                        std_name = DEFAULT_LOGIC_STANDARD
                        std = LOGIC_STANDARDS.get(std_name, DEFAULT_STANDARD)
                        v_out = std.Voh if item.value else std.Vol
                        out_node = item.node1.strip() or pin_node.get(f"{item.name}__p1", f"ls_{item.name}")
                        if out_node and out_node not in ('0', 'gnd', 'GND'):
                            components.append(VoltageSource(item.name, out_node, '0', v_out))
                    continue

                # En DC la fuente VAC vale 0 V (valor medio de senoidal)
                if item.comp_type == 'VAC':
                    components.append(VoltageSource(item.name, n1, n2, 0.0))
                    continue

                # Validación específica de R
                if item.comp_type == 'R' and item.value <= 0:
                    errors.append(f"{item.name}: resistencia debe ser > 0")
                    continue

                # Resto de componentes analógicos (incluye POT, XFMR, BRIDGE)
                components.extend(build_engine_components_for_item(item, pin_node))
            except Exception as e:
                errors.append(f"{item.name}: {e}")

        # ── Excluir LEDs/Diodos cuyo ánodo es salida exclusiva de puerta digital ──
        # Esos componentes no tienen driver analógico → matriz singular.
        # Se evalúan luego con _evaluate_digital_gates.
        _gate_types_dc = {'AND','OR','NOT','NAND','NOR','XOR','NAND','NOR',
                          'DFF','JKFF','TFF','SRFF','MUX2','COUNTER'}
        _dig_out_nodes = set()
        for _item in sim_comps:
            if _item.comp_type in _gate_types_dc:
                _on = _item.node1.strip() or pin_node.get(f"{_item.name}__p1", "")
                if _on and _on not in ('0','gnd','GND'):
                    _dig_out_nodes.add(_on)
        # Reunir todos los nodos que tienen driver analógico activo
        # (fuentes de voltaje/corriente, BJT/MOSFET/OpAmp). Los pasivos
        # como R, L, C, Diode NO se cuentan como drivers — sólo aportan
        # caminos pasivos.
        _analog_driver_nodes = set()
        for _c in components:
            if _c.__class__.__name__ == 'Diode':
                continue
            for _attr in ('n_pos','n_neg','n_p','n_n','n_out','n_in',
                          'n_base','n_collector','n_emitter',
                          'n_gate','n_drain','n_source'):
                _nd = getattr(_c, _attr, None)
                if _nd and _nd not in ('0','gnd','GND'):
                    _analog_driver_nodes.add(_nd)
        # Quitar del netlist analógico los LED/Diodo cuyo ánodo está en
        # un nodo de salida sólo-digital. Diode usa n_a (ánodo).
        # Antes este filtro buscaba `n_p`/`n_pos` que Diode no tiene,
        # por lo que nunca excluía nada — funcionaba sólo porque los
        # nodos del diodo tampoco entraban al node_map. Tras corregir
        # _build_maps, los diodos sí se estampan, así que el filtro
        # tiene que usar el atributo correcto.
        components = [
            _c for _c in components
            if not (
                _c.__class__.__name__ == 'Diode'
                and getattr(_c, 'n_a', '') in _dig_out_nodes
                and getattr(_c, 'n_a', '') not in _analog_driver_nodes
            )
        ]

        if not components:
            # Solo puertas digitales y LEDs en sus salidas: evaluar directo.
            # FIX: construir dc_voltages desde los LOGIC_STATE antes de
            # llamar a _evaluate_digital_gates. Sin esto, el diccionario
            # llega vacío y todas las entradas se leen como 0 V (LOW).
            std_name = DEFAULT_LOGIC_STANDARD
            std = LOGIC_STANDARDS.get(std_name, DEFAULT_STANDARD)
            _dig_voltages = {}
            for _it in sim_comps:
                if _it.comp_type in ('LOGIC_STATE', 'CLK'):
                    _v = std.Voh if _it.value else std.Vol
                    _net = _it.node1.strip() or pin_node.get(f"{_it.name}__p1", "")
                    if _net:
                        _dig_voltages[_net] = _v
            out = ["═══ SIMULACIÓN DIGITAL ═══", ""]
            self._evaluate_digital_gates(pin_node, _dig_voltages, silent=silent, out=out, sim_comps=sim_comps)
            if not silent:
                self.results_text.setPlainText('\n'.join(out))
            for sheet in self._sheets:
                sheet['scene'].update()
            return

        # Mostrar netlist extraida antes de simular
        if not silent:
            out_pre = ["═══ NETLIST EXTRAIDA ═══"]
            for item in sim_comps:
                if item.comp_type in ('GND', 'NODE'):
                    continue
                auto_n1 = pin_node.get(f"{item.name}__p1", '?')
                auto_n2 = pin_node.get(f"{item.name}__p2", '?')
                n1_show = item.node1.strip() if item.node1.strip() else auto_n1
                n2_show = item.node2.strip() if item.node2.strip() else auto_n2
                out_pre.append(f"  {item.name}: {n1_show} → {n2_show}  ({item._format_value()})")
            out_pre.append("")
            self.results_text.setPlainText('\n'.join(out_pre) + "Simulando...")
            QApplication.processEvents()

        result = self.solver.solve_dc(components)

        # Mostrar resultados
        out = []
        if result['success']:
            out.append("═══ ANÁLISIS DC ═══\n")
            out.append("── Tensiones nodales ──")
            for node, v in sorted(result['voltages'].items()):
                out.append(f"  V({node}) = {v:+.4f} V")
            if result.get('branch_currents'):
                out.append("\n── Corrientes de rama ──")
                for name, i in result['branch_currents'].items():
                    out.append(f"  I({name}) = {i*1000:+.4f} mA")

            # Iteraciones Newton-Raphson (si aplica)
            if 'iterations' in result:
                out.append(f"\n  [NR convergió en {result['iterations']} iteraciones]")
            if 'warning' in result:
                out.append(f"\n  ⚠ {result['warning']}")

            # Puntos de operación de componentes no-lineales
            if result.get('operating_points'):
                out.append("\n── Puntos de operación ──")
                for comp_name, op in result['operating_points'].items():
                    out.append(f"  {comp_name}:")
                    for k, v in op.items():
                        if isinstance(v, float):
                            out.append(f"    {k} = {v:.4g}")
                        else:
                            out.append(f"    {k} = {v}")

            # Corrientes y potencias
            out.append("\n── Corrientes y potencias ──")
            for comp in components:
                if isinstance(comp, VoltageSource):
                    i_branch = result['branch_currents'].get(comp.name, 0)
                    p = comp.V * i_branch
                    out.append(f"  I({comp.name}) = {i_branch*1000:+.4f} mA  |  P = {abs(p):.4f} W")
                elif isinstance(comp, Resistor):
                    v1 = result['voltages'].get(comp.n1, 0)
                    v2 = result['voltages'].get(comp.n2, 0)
                    i_r = (v1 - v2) / comp.R
                    p   = (v1 - v2)**2 / comp.R
                    out.append(f"  I({comp.name}) = {i_r*1000:+.4f} mA  |  P = {p*1000:.4f} mW")
                elif isinstance(comp, CurrentSource):
                    v1 = result['voltages'].get(comp.n_pos, 0)
                    v2 = result['voltages'].get(comp.n_neg, 0)
                    p = comp.I_val * (v1 - v2)
                    out.append(f"  I({comp.name}) = {comp.I_val*1000:+.4f} mA  |  P = {abs(p):.4f} W")

            # Actualizar canvas con voltajes y estado LED
            for item in sim_comps:
                auto_n1 = pin_node.get(f"{item.name}__p1", '')
                auto_n2 = pin_node.get(f"{item.name}__p2", '0')
                n1 = item.node1.strip() if item.node1.strip() else auto_n1
                n2 = item.node2.strip() if item.node2.strip() else auto_n2
                if n1 in result['voltages']:
                    item.result_voltage = result['voltages'][n1]
                else:
                    item.result_voltage = None
                if item.comp_type == 'MULTIMETER':
                    self._update_multimeter_from_dc(
                        item, n1, n2, result['voltages'])
                if item.comp_type == 'LED':
                    led_on = False
                    op = result.get('operating_points', {}).get(item.name, {})
                    id_ = None
                    if op:
                        id_ = op.get('Id', op.get('id', op.get('I', None)))
                    if id_ is not None:
                        led_on = float(id_) > 1e-4
                    else:
                        v_a = result['voltages'].get(n1, None)
                        v_k = result['voltages'].get(n2, None)
                        if v_a is not None and v_k is not None:
                            vf_min = {'red':1.5,'orange':1.7,'yellow':1.8,
                                      'green':1.9,'blue':2.6,'white':2.6}
                            thr = vf_min.get(getattr(item,'led_color','red'), 1.5)
                            led_on = (v_a - v_k) > thr
                    item.led_on = led_on
                item.update()
                if hasattr(item, 'scene') and item.scene():
                    item.scene().update(item.mapToScene(item.boundingRect()).boundingRect())

            # Debug LED — mostrar info de nodos y voltajes del LED
            led_items = [it for it in sim_comps if it.comp_type == 'LED']
            if led_items:
                out.append("\n── Debug LED ──")
                for it in led_items:
                    auto_n1 = pin_node.get(f"{it.name}__p1", '?')
                    auto_n2 = pin_node.get(f"{it.name}__p2", '?')
                    n1d = it.node1.strip() if it.node1.strip() else auto_n1
                    n2d = it.node2.strip() if it.node2.strip() else auto_n2
                    va  = result['voltages'].get(n1d, 'N/A')
                    vk  = result['voltages'].get(n2d, 'N/A')
                    op  = result.get('operating_points', {}).get(it.name, {})
                    out.append(f"  {it.name}: ánodo={n1d}({va}) cátodo={n2d}({vk})")
                    out.append(f"    op={op}  led_on={it.led_on}")

        else:
            if not silent:
                out.append(f"\u2717 Error de simulaci\u00f3n:\n{result['error']}")
                out.append("\nVerifica que el circuito tenga:")
                out.append("  \u2022 Al menos una fuente de voltaje")
                out.append("  \u2022 Nodo de tierra (nodo '0')")
                out.append("  \u2022 Nodos asignados a cada componente")

        if errors and not silent:
            out.append("\n── Advertencias ──")
            out.extend([f"  ⚠ {e}" for e in errors])

        # Evaluar puertas digitales y actualizar LEDs en su salida
        if result.get('success'):
            self._evaluate_digital_gates(pin_node, result['voltages'], silent=silent, out=out, sim_comps=sim_comps)

        if not silent:
            self.results_text.setPlainText('\n'.join(out))
        for sheet in self._sheets:
            sheet['scene'].update()
        self._refresh_open_multimeter_panels()


    def _evaluate_digital_gates(self, pin_node, dc_voltages, silent=False, out=None, sim_comps=None):
        std_name = DEFAULT_LOGIC_STANDARD
        std = LOGIC_STANDARDS.get(std_name, DEFAULT_STANDARD)
        _gmap = {'AND':'AND','OR':'OR','NOT':'NOT','NAND':'NAND','NOR':'NOR','XOR':'XOR'}
        _funcs = {
            'AND':  lambda vals: all(vals),
            'OR':   lambda vals: any(vals),
            'NAND': lambda vals: not all(vals),
            'NOR':  lambda vals: not any(vals),
            'XOR':  lambda vals: bool(sum(vals) % 2),
            'NOT':  lambda vals: not bool(vals[0]),
        }
        _all = sim_comps if sim_comps is not None else list(self.scene.components)
        gate_items = [it for it in _all if it.comp_type in _gmap]
        if gate_items and out is not None and not silent:
            out.append('\n── Señales digitales ──')
        for item in gate_items:
            n_in = max(1, item.dig_inputs)
            input_logics = []
            neg_mask = list(getattr(item, 'dig_input_neg', []) or [])
            for i in range(n_in):
                if i == 0:
                    node = (item.node2.strip()
                            or pin_node.get(f'{item.name}__p2', ''))
                elif i == 1:
                    _n3 = item.node3.strip() if hasattr(item, 'node3') else ''
                    node = _n3 or pin_node.get(f'{item.name}__p3', '')
                else:
                    _extra = getattr(item, 'dig_input_nodes', [])
                    _manual_extra = _extra[i-2].strip() if len(_extra) > i-2 else ''
                    node = _manual_extra or pin_node.get(f'{item.name}__p{i+2}', '')
                if node in ('0', 'gnd', 'GND'):
                    v = 0.0
                elif not node:
                    v = 0.0
                else:
                    v = dc_voltages.get(node, 0.0)
                bit = 1 if v >= std.Vih else 0
                # Si la entrada está marcada como negada (bubble), se invierte
                # antes de evaluar la función de la compuerta.
                if i < len(neg_mask) and neg_mask[i]:
                    bit = 1 - bit
                input_logics.append(bit)
            y = int(_funcs[item.comp_type](input_logics))
            out_node = item.node1.strip() or pin_node.get(f'{item.name}__p1', '')
            v_out = std.Voh if y else std.Vol
            if out_node and out_node not in ('0', 'gnd', 'GND'):
                dc_voltages[out_node] = v_out
            for led in _all:
                if led.comp_type == 'LED':
                    led_anode = led.node1.strip() or pin_node.get(f'{led.name}__p1', '')
                    if led_anode == out_node:
                        led.led_on = (v_out > 0.3)
                        led.update()
            if out is not None and not silent:
                out.append(f"  {item.name}_Y = {y}  ({'HIGH' if y else 'LOW'})")

        # ── Evaluación de flip-flops (DFF/JKFF/TFF/SRFF) ─────────────────
        # Lectura de niveles desde dc_voltages, prioridad SET/RESET asíncronos,
        # y actualización del círculo de memoria (dig_q_state).
        ff_items = [it for it in _all
                    if it.comp_type in ComponentItem.FLIPFLOP_TYPES]
        if ff_items and out is not None and not silent:
            out.append('\n── Flip-flops ──')

        def _logic_at(node: str) -> int:
            if not node or node in ('0', 'gnd', 'GND'):
                return 0
            return 1 if dc_voltages.get(node, 0.0) >= std.Vih else 0

        for item in ff_items:
            # Resolver nodos de cada pin: manual > automático.
            n_q   = item.node1.strip() or pin_node.get(f'{item.name}__p1', '')
            n_in1 = item.node2.strip() or pin_node.get(f'{item.name}__p2', '')
            n_in2 = (item.node3.strip() if hasattr(item, 'node3') else '') \
                    or pin_node.get(f'{item.name}__p3', '')
            n_set = pin_node.get(f'{item.name}__p4', '')
            n_rst = pin_node.get(f'{item.name}__p5', '')

            # Persistir último valor de CLK por flip-flop para detectar flancos.
            last_clk = getattr(item, '_last_clk_seen', 0)
            q_prev   = int(getattr(item, 'dig_q_state', 0))
            q_new    = q_prev

            set_active = bool(_logic_at(n_set))
            rst_active = bool(_logic_at(n_rst))

            if rst_active:
                q_new = 0
            elif set_active:
                q_new = 1
            elif item.comp_type == 'SRFF':
                # Asíncrono: S=p2, R=p3
                S = _logic_at(n_in1)
                R = _logic_at(n_in2)
                if S and R:    q_new = 0    # estado prohibido → 0
                elif S:        q_new = 1
                elif R:        q_new = 0
            else:
                # Síncrono por flanco de subida.
                # DFF/TFF: CLK está en p3 (entrada secundaria)
                # JKFF: J=p2, K=p3, CLK = item.dig_clk (net global)
                if item.comp_type == 'JKFF':
                    clk_now = _logic_at(item.dig_clk)
                else:
                    clk_now = _logic_at(n_in2)
                if clk_now == 1 and last_clk == 0:
                    if item.comp_type == 'DFF':
                        q_new = _logic_at(n_in1)
                    elif item.comp_type == 'TFF':
                        if _logic_at(n_in1):
                            q_new = 1 - q_prev
                    elif item.comp_type == 'JKFF':
                        J = _logic_at(n_in1)
                        K = _logic_at(n_in2)
                        if   J == 0 and K == 0: pass
                        elif J == 0 and K == 1: q_new = 0
                        elif J == 1 and K == 0: q_new = 1
                        else:                   q_new = 1 - q_prev
                item._last_clk_seen = clk_now

            item.dig_q_state = q_new

            # Propagar Q y Q̄ a sus nodos para que la cadena digital los vea
            if n_q and n_q not in ('0', 'gnd', 'GND'):
                dc_voltages[n_q] = std.Voh if q_new else std.Vol
            n_qn = pin_node.get(f'{item.name}__p6', '')
            if n_qn and n_qn not in ('0', 'gnd', 'GND'):
                dc_voltages[n_qn] = std.Voh if (1 - q_new) else std.Vol

            # Refrescar LEDs cuyo ánodo cae sobre Q o Q̄ del flip-flop. Sin
            # esto, el LED quedaría apagado aunque la salida del FF esté en
            # alto, porque la actualización de led_on se hacía sólo en el
            # bucle de compuertas.
            for led in _all:
                if led.comp_type != 'LED':
                    continue
                led_anode = (led.node1.strip()
                             or pin_node.get(f'{led.name}__p1', ''))
                if not led_anode:
                    continue
                if led_anode == n_q:
                    led.led_on = bool(q_new)
                    led.update()
                elif led_anode == n_qn:
                    led.led_on = bool(1 - q_new)
                    led.update()

            # Repintar el componente para reflejar el círculo de memoria
            item.update()

            if out is not None and not silent:
                out.append(f"  {item.name}.Q = {q_new}")

    # ── Panel de propiedades ─────────────────────
    def _run_simulation_ac(self):
        """Análisis AC de frecuencia única con triángulo de potencia."""
        from PyQt6.QtWidgets import QInputDialog

        # Usar contexto multi-hoja si disponible
        sim_comps = getattr(self, '_sim_all_comps', None) or list(self.scene.components)
        pin_node = getattr(self, '_sim_pin_node', None) or self.scene.extract_netlist()

        # Buscar fuente AC en el canvas para leer la frecuencia
        ac_items = [it for it in sim_comps if it.comp_type in ('VAC', 'FGEN')]
        if not ac_items:
            self.results_text.setPlainText(
                "⚠  No hay fuentes AC en el circuito.\n"
                "Agrega una fuente VAC o FGEN para el análisis AC.")
            return

        # Usar la frecuencia de la primera fuente AC como referencia
        freq_default = ac_items[0].frequency
        freq, ok = QInputDialog.getDouble(
            self, 'Frecuencia de análisis',
            'Frecuencia (Hz):', freq_default, 0.001, 1e9, 3)
        if not ok:
            return

        components = []
        errors     = []

        for item in sim_comps:
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue
            auto_n1 = pin_node.get(f"{item.name}__p1", f'iso_{item.name}_p')
            auto_n2 = pin_node.get(f"{item.name}__p2", '0')
            n1 = item.node1.strip() if item.node1.strip() else auto_n1
            n2 = item.node2.strip() if item.node2.strip() else auto_n2

            try:
                if item.comp_type in ComponentItem.DIGITAL_TYPES:
                    continue
                # En AC pura, las fuentes DC contribuyen 0 V
                if item.comp_type == 'V':
                    components.append(VoltageSource(item.name, n1, n2, 0.0))
                    continue
                # Validación específica
                if item.comp_type == 'R' and item.value <= 0:
                    errors.append(f"{item.name}: R debe ser > 0")
                    continue
                # BRIDGE en AC: los diodos linealizados a Vd=0.6V actúan
                # como cortos (gd≈4.7e7 S) y vuelven la matriz singular
                # cuando el lado AC del puente está ligado al secundario
                # flotante de un transformador. Reemplazamos por 4 resistencias
                # de la misma topología que mantienen el sistema solvable
                # sin "shortear" entre sí los nodos AC1, AC2, DC+ y DC-.
                # La rectificación verdadera no se puede representar en
                # análisis fasorial; la salida DC real se reporta en
                # post-proceso (sección "Puentes rectificadores").
                if item.comp_type == 'BRIDGE':
                    auto_n3 = pin_node.get(f"{item.name}__p3", f'dcp_{item.name}')
                    auto_n4 = pin_node.get(f"{item.name}__p4", f'dcn_{item.name}')
                    n3_b = (item.node3.strip() if hasattr(item, 'node3') and item.node3.strip()
                            else auto_n3)
                    n4_b = (item.node4.strip() if hasattr(item, 'node4') and item.node4.strip()
                            else auto_n4)
                    R_BR = 1e4   # 10 kΩ — preserva topología sin cortocircuitar
                    components.append(Resistor(f'{item.name}_R1', n1,   n3_b, R_BR))
                    components.append(Resistor(f'{item.name}_R2', n2,   n3_b, R_BR))
                    components.append(Resistor(f'{item.name}_R3', n4_b, n1,   R_BR))
                    components.append(Resistor(f'{item.name}_R4', n4_b, n2,   R_BR))
                    continue
                # Transformador: tie a tierra de alta impedancia en la
                # primaria (lado −) para evitar matriz singular cuando el
                # usuario no la conecta explícitamente a GND. 1 MΩ apenas
                # carga al circuito (Iref ≈ V/1MΩ) pero define el modo
                # común y permite resolver el AC.
                if item.comp_type == 'XFMR':
                    components.extend(build_engine_components_for_item(item, pin_node))
                    components.append(Resistor(
                        f'{item.name}_GREF', n2, '0', 1e6))
                    continue
                # Resto: helper centralizado (POT, VAC, C, L, Z, I…)
                components.extend(build_engine_components_for_item(item, pin_node))
            except Exception as e:
                errors.append(f"{item.name}: {e}")

        if not components:
            self.results_text.setPlainText("⚠  No hay componentes simulables.")
            return

        solver = MNASolver()
        result = solver.solve_ac_single(components, freq)

        out = ["═══ ANÁLISIS AC ═══", f"  Frecuencia: {freq} Hz", ""]

        if errors:
            out.append("⚠ Advertencias:")
            out += [f"  {e}" for e in errors]
            out.append("")

        if not result['success']:
            out.append(f"✗ Error: {result['error']}")
            self.results_text.setPlainText('\n'.join(out))
            self.btn_power_triangle.setVisible(False)
            return

        # Lecturas de los multímetros con los fasores AC
        for item in sim_comps:
            if item.comp_type != 'MULTIMETER':
                continue
            n1 = item.node1.strip() or pin_node.get(f"{item.name}__p1", "")
            n2 = item.node2.strip() or pin_node.get(f"{item.name}__p2", "0")
            self._update_multimeter_from_ac(item, n1, n2, result['voltages'])
            item.update()
        self._refresh_open_multimeter_panels()

        # ── Voltajes nodales ──────────────────────────────────────────────
        out.append("── Voltajes nodales (Vrms / ∠°) ──")
        for node, V in sorted(result['voltages'].items()):
            import cmath
            mag   = abs(V)
            phase = cmath.phase(V) * 180 / cmath.pi
            out.append(f"  V({node}) = {mag:.4f} V  ∠{phase:.2f}°")

        # ── Rectificación: análisis híbrido AC + DC ───────────────────────
        # El fasor AC no representa rectificación (es no-lineal). Para que
        # el puente "funcione de verdad" tras el AC corremos una segunda
        # solución DC en la que:
        #   • cada VAC contribuye 0 V (componente DC de la senoide)
        #   • cada BRIDGE se reemplaza por una VoltageSource(DC+, DC−)
        #     cuyo valor es V_pk − 2·Vf, calculado a partir del fasor que
        #     vio el puente en el AC. Así LEDs/resistencias/cargas en el
        #     lado DC ven tensión DC real y el circuito “rectifica”.
        bridges = [it for it in sim_comps if it.comp_type == 'BRIDGE']
        bridge_vdc: Dict[str, float] = {}
        if bridges:
            out.append("\n── Puentes rectificadores (salida DC) ──")
            import math as _m
            for br in bridges:
                a1 = br.node1.strip() or pin_node.get(f"{br.name}__p1", "")
                a2 = br.node2.strip() or pin_node.get(f"{br.name}__p2", "")
                d_p = br.node3.strip() or pin_node.get(f"{br.name}__p3", "")
                d_n = (br.node4.strip()
                       if hasattr(br, 'node4') else
                       pin_node.get(f"{br.name}__p4", "")) \
                      or pin_node.get(f"{br.name}__p4", "")
                v1 = result['voltages'].get(a1, 0+0j)
                v2 = result['voltages'].get(a2, 0+0j)
                # Las tensiones del solver están en Vrms (fasor = Vrms·e^jφ).
                v_rms = abs(v1 - v2)
                v_pk  = v_rms * (2 ** 0.5)
                vf    = float(getattr(br, 'bridge_vf', 0.7) or 0.7)
                v_dc_peak = max(0.0, v_pk - 2 * vf)
                v_dc_avg  = max(0.0, (2.0 / _m.pi) * v_pk - 2 * vf)
                bridge_vdc[br.name] = v_dc_peak
                out.append(f"  {br.name} (V_f = {vf:.2f} V por diodo):")
                out.append(f"    V_AC entre AC1-AC2 : {v_rms:.4f} Vrms ({v_pk:.4f} Vpk)")
                out.append(f"    V_DC con filtro    ≈ {v_dc_peak:.4f} V"
                           f"  (pico − 2·Vf, salida {d_p} − {d_n})")
                out.append(f"    V_DC sin filtro    ≈ {v_dc_avg:.4f} V"
                           f"  (promedio onda completa)")
                br.result_voltage = v_dc_peak

            # ── Construir circuito DC con el puente como fuente ideal ────
            dc_components = []
            for it in sim_comps:
                if it.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                    continue
                if it.comp_type in ComponentItem.DIGITAL_TYPES:
                    continue
                a1 = it.node1.strip() or pin_node.get(f"{it.name}__p1", f'iso_{it.name}_p')
                a2 = it.node2.strip() or pin_node.get(f"{it.name}__p2", '0')
                if it.comp_type == 'V':
                    dc_components.append(VoltageSource(it.name, a1, a2, it.value))
                    continue
                if it.comp_type == 'VAC':
                    # AC source en DC = 0 V (valor medio de la senoidal)
                    dc_components.append(VoltageSource(it.name, a1, a2, 0.0))
                    continue
                if it.comp_type == 'BRIDGE':
                    d_p_b = it.node3.strip() or pin_node.get(f"{it.name}__p3", f'dcp_{it.name}')
                    d_n_b = (it.node4.strip() if hasattr(it,'node4') and it.node4.strip()
                             else pin_node.get(f"{it.name}__p4", f'dcn_{it.name}'))
                    vdc = bridge_vdc.get(it.name, 0.0)
                    # Fuente DC ideal entre DC+ y DC− equivalente al rectificado.
                    dc_components.append(
                        VoltageSource(f'{it.name}_DC', d_p_b, d_n_b, vdc))
                    # Mantener AC1/AC2 referenciados a DC− vía resistencia
                    # alta. Sin esto, el secundario del transformador queda
                    # flotando en la etapa DC y la matriz se vuelve singular.
                    dc_components.append(Resistor(
                        f'{it.name}_GR1', a1, d_n_b, 1e6))
                    dc_components.append(Resistor(
                        f'{it.name}_GR2', a2, d_n_b, 1e6))
                    continue
                # XFMR en DC: cada devanado es R_winding (no acopla AC→DC),
                # añadimos también la referencia a tierra para que no flote.
                if it.comp_type == 'XFMR':
                    dc_components.extend(build_engine_components_for_item(it, pin_node))
                    dc_components.append(Resistor(
                        f'{it.name}_GREF_DC', a2, '0', 1e6))
                    continue
                if it.comp_type == 'R' and it.value <= 0:
                    continue
                # Resto: igual que en DC normal
                try:
                    dc_components.extend(build_engine_components_for_item(it, pin_node))
                except Exception:
                    pass

            if dc_components:
                dc_solver = MNASolver()
                dc_res = dc_solver.solve_dc(dc_components)
                if dc_res.get('success'):
                    out.append("")
                    out.append("── Voltajes DC del lado rectificado ──")
                    for node, v in sorted(dc_res['voltages'].items()):
                        out.append(f"  V({node}) = {v:+.4f} V")
                    if dc_res.get('branch_currents'):
                        out.append("")
                        out.append("── Corrientes DC ──")
                        for name, i in dc_res['branch_currents'].items():
                            out.append(f"  I({name}) = {i*1000:+.4f} mA")
                    # Encender LEDs cuyos nodos quedaron polarizados con
                    # corriente directa real. Esto refleja en pantalla que
                    # el LED está conduciendo a la salida del puente.
                    op = dc_res.get('operating_points', {}) or {}
                    for it in sim_comps:
                        if it.comp_type != 'LED':
                            continue
                        a1 = it.node1.strip() or pin_node.get(f"{it.name}__p1", '')
                        a2 = it.node2.strip() or pin_node.get(f"{it.name}__p2", '0')
                        Id_op = (op.get(it.name, {}) or {}).get('Id') \
                                or (op.get(it.name, {}) or {}).get('id')
                        on = False
                        if Id_op is not None:
                            on = float(Id_op) > 1e-4
                        else:
                            v_a = dc_res['voltages'].get(a1, 0.0)
                            v_k = dc_res['voltages'].get(a2, 0.0)
                            vf_min = {'red':1.5,'orange':1.7,'yellow':1.8,
                                      'green':1.9,'blue':2.6,'white':2.6}
                            thr = vf_min.get(getattr(it,'led_color','red'), 1.5)
                            on = (v_a - v_k) > thr
                        it.led_on = on
                        it.update()
                else:
                    out.append(f"\n  ⚠ Análisis DC del lado rectificado falló: {dc_res.get('error')}")

        # ── Potencias por componente ──────────────────────────────────────
        out.append("\n── Potencias por componente ──")
        for name, pw in result['powers'].items():
            out.append(f"  {name}:")
            out.append(f"    P = {pw['P']:+.4f} W")
            out.append(f"    Q = {pw['Q']:+.4f} VAR")
            out.append(f"    S = {pw['S']:.4f} VA")
            out.append(f"    fp= {pw['fp']:.4f}")

        # ── Triángulo de potencia total ───────────────────────────────────
        t = result['total']
        out.append("\n── Potencia total del circuito ──")
        out.append(f"  P  = {t['P']:+.4f} W      (potencia real/activa)")
        out.append(f"  Q  = {t['Q']:+.4f} VAR    (potencia reactiva)")
        out.append(f"  S  = {t['S']:.4f} VA     (potencia aparente)")
        out.append(f"  fp = {t['fp']:.4f}  ({t['fp_type']})")
        out.append("")
        out.append("  [Haz click en '📐 Ver Triángulo de Potencia']")

        if result.get('warning'):
            out.append(f"\n⚠ {result['warning']}")

        self.results_text.setPlainText('\n'.join(out))
        self._last_ac_result = result
        self.btn_power_triangle.setVisible(True)


    def _run_simulation_mixed(self):
        """
        Simulación mixta analógica-digital.
        Construye:
          - Lista de componentes MNA (analógicos)
          - DigitalSimulator con puertas y flip-flops del canvas
          - Puentes ADC/DAC desde los bloques ADC_BRIDGE / DAC_BRIDGE
        Luego lanza MixedSignalInterface.run_iterative() y muestra resultados.
        """
        from engine.digital_engine import (
            DigitalSimulator, Gate, DFF, JKFF, TFF, SRFF,
            BinaryCounter, MUX,
        )
        from engine.bridges import ADC, DAC, ComparatorBridge, PWMBridge
        from engine.mixed_signal import MixedSignalInterface

        # ── Parámetros de simulación ─────────────────────────────────────
        t_stop = 1e-3      # 1 ms por defecto
        dt_chunk = 100e-6  # 100 µs por chunk

        self.results_text.setPlainText("Preparando simulación mixta...")
        QApplication.processEvents()

        sim_all = getattr(self, '_sim_all_comps', None) or list(self.scene.components)
        pin_node = getattr(self, '_sim_pin_node', None) or self.scene.extract_netlist()
        analog_comps = []
        errors = []

        # ── Construir circuito analógico ─────────────────────────────────
        for item in sim_all:
            if item.comp_type in ComponentItem.DIGITAL_TYPES:
                continue
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT', 'GND', 'NODE'):
                continue
            auto_n1 = pin_node.get(f"{item.name}__p1", f'iso_{item.name}_p')
            auto_n2 = pin_node.get(f"{item.name}__p2", '0')
            n1 = item.node1.strip() if item.node1.strip() else auto_n1
            n2 = item.node2.strip() if item.node2.strip() else auto_n2
            try:
                if item.comp_type in ComponentItem.DIGITAL_TYPES:
                    continue
                if item.comp_type == 'VAC':
                    # En esta ruta DC se ignora la VAC
                    analog_comps.append(VoltageSource(item.name, n1, n2, 0.0))
                    continue
                analog_comps.extend(build_engine_components_for_item(item, pin_node))
            except Exception as e:
                errors.append(f"{item.name}: {e}")

        if not analog_comps:
            self.results_text.setPlainText(
                "⚠  No hay componentes analógicos en el canvas.\n"
                "Añade al menos una fuente y una resistencia.")
            return

        # ── Construir circuito digital ────────────────────────────────────
        dsim = DigitalSimulator()
        adc_bridges = []
        dac_bridges = []

        _gate_map = {'AND':'AND','OR':'OR','NOT':'NOT',
                     'NAND':'NAND','NOR':'NOR','XOR':'XOR'}

        for item in sim_all:
            ct = item.comp_type
            tpd = item.dig_tpd_ns * 1e-9
            try:
                if ct in _gate_map:
                    n_in = max(1, item.dig_inputs)
                    ins  = [f'{item.name}_I{i}' for i in range(n_in)]
                    dsim.add(Gate(item.name, _gate_map[ct], ins,
                                  f'{item.name}_Y', t_pd=tpd,
                                  input_invert=list(
                                      getattr(item, 'dig_input_neg', []) or [])))
                elif ct == 'DFF':
                    dsim.add(DFF(item.name,
                                 d=f'{item.name}_D', clk=item.dig_clk,
                                 q=f'{item.name}_Q', qn=f'{item.name}_Qn',
                                 t_pd=tpd))
                elif ct == 'JKFF':
                    dsim.add(JKFF(item.name,
                                  j=f'{item.name}_J', k=f'{item.name}_K',
                                  clk=item.dig_clk,
                                  q=f'{item.name}_Q', qn=f'{item.name}_Qn',
                                  t_pd=tpd))
                elif ct == 'TFF':
                    dsim.add(TFF(item.name,
                                 t_in=f'{item.name}_T', clk=item.dig_clk,
                                 q=f'{item.name}_Q', qn=f'{item.name}_Qn',
                                 t_pd=tpd))
                elif ct == 'SRFF':
                    dsim.add(SRFF(item.name,
                                  s=f'{item.name}_S', r=f'{item.name}_R',
                                  q=f'{item.name}_Q', qn=f'{item.name}_Qn',
                                  t_pd=tpd))
                elif ct == 'COUNTER':
                    dsim.add(BinaryCounter(item.name, n=item.dig_bits,
                                           clk=item.dig_clk,
                                           q_prefix=f'{item.name}_Q',
                                           t_pd=tpd))
                elif ct == 'MUX2':
                    dsim.add(MUX(item.name,
                                 inputs=[f'{item.name}_I0', f'{item.name}_I1'],
                                 sel=[f'{item.name}_SEL'],
                                 output=f'{item.name}_Y', t_pd=tpd))
                elif ct == 'ADC_BRIDGE':
                    node = item.dig_analog_node or pin_node.get(f"{item.name}__p1", '')
                    adc  = ADC(item.name, node=node,
                               bits=item.dig_bits_adc,
                               vref=item.dig_vref,
                               clk=item.dig_clk if item.dig_clk else None)
                    adc_bridges.append(adc)
                elif ct == 'DAC_BRIDGE':
                    node = item.dig_analog_node or pin_node.get(f"{item.name}__p1", '')
                    dac  = DAC(item.name, bits=item.dig_bits_adc,
                               vref=item.dig_vref, out_node=node,
                               clk=item.dig_clk if item.dig_clk else None)
                    dac_bridges.append(dac)
                elif ct == 'COMPARATOR':
                    node = item.dig_analog_node or pin_node.get(f"{item.name}__p1", '')
                    cmp  = ComparatorBridge(item.name, node_pos=node)
                    adc_bridges.append(cmp)
                elif ct == 'PWM':
                    pwm  = PWMBridge(item.name,
                                     pwm_net=f'{item.name}_IN',
                                     vmax=item.dig_vref)
                    dac_bridges.append(pwm)
            except Exception as e:
                errors.append(f"{item.name} (digital): {e}")

        # ── Lanzar co-simulación ─────────────────────────────────────────
        iface = MixedSignalInterface(self.solver, dsim, analog_comps)
        for adc in adc_bridges:
            iface.add_adc(adc)
        for dac in dac_bridges:
            if hasattr(dac, 'pwm_net'):   # PWMBridge
                iface.add_pwm(dac)
            elif hasattr(dac, 'input_nets'):  # DAC
                iface.add_dac(dac)
            else:
                iface.add_comparator(dac)

        result = iface.run_iterative(
            t_stop=t_stop,
            dt_chunk=dt_chunk,
            dt_analog=min(dt_chunk / 10, 1e-6),
        )

        # ── Mostrar resultados ───────────────────────────────────────────
        out = ["═══ SIMULACIÓN MIXTA ═══", ""]
        if not result.success:
            out.append(f"✗ Error: {result.error}")
        else:
            out.append(result.summary())
            out.append("")
            out.append("── Voltajes analógicos (valor final) ──")
            if len(result.t):
                for node, arr in sorted(result.analog_voltages.items()):
                    if len(arr):
                        out.append(f"  V({node}) = {arr[-1]:+.4f} V")
            if result.digital_waveforms:
                out.append("")
                out.append("── Señales digitales (valor final) ──")
                for net, hist in sorted(result.digital_waveforms.items()):
                    if hist:
                        out.append(f"  {net} = {hist[-1][1]}")
            if result.adc_samples:
                out.append("")
                out.append("── Muestras ADC (último código) ──")
                for name, samples in result.adc_samples.items():
                    if samples:
                        out.append(f"  {name}: code={samples[-1][1]}")
            if result.warnings:
                out.append("")
                for w in result.warnings:
                    out.append(f"  ⚠ {w}")

        if errors:
            out.append("\n── Errores de construcción ──")
            out.extend([f"  ✗ {e}" for e in errors])

        self.results_text.setPlainText('\n'.join(out))

    def _show_power_triangle(self):
        if not self._last_ac_result:
            return
        dlg = PowerTriangleDialog(self._last_ac_result, COLORS, parent=self)
        dlg.exec()

    def _on_instrument_changed(self, item):
        """Un parámetro de instrumento (FGEN, …) cambió. Si el live
        transient está corriendo, reconstruyo la lista de componentes del
        motor con los nuevos valores. Manteniendo el estado anterior se
        evita un “salto” brusco en la simulación."""
        if not self._sim_running:
            return
        if self._live_components is None:
            return
        # Reconstruir componentes del motor para el item modificado.
        # Buscamos por nombre y reemplazamos en la lista.
        pin_node = self._live_pin_node or {}
        new_comps = build_engine_components_for_item(item, pin_node)
        if not new_comps:
            return
        new_by_name = {c.name: c for c in new_comps}
        self._live_components = [
            new_by_name.get(c.name, c) for c in self._live_components
        ]
        # CRÍTICO: si cambió la frecuencia de una fuente AC, recalcular
        # `_live_freq`. Sin esto el `dt_internal` del tick sigue calculado
        # para la frecuencia ANTERIOR, lo que produce aliasing severo y
        # hace que el circuito “no responda” al cambio de frecuencia.
        if item.comp_type in ('VAC', 'FGEN'):
            all_items = [it for sh in self._sheets for it in sh['scene'].components]
            new_freq = self._max_ac_source_frequency(all_items)
            if new_freq:
                prev_freq = self._live_freq
                self._live_freq = new_freq
                # Si la frecuencia cambió >2x (subió o bajó) reseteamos el
                # estado: los capacitores del filtro tienen carga residual
                # de la frecuencia anterior y mantenerla causa transitorios
                # MUY largos (o falsos steady-state) en el solver trapezoidal.
                if prev_freq > 0 and (new_freq / prev_freq > 2.0 or
                                       prev_freq / new_freq > 2.0):
                    self._live_state = None   # el próximo tick recalcula DC OP
                    # Limpiar buffers de osciloscopios abiertos: las muestras
                    # de la frecuencia anterior tienen otra escala temporal.
                    for it in self.scene.components:
                        if it.comp_type == 'OSC':
                            dlg = getattr(it, '_panel_dialog', None)
                            if dlg is not None and dlg.isVisible():
                                try:
                                    dlg.screen.clear()
                                except AttributeError:
                                    pass

    @staticmethod
    def _max_ac_source_frequency(items) -> float:
        """Frecuencia máxima entre todas las fuentes AC del iterable.
        Devuelve 0.0 si no hay fuentes AC."""
        return max((it.frequency for it in items
                    if it.comp_type in ('VAC', 'FGEN')), default=0.0)

    def _on_logic_state_toggled(self, item):
        """Re-ejecuta la simulación cuando un LOGIC_STATE cambia de estado."""
        if self._sim_running:
            self._run_simulation_dc(silent=True)
        else:
            # Aunque no esté en modo continuo, actualizar igual (one-shot silencioso)
            pin_node = self.scene.extract_netlist()
            std_name = DEFAULT_LOGIC_STANDARD
            std = LOGIC_STANDARDS.get(std_name, DEFAULT_STANDARD)
            # Calcular voltaje del estado y actualizar display del prop_table
            v = std.Voh if item.value else std.Vol
            self._on_component_selected(item)

    # ──────────────────────────────────────────────────────────────────
    # Potenciómetro: control en tiempo real desde el panel derecho
    # ──────────────────────────────────────────────────────────────────
    def _on_pot_slider(self, value: int):
        """El usuario movió el slider → actualiza el wiper en vivo."""
        if self._selected_pot is None:
            return
        self._selected_pot.pot_wiper = value / 1000.0
        self._selected_pot.update()           # repinta el componente con la flecha movida
        self._update_pot_label()
        # Si la simulación continua está corriendo, el siguiente tick
        # recalcula con el nuevo valor automáticamente.

    def _update_pot_label(self):
        if self._selected_pot is None:
            return
        w   = self._selected_pot.pot_wiper
        Rt  = max(self._selected_pot.value, 1.0)
        Ref = Rt * w
        # Formato bonito de R efectiva
        if Ref >= 1e6:   r_str = f"{Ref/1e6:.2f} MΩ"
        elif Ref >= 1e3: r_str = f"{Ref/1e3:.2f} kΩ"
        else:            r_str = f"{Ref:.2f} Ω"
        self.pot_value_label.setText(f"{w*100:.1f}% — R = {r_str}")

    def _on_component_selected(self, item):
        self.prop_table.setRowCount(0)
        # ── Slider del potenciómetro ──────────────────────────────────────
        if item is not None and item.comp_type == 'POT':
            self._selected_pot = item
            self.pot_slider.blockSignals(True)
            self.pot_slider.setValue(int(item.pot_wiper * 1000))
            self.pot_slider.blockSignals(False)
            self.pot_panel.setVisible(True)
            self._update_pot_label()
        else:
            self._selected_pot = None
            self.pot_panel.setVisible(False)

        if item is None:
            return

        # Nodos automáticos desde cables
        pin_node = self.scene.extract_netlist()

        def _node_display(manual, auto_key):
            v = manual.strip() if manual.strip() else pin_node.get(auto_key, '—')
            return v if manual.strip() else f"{v} (auto)"

        rows = [
            ("Tipo",     item.comp_type),
            ("Nombre",   item.name),
            ("Valor",    f"{item.value} {item.unit}"),
            ("Rotación", f"{item._angle}°"),
        ]

        if item.comp_type in DIGITAL_GATE_TYPES:
            n_in = item.dig_inputs if item.comp_type != 'NOT' else 1
            rows.append(("Salida (Y)", _node_display(item.node1, f"{item.name}__p1")))
            rows.append(("Entrada 1 (A)", _node_display(item.node2, f"{item.name}__p2")))
            if n_in >= 2:
                rows.append(("Entrada 2 (B)", _node_display(
                    item.node3 if hasattr(item,'node3') else '', f"{item.name}__p3")))
            for i in range(2, n_in):
                _extra = getattr(item, 'dig_input_nodes', [])
                _manual = _extra[i-2] if len(_extra) > i-2 else ''
                rows.append((f"Entrada {i+1}", _node_display(_manual, f"{item.name}__p{i+2}")))
            rows.append(("Nº entradas", str(n_in)))
            rows.append(("Retardo tpd", f"{item.dig_tpd_ns} ns"))
        elif item.comp_type in DIGITAL_FLIPFLOP_TYPES:
            rows.append(("Salida Q",    _node_display(item.node1, f"{item.name}__p1")))
            rows.append(("Dato D / J",  _node_display(item.node2, f"{item.name}__p2")))
            rows.append(("CLK",         _node_display(
                item.node3 if hasattr(item,'node3') else '', f"{item.name}__p3")))
        elif item.comp_type == 'LOGIC_STATE':
            rows.append(("Salida",  _node_display(item.node1, f"{item.name}__p1")))
            rows.append(("Estado",  "1 (HIGH)" if item.value else "0 (LOW)"))
        elif item.comp_type in FOUR_PIN_NODE_LABELS:
            lbls = FOUR_PIN_NODE_LABELS[item.comp_type]
            rows.append((lbls[0], _node_display(item.node1, f"{item.name}__p1")))
            rows.append((lbls[1], _node_display(item.node2, f"{item.name}__p2")))
            rows.append((lbls[2], _node_display(
                item.node3 if hasattr(item, 'node3') else '', f"{item.name}__p3")))
            rows.append((lbls[3], _node_display(
                item.node4 if hasattr(item, 'node4') else '', f"{item.name}__p4")))
        else:
            lbl1, lbl2, lbl3 = COMPONENT_NODE_LABELS.get(
                item.comp_type, DEFAULT_NODE_LABELS)
    
            # Para fuentes, invertir el orden de visualización
            # porque node1=pin izquierdo=negativo, node2=pin derecho=positivo
            if item.comp_type in ('V', 'VAC', 'I'):
                rows.append(("Nodo +", _node_display(item.node2, f"{item.name}__p2")))  # Nodo +
                rows.append(("Nodo -", _node_display(item.node1, f"{item.name}__p1")))  # Nodo −
            else:
                rows.append((lbl1, _node_display(item.node1, f"{item.name}__p1")))
                rows.append((lbl2, _node_display(item.node2, f"{item.name}__p2")))
            if lbl3 is not None:
                rows.append((lbl3, _node_display(
                    item.node3 if hasattr(item,'node3') else '', f"{item.name}__p3")))
            if item.comp_type == 'Z':
                rows.append(("Modo Z", item.z_mode))
                if item.z_mode == 'rect':
                    rows.append(("Z", f"{item.z_real:.4g} {item.z_imag:+.4g}j Ω"))
                else:
                    rows.append(("Z", f"{item.z_mag:.4g} ∠{item.z_phase:.2f}° Ω"))

        for label, val in rows:
            r = self.prop_table.rowCount()
            self.prop_table.insertRow(r)
            self.prop_table.setItem(r, 0, QTableWidgetItem(label))
            self.prop_table.setItem(r, 1, QTableWidgetItem(str(val)))

    # ── Circuito de demo ─────────────────────────
    def _load_demo_circuit(self):
        """Carga un divisor de voltaje de ejemplo."""
        s = self.scene
        s.place_component('V',   QPointF(-120,   0), 'V1', 10.0, 'V',  '0', 'A')
        s.place_component('R',   QPointF(   0, -80), 'R1', 1000.0, 'Ω', 'A', 'B')
        s.place_component('R',   QPointF(   0,  80), 'R2', 1000.0, 'Ω', 'B', '0')
        s.place_component('GND', QPointF(-120,  80), 'GND1')
        self.results_text.setPlainText(
            "Circuito demo: divisor de voltaje\n"
            "V1=10V, R1=R2=1kΩ\n\n"
            "Esperado: V(B) = 5.0 V\n\n"
            "Presiona ▶ SIMULAR para verificar.\n\n"
            "Tip: doble-click sobre un componente\npara editar sus nodos y valores."
        )

    # ── Acciones ─────────────────────────────────
    def _new_circuit(self):
        reply = QMessageBox.question(
            self, "Nuevo circuito",
            "¿Descartar el circuito actual (todas las hojas)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._clear_all_sheets()
            self._current_file = None
            self.setWindowTitle("PyNode — Simulador de Circuitos")
            self._load_demo_circuit()

    def _clear_circuit(self):
        """Limpia solo la hoja activa."""
        for item in self.scene.components + self.scene.wires:
            self.scene.removeItem(item)
        self.scene.components.clear()
        self.scene.wires.clear()
        self.scene._comp_counter.clear()
        self.results_text.clear()

    def _clear_all_sheets(self):
        """Elimina todas las hojas y crea una nueva vacía."""
        for sheet in self._sheets:
            sc = sheet['scene']
            for item in sc.components + sc.wires:
                sc.removeItem(item)
            sc.components.clear()
            sc.wires.clear()
            sc._comp_counter.clear()
        self._sheets.clear()
        self.tab_widget.clear()
        self._add_sheet(name="Hoja 1")
        self.results_text.clear()
        self._current_file = None
        self.setWindowTitle("PyNode — Simulador de Circuitos")

    # ── Serialización de una hoja ─────────────────
    def _serialize_sheet(self, scene: CircuitScene) -> dict:
        sheet_data = {'components': [], 'wires': []}
        for item in scene.components:
            entry = {
                'type':  item.comp_type,
                'name':  item.name,
                'value': item.value,
                'unit':  item.unit,
                'node1': item.node1,
                'node2': item.node2,
                'node3': item.node3,
                'x':     item.pos().x(),
                'y':     item.pos().y(),
                'angle': item._angle,
                'flip_x': item._flip_x,
                'flip_y': item._flip_y,
            }
            if item.comp_type in ('VAC', 'FGEN'):
                entry['frequency'] = item.frequency
                entry['phase_deg'] = item.phase_deg
                entry['ac_mode']   = item.ac_mode
            if item.comp_type == 'FGEN':
                entry['fgen_waveform'] = item.fgen_waveform
                entry['fgen_offset']   = item.fgen_offset
                entry['fgen_duty']     = item.fgen_duty
            if item.comp_type == 'LED':
                entry['led_color'] = item.led_color
            if item.comp_type == 'Z':
                entry['z_real']  = item.z_real
                entry['z_imag']  = item.z_imag
                entry['z_mag']   = item.z_mag
                entry['z_phase'] = item.z_phase
                entry['z_mode']  = item.z_mode
            if item.comp_type == 'POT':
                entry['pot_wiper'] = item.pot_wiper
            if item.comp_type == 'XFMR':
                entry['xfmr_ratio'] = item.xfmr_ratio
                entry['xfmr_imax']  = item.xfmr_imax
            if item.comp_type == 'BRIDGE':
                entry['bridge_vf'] = item.bridge_vf
            if item.comp_type in ComponentItem.FOUR_PIN_TYPES:
                entry['node4'] = item.node4
            if item.comp_type in ComponentItem.FIVE_PIN_TYPES:
                entry['node4']     = item.node4
                entry['node5']     = item.node5
                entry['tl082_unit'] = item.tl082_unit
            if item.comp_type in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
                entry['sheet_label'] = item.sheet_label
            if item.comp_type == 'CLK':
                entry['clk_running'] = item.clk_running
            if item.comp_type == 'MULTIMETER':
                entry['meter_quantity'] = item.meter_quantity
                entry['meter_coupling'] = item.meter_coupling
            if item.comp_type in ComponentItem.DIGITAL_TYPES:
                neg = list(getattr(item, 'dig_input_neg', []) or [])
                if any(neg):
                    entry['dig_input_neg'] = neg
            sheet_data['components'].append(entry)

        for wire in scene.wires:
            line = wire.line()
            sheet_data['wires'].append({
                'x1': line.x1(), 'y1': line.y1(),
                'x2': line.x2(), 'y2': line.y2(),
            })
        return sheet_data

    def _load_sheet_data(self, scene: CircuitScene, sheet_data: dict):
        for c in sheet_data.get('components', []):
            item = scene.place_component(
                c['type'], QPointF(c['x'], c['y']),
                name=c['name'], value=c['value'],
                unit=c.get('unit', ''),
                node1=c.get('node1', ''),
                node2=c.get('node2', ''),
                node3=c.get('node3', '')
            )
            angle = c.get('angle', 0)
            flip_x = bool(c.get('flip_x', False))
            flip_y = bool(c.get('flip_y', False))
            if angle or flip_x or flip_y:
                item._angle = angle
                item._flip_x = flip_x
                item._flip_y = flip_y
                item._apply_transform()
            if c['type'] == 'VAC':
                item.frequency = c.get('frequency', 60.0)
                item.phase_deg = c.get('phase_deg', 0.0)
                item.ac_mode   = c.get('ac_mode', 'rms')
            if c['type'] == 'FGEN':
                item.frequency     = c.get('frequency', item.frequency)
                item.phase_deg     = c.get('phase_deg', item.phase_deg)
                item.ac_mode       = c.get('ac_mode', item.ac_mode)
                item.fgen_waveform = c.get('fgen_waveform', item.fgen_waveform)
                item.fgen_offset   = c.get('fgen_offset', item.fgen_offset)
                item.fgen_duty     = c.get('fgen_duty', item.fgen_duty)
            if c['type'] == 'LED':
                item.led_color = c.get('led_color', 'red')
            if c['type'] == 'Z':
                item.z_real  = c.get('z_real',  100.0)
                item.z_imag  = c.get('z_imag',  0.0)
                item.z_mag   = c.get('z_mag',   100.0)
                item.z_phase = c.get('z_phase', 0.0)
                item.z_mode  = c.get('z_mode',  'rect')
            if c['type'] == 'POT' and 'pot_wiper' in c:
                item.pot_wiper = max(0.0, min(1.0, float(c['pot_wiper'])))
            if c['type'] == 'XFMR':
                item.xfmr_ratio = c.get('xfmr_ratio', 2.0)
                item.xfmr_imax  = c.get('xfmr_imax',  1.0)
            if c['type'] == 'BRIDGE':
                item.bridge_vf = c.get('bridge_vf', 0.7)
            if c['type'] in ComponentItem.FOUR_PIN_TYPES and 'node4' in c:
                item.node4 = c['node4']
            if c['type'] in ComponentItem.FIVE_PIN_TYPES:
                if 'node4' in c: item.node4 = c['node4']
                if 'node5' in c: item.node5 = c['node5']
                item.tl082_unit = c.get('tl082_unit', 'A')
            if c['type'] in ('NET_LABEL_IN', 'NET_LABEL_OUT'):
                item.sheet_label = c.get('sheet_label', item.name)
            if c['type'] == 'CLK':
                item.clk_running = bool(c.get('clk_running', False))
            if c['type'] == 'MULTIMETER':
                item.meter_quantity = c.get('meter_quantity', 'V')
                item.meter_coupling = c.get('meter_coupling', 'DC')
                item.meter_reading_unit_hint = {
                    'V': 'V', 'A': 'A', 'OHM': 'Ω'
                }.get(item.meter_quantity, 'V')
            if c['type'] in ComponentItem.DIGITAL_TYPES and 'dig_input_neg' in c:
                item.dig_input_neg = list(c['dig_input_neg'])

        for w in sheet_data.get('wires', []):
            wire = WireItem(QPointF(w['x1'], w['y1']), QPointF(w['x2'], w['y2']))
            scene.addItem(wire)
            scene.wires.append(wire)

    # ── Guardar (.csin) ──────────────────────────
    def _save_circuit(self):
        path = self._current_file
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self, "Guardar circuito", "",
                "PyNode (*.csin);;Todos los archivos (*)"
            )
        if not path:
            return
        if not path.endswith('.csin'):
            path += '.csin'

        sheets = []
        for sheet in self._sheets:
            sd = self._serialize_sheet(sheet['scene'])
            sd['name'] = sheet['name']
            sheets.append(sd)

        data = {'version': '2.0', 'sheets': sheets}

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self._current_file = path
        self.setWindowTitle(f"PyNode — {os.path.basename(path)}")
        self.statusBar().showMessage(f"Guardado: {path}")

    # ── Guardar como (.csin) ─────────────────────
    def _save_circuit_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Guardar circuito como", "",
            "PyNode (*.csin);;Todos los archivos (*)"
        )
        if not path:
            return
        if not path.endswith('.csin'):
            path += '.csin'
        self._current_file = path
        self._save_circuit()

    # ── Abrir (.csin) ────────────────────────────
    def _open_circuit(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Abrir circuito", "",
            "PyNode (*.csin);;Todos los archivos (*)"
        )
        if not path:
            return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir el archivo:\n{e}")
            return

        # Compatibilidad con formato v1 (una sola hoja)
        if 'sheets' not in data:
            data = {'version': '2.0', 'sheets': [{
                'name': 'Hoja 1',
                'components': data.get('components', []),
                'wires': data.get('wires', []),
            }]}

        self._clear_all_sheets()
        self._sheets.clear()
        self.tab_widget.clear()

        for sd in data['sheets']:
            name = sd.get('name', f'Hoja {len(self._sheets)+1}')
            self._add_sheet(name=name)
            scene = self._sheets[-1]['scene']
            self._load_sheet_data(scene, sd)

        self._current_file = path
        self.setWindowTitle(f"PyNode — {os.path.basename(path)}")
        self.statusBar().showMessage(f"Abierto: {path}")

    # ── Exportar netlist SPICE (.net) ────────────
    def _export_spice(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar netlist SPICE", "",
            "SPICE Netlist (*.net);;Todos los archivos (*)"
        )
        if not path:
            return
        if not path.endswith('.net'):
            path += '.net'

        lines = []
        lines.append(f"* PyNode — Netlist exportado")
        lines.append(f"* Archivo: {os.path.basename(path)}")
        lines.append("")

        type_map = {'R': 'R', 'C': 'C', 'L': 'L', 'V': 'V', 'I': 'I', 'Z': 'Z'}

        for item in self.scene.components:
            if item.comp_type not in type_map:
                continue
            n1 = item.node1.strip() or '?'
            n2 = item.node2.strip() or '0'
            val = item.value

            # Formatear valor en notación SPICE
            if abs(val) >= 1e6:
                val_str = f"{val/1e6:.6g}Meg"
            elif abs(val) >= 1e3:
                val_str = f"{val/1e3:.6g}k"
            elif abs(val) >= 1:
                val_str = f"{val:.6g}"
            elif abs(val) >= 1e-3:
                val_str = f"{val*1e3:.6g}m"
            elif abs(val) >= 1e-6:
                val_str = f"{val*1e6:.6g}u"
            elif abs(val) >= 1e-9:
                val_str = f"{val*1e9:.6g}n"
            else:
                val_str = f"{val:.6g}"

            # Para impedancias, exportar como R + jX
            if item.comp_type == 'Z':
                if item.z_mode == 'rect':
                    val_str = f"{item.z_real:.6g}"
                    if abs(item.z_imag) > 1e-12:
                        sign = '+' if item.z_imag >= 0 else ''
                        val_str += f"{sign}{item.z_imag:.6g}j"
                else:
                    ph_rad = math.radians(item.z_phase)
                    zr = item.z_mag * math.cos(ph_rad)
                    zx = item.z_mag * math.sin(ph_rad)
                    val_str = f"{zr:.6g}"
                    if abs(zx) > 1e-12:
                        sign = '+' if zx >= 0 else ''
                        val_str += f"{sign}{zx:.6g}j"

            lines.append(f"{item.name} {n1} {n2} {val_str}")

        lines.append("")
        lines.append(".op")
        lines.append(".end")

        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        self.statusBar().showMessage(f"Netlist exportado: {path}")
        QMessageBox.information(
            self, "Exportado",
            f"Netlist SPICE guardado en:\n{path}\n\nCompatible con LTspice y ngspice."
        )

    def _reset_zoom(self):
        self.view.resetTransform()
        self.view.centerOn(0, 0)

    def _wheel_zoom(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.view.scale(factor, factor)



# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PyNode")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
