#!/usr/bin/env python3
"""
dictate_meter.py — live microphone level overlay for the dictation daemon.

A small, frameless, always-on-top window that appears while recording and
shows a scrolling VU/waveform of your voice so you can see the mic is hearing
you. It opens its own capture stream (PipeWire/Pulse allow multiple readers),
so it's fully independent of the daemon — the daemon just launches it on record
start and kills it on stop.

Run standalone to preview:  ./venv/bin/python dictate_meter.py
"""

from __future__ import annotations

import math
import signal
import sys
from collections import deque

import numpy as np
import sounddevice as sd
from PyQt5 import QtCore, QtGui, QtWidgets

SAMPLE_RATE = 16000
BARS = 56               # number of history bars across the meter
FLOOR_DB = -60.0        # rms quieter than this reads as empty

# Colours (level 0..1 → colour)
GREEN = QtGui.QColor(80, 220, 120)
YELLOW = QtGui.QColor(240, 210, 70)
RED = QtGui.QColor(240, 90, 80)
DIM = QtGui.QColor(255, 255, 255, 40)


def rms_to_level(rms: float) -> float:
    """Map an RMS amplitude to a perceptual 0..1 level via a dB scale."""
    if rms <= 1e-6:
        return 0.0
    db = 20.0 * math.log10(rms)
    return max(0.0, min(1.0, (db - FLOOR_DB) / (0.0 - FLOOR_DB)))


def level_colour(level: float) -> QtGui.QColor:
    if level > 0.85:
        return RED
    if level > 0.6:
        return YELLOW
    return GREEN


class Meter(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
            | QtCore.Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.resize(440, 96)

        self._history: deque = deque([0.0] * BARS, maxlen=BARS)
        self._level = 0.0        # written by the audio callback thread
        self._peak = 0.0
        self._pulse = 0.0        # 0..1 for the REC dot animation

        self._stream = self._open_stream()

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)    # ~30 fps

        self._place_bottom_centre()

    # ---- audio ----------------------------------------------------------
    def _open_stream(self):
        def callback(indata, frames, time_info, status):  # noqa: ANN001
            rms = float(np.sqrt(np.mean(np.square(indata))))
            self._level = rms
            self._peak = max(self._peak, rms)
        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                blocksize=1024, callback=callback,
            )
            stream.start()
            return stream
        except Exception as exc:  # no mic / busy — show the window anyway
            print(f"meter: could not open mic: {exc}", file=sys.stderr)
            return None

    # ---- animation ------------------------------------------------------
    def _tick(self) -> None:
        level = rms_to_level(self._level)
        self._history.append(level)
        self._peak *= 0.9         # decay the peak hold
        self._pulse = (self._pulse + 0.06) % 1.0
        self.update()

    def _place_bottom_centre(self) -> None:
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        x = screen.x() + (screen.width() - self.width()) // 2
        y = screen.y() + screen.height() - self.height() - 120
        self.move(x, y)

    # ---- painting -------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: ANN001
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        # Rounded translucent background.
        rect = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(20, 22, 28, 220))
        p.drawRoundedRect(rect, 16, 16)

        # "● REC" indicator (pulsing dot).
        dot_alpha = int(120 + 135 * (0.5 + 0.5 * math.sin(self._pulse * 2 * math.pi)))
        p.setBrush(QtGui.QColor(240, 70, 70, dot_alpha))
        p.drawEllipse(20, 40, 14, 14)
        p.setPen(QtGui.QColor(235, 235, 240))
        font = p.font()
        font.setBold(True)
        font.setPointSize(10)
        p.setFont(font)
        p.drawText(44, 51, "REC")

        # Waveform / VU history — mirrored bars scrolling right.
        left = 92
        right = self.width() - 18
        area_w = right - left
        cy = self.height() / 2
        max_h = self.height() / 2 - 14
        n = len(self._history)
        gap = 2.0
        bar_w = max(1.5, area_w / n - gap)
        for i, lv in enumerate(self._history):
            x = left + i * (bar_w + gap)
            h = max(2.0, lv * max_h)
            col = level_colour(lv)
            # Fade older bars slightly toward the left.
            col = QtGui.QColor(col)
            col.setAlpha(int(90 + 165 * (i / max(1, n - 1))))
            p.setBrush(col)
            p.setPen(QtCore.Qt.NoPen)
            p.drawRoundedRect(QtCore.QRectF(x, cy - h, bar_w, h * 2), 1.5, 1.5)

        # Faint centre line.
        p.setPen(QtGui.QPen(DIM, 1))
        p.drawLine(left, int(cy), right, int(cy))
        p.end()

    # ---- shutdown -------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: ANN001
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        event.accept()


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    # Let the Python interpreter process SIGTERM/SIGINT while Qt's loop runs.
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    idle = QtCore.QTimer()
    idle.start(200)
    idle.timeout.connect(lambda: None)

    meter = Meter()
    meter.show()
    app.exec_()


if __name__ == "__main__":
    main()
