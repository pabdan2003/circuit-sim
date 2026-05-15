"""
Diálogo de selección de fuente de muestras "hardware" para el osciloscopio.

El osciloscopio del simulador puede alimentarse desde dos fuentes:
  • Simulación interna (live transient) — comportamiento por defecto.
  • Hardware externo — un microcontrolador (RP2040 / RP2350 / STM32 …)
    que muestrea su ADC y envía un stream binario por USB-CDC serial.

Este diálogo elige puerto serial (o "Mock device" para pruebas sin HW),
baud rate y calibración por canal (ganancia y offset para mapear
mV crudos del ADC a voltios del osciloscopio). Al aceptar, arma el
hilo lector y deja el OSC conectado al stream HW. Re-abrirlo permite
cambiar configuración o desconectar.

Si pyserial no está instalado el botón "Conectar" se deshabilita y
solo el "Mock device" sigue disponible.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)


try:
    import serial.tools.list_ports as _serial_lp
    _SERIAL_AVAILABLE = True
except Exception:
    _SERIAL_AVAILABLE = False


MOCK_PORT_LABEL = '⟨Mock device⟩'


def _list_serial_ports() -> List[str]:
    """Devuelve los puertos serie disponibles ('COM3', '/dev/ttyACM0', etc.).
    Si pyserial no está, devuelve lista vacía."""
    if not _SERIAL_AVAILABLE:
        return []
    return [p.device for p in _serial_lp.comports()]


class HardwareSourceDialog(QDialog):
    """Configuración de la fuente HW para el osciloscopio.

    El padre debe ser un OscilloscopeDialog (o cualquier widget que mantenga
    un atributo `_hw_config` dict, persistido entre invocaciones).
    """

    def __init__(self, current_config: dict, colors: dict, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.setWindowTitle('Hardware — Osciloscopio')
        self.setModal(True)
        self._result: Optional[dict] = None
        self._cfg = dict(current_config) if current_config else {}
        self._build_ui()
        self._load_from_cfg()
        self._apply_style()

    # ── UI ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)

        gb_port = QGroupBox('Puerto')
        form_port = QFormLayout(gb_port)
        self.cb_port = QComboBox()
        self.cb_port.setMinimumWidth(160)
        self._refresh_ports()
        self.btn_refresh = QPushButton('↻')
        self.btn_refresh.setFixedWidth(28)
        self.btn_refresh.setToolTip('Refrescar lista de puertos')
        self.btn_refresh.clicked.connect(self._refresh_ports)
        port_row = QHBoxLayout()
        port_row.addWidget(self.cb_port, 1)
        port_row.addWidget(self.btn_refresh)
        port_w = QWidget(); port_w.setLayout(port_row)
        form_port.addRow('Puerto:', port_w)

        self.cb_baud = QComboBox()
        for b in ('115200', '230400', '460800', '921600', '1000000', '2000000'):
            self.cb_baud.addItem(b, int(b))
        form_port.addRow('Baudrate:', self.cb_baud)

        info = QLabel(
            '<small>Para USB-CDC (Pico / STM32 / Arduino nativo USB) el '
            'baudrate suele ignorarse — el firmware empuja a la '
            'velocidad máxima del bus. Pónlo igual al de tu '
            'firmware si usas UART físico.</small>')
        info.setWordWrap(True)
        form_port.addRow(info)
        root.addWidget(gb_port)

        # Calibración por canal: mV crudos → V del scope.
        # v_volt = gain * v_raw_mV + offset_V
        # Defaults: gain=1.0 mV→mV (sí, redundante) y offset=0.
        # El firmware ya envía en mV con su escala física, así que para un
        # frontend 0-3.3V con divisor /3 hay que poner gain=3.0 y offset=0.
        gb_cal = QGroupBox('Calibración (mV del HW → V mostrado)')
        form_cal = QFormLayout(gb_cal)
        self.sb_gain_a = QDoubleSpinBox()
        self.sb_gain_a.setRange(-1e6, 1e6); self.sb_gain_a.setDecimals(6)
        self.sb_gain_a.setValue(1.0)
        form_cal.addRow('Canal A — ganancia:', self.sb_gain_a)
        self.sb_off_a = QDoubleSpinBox()
        self.sb_off_a.setRange(-1e6, 1e6); self.sb_off_a.setDecimals(6)
        self.sb_off_a.setSuffix(' V')
        form_cal.addRow('Canal A — offset:', self.sb_off_a)
        self.sb_gain_b = QDoubleSpinBox()
        self.sb_gain_b.setRange(-1e6, 1e6); self.sb_gain_b.setDecimals(6)
        self.sb_gain_b.setValue(1.0)
        form_cal.addRow('Canal B — ganancia:', self.sb_gain_b)
        self.sb_off_b = QDoubleSpinBox()
        self.sb_off_b.setRange(-1e6, 1e6); self.sb_off_b.setDecimals(6)
        self.sb_off_b.setSuffix(' V')
        form_cal.addRow('Canal B — offset:', self.sb_off_b)
        root.addWidget(gb_cal)

        # Mock device: ajuste rápido de forma de onda para pruebas sin HW.
        gb_mock = QGroupBox('Mock device (si elegiste ⟨Mock device⟩)')
        form_mock = QFormLayout(gb_mock)
        self.cb_mock_wave = QComboBox()
        self.cb_mock_wave.addItems(['Senoidal', 'Cuadrada', 'Triangular'])
        form_mock.addRow('Forma de onda A:', self.cb_mock_wave)
        self.sb_mock_freq = QDoubleSpinBox()
        self.sb_mock_freq.setRange(0.1, 1e6); self.sb_mock_freq.setDecimals(2)
        self.sb_mock_freq.setValue(1000.0); self.sb_mock_freq.setSuffix(' Hz')
        form_mock.addRow('Frecuencia:', self.sb_mock_freq)
        self.sb_mock_amp = QDoubleSpinBox()
        self.sb_mock_amp.setRange(0.0, 100.0); self.sb_mock_amp.setDecimals(3)
        self.sb_mock_amp.setValue(1.0); self.sb_mock_amp.setSuffix(' V')
        form_mock.addRow('Amplitud A:', self.sb_mock_amp)
        self.sb_mock_rate = QDoubleSpinBox()
        self.sb_mock_rate.setRange(100.0, 1e6); self.sb_mock_rate.setDecimals(0)
        self.sb_mock_rate.setValue(50000.0); self.sb_mock_rate.setSuffix(' Sps')
        form_mock.addRow('Sample rate:', self.sb_mock_rate)
        root.addWidget(gb_mock)

        # Si pyserial no está instalado, deshabilitamos puerto/baud y
        # forzamos al usuario a Mock device.
        if not _SERIAL_AVAILABLE:
            warn = QLabel(
                '<small style="color: #e94560;">pyserial no instalado. '
                'Solo Mock device está disponible. Instala con '
                '<code>pip install pyserial</code>.</small>')
            warn.setWordWrap(True)
            root.addWidget(warn)
            self.cb_port.setEnabled(False)
            self.cb_baud.setEnabled(False)

        # Botones
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _apply_style(self):
        c = self.colors
        panel = c.get('panel', '#16213e')
        text = c.get('text', '#e0e0e0')
        body = c.get('comp_body', '#16213e')
        border = c.get('panel_brd', '#0f3460')
        accent = c.get('component', '#e94560')
        hover = c.get('comp_sel', '#f5a623')
        self.setStyleSheet(f"""
            QDialog {{ background: {panel}; color: {text}; }}
            QLabel  {{ color: {text}; }}
            QGroupBox {{ border: 1px solid {border}; border-radius: 4px;
                         margin-top: 10px; padding-top: 8px; color: {text}; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
            QComboBox, QDoubleSpinBox {{
                background: {c.get('bg', '#1a1a2e')}; color: {text};
                border: 1px solid {border}; border-radius: 4px; padding: 4px;
            }}
            QPushButton {{
                background: {body}; color: {text};
                border: 1px solid {border}; border-radius: 4px; padding: 4px 12px;
            }}
            QPushButton:hover {{ background: {hover}; color: white; }}
        """)

    def _refresh_ports(self):
        current = self.cb_port.currentText() if self.cb_port.count() else ''
        self.cb_port.clear()
        self.cb_port.addItem(MOCK_PORT_LABEL)
        for p in _list_serial_ports():
            self.cb_port.addItem(p)
        # Restaurar selección previa si todavía existe
        idx = self.cb_port.findText(current) if current else -1
        if idx >= 0:
            self.cb_port.setCurrentIndex(idx)

    def _load_from_cfg(self):
        c = self._cfg
        port = c.get('port', MOCK_PORT_LABEL)
        idx = self.cb_port.findText(port)
        if idx >= 0:
            self.cb_port.setCurrentIndex(idx)
        else:
            # Puerto guardado ya no existe: agregarlo deshabilitado en el combo
            self.cb_port.addItem(f'{port} (no disponible)')
            self.cb_port.setCurrentIndex(self.cb_port.count() - 1)
        baud = c.get('baud', 921600)
        i = self.cb_baud.findData(int(baud))
        if i >= 0:
            self.cb_baud.setCurrentIndex(i)
        self.sb_gain_a.setValue(float(c.get('gain_a', 1.0)))
        self.sb_off_a.setValue(float(c.get('offset_a', 0.0)))
        self.sb_gain_b.setValue(float(c.get('gain_b', 1.0)))
        self.sb_off_b.setValue(float(c.get('offset_b', 0.0)))
        # Mock
        wave = c.get('mock_wave', 'Senoidal')
        i = self.cb_mock_wave.findText(wave)
        if i >= 0:
            self.cb_mock_wave.setCurrentIndex(i)
        self.sb_mock_freq.setValue(float(c.get('mock_freq', 1000.0)))
        self.sb_mock_amp.setValue(float(c.get('mock_amp', 1.0)))
        self.sb_mock_rate.setValue(float(c.get('mock_rate', 50000.0)))

    def _accept(self):
        port_text = self.cb_port.currentText()
        if port_text.endswith('(no disponible)'):
            # Limpiar el sufijo informativo
            port_text = port_text.split(' (')[0]
        self._result = {
            'port':       port_text,
            'baud':       int(self.cb_baud.currentData()),
            'gain_a':     float(self.sb_gain_a.value()),
            'offset_a':   float(self.sb_off_a.value()),
            'gain_b':     float(self.sb_gain_b.value()),
            'offset_b':   float(self.sb_off_b.value()),
            'mock_wave':  self.cb_mock_wave.currentText(),
            'mock_freq':  float(self.sb_mock_freq.value()),
            'mock_amp':   float(self.sb_mock_amp.value()),
            'mock_rate':  float(self.sb_mock_rate.value()),
            'is_mock':    (port_text == MOCK_PORT_LABEL),
        }
        self.accept()

    def get_result(self) -> Optional[dict]:
        return self._result
