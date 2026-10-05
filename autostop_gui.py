#!/usr/bin/env python3
"""AutoStop - wersja okienkowa.

Co sekundę pokazuje prędkość pobierania i kolejkę gier, a po kliknięciu
"Start" wyłącza komputer, gdy gry skończą się pobierać.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from collections import deque
from tkinter import font as tkfont
from tkinter import messagebox

import autostop

REFRESH_SECONDS = 1.0
HISTORY_SECONDS = 120
STALL_MINUTES = 30

# Kolory (ciemny motyw)
BG = "#0d1117"
CARD = "#161b22"
CARD_EDGE = "#232a35"
TRACK = "#262e3a"
TEXT = "#f0f3f6"
MUTED = "#8d96a0"
FAINT = "#5d6672"
ACCENT = "#4c8dff"
ACCENT_HOVER = "#6aa1ff"
CHART_FILL = "#16294a"
GREEN = "#3fcf8e"
AMBER = "#f2b84b"
RED = "#f4645f"
RED_HOVER = "#ff7b76"


def polish_games(n: int) -> str:
    if n == 1:
        return "1 gra"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} gry"
    return f"{n} gier"


class Worker(threading.Thread):
    """Co sekundę mierzy w tle, żeby okno nigdy się nie zacinało."""

    def __init__(self, monitor: autostop.Monitor, out: queue.Queue):
        super().__init__(daemon=True)
        self.monitor = monitor
        self.out = out

    def run(self) -> None:
        tick = 0
        next_time = time.monotonic()
        while True:
            try:
                generation = self.monitor.generation
                snapshot = self.monitor.poll()
                launchers = (autostop.running_launchers(self.monitor.extra)
                             if tick % 5 == 0 else None)
                self.out.put(("snapshot", generation, snapshot, launchers))
            except Exception:  # pokazujemy w oknie zamiast cicho umierać
                self.out.put(("error", traceback.format_exc()))
            tick += 1
            next_time += REFRESH_SECONDS
            time.sleep(max(0.0, next_time - time.monotonic()))


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.scale = root.winfo_fpixels("1i") / 96
        self._setup_fonts()

        self.idle_minutes = 5
        self.countdown_seconds = 60
        self.test_mode = False

        self.monitor = autostop.Monitor(idle_minutes=self.idle_minutes,
                                        stall_minutes=STALL_MINUTES, window=5)
        self.updates: queue.Queue = queue.Queue()
        self.snapshot: autostop.Snapshot | None = None
        self.history: deque[float] = deque(maxlen=HISTORY_SECONDS)
        self.launchers: list[str] = []
        self.error: str | None = None

        self.armed = False
        self.armed_generation = -1
        self.seen_download = False  # czy od kliknięcia Start coś się pobierało
        self.countdown_left: int | None = None
        self.result: tuple[str, str, str] | None = None  # (kolor, tytuł, opis)
        self.tick = 0
        self.hover_x: float | None = None
        self.button_hover = False

        self._build()
        self.redraw()
        Worker(self.monitor, self.updates).start()
        root.after(100, self._drain_updates)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------ wygląd

    def px(self, value: float) -> int:
        return int(round(value * self.scale))

    def _setup_fonts(self) -> None:
        families = set(tkfont.families(self.root))
        family = next((f for f in ("Segoe UI", "Inter", "Cantarell", "DejaVu Sans")
                       if f in families), "Helvetica")
        bold_family = "Segoe UI Semibold" if "Segoe UI Semibold" in families else family
        weight = "normal" if bold_family != family else "bold"

        def make(size, bold=False, fam=None):
            if bold:
                return tkfont.Font(self.root, family=fam or bold_family, size=size, weight=weight)
            return tkfont.Font(self.root, family=fam or family, size=size)

        self.f_title = make(18, True)
        self.f_huge = make(30, True)
        self.f_big = make(16, True)
        self.f_ring = make(17, True)
        self.f_text = make(10)
        self.f_text_bold = make(10, True)
        self.f_button = make(12, True)
        self.f_small = make(9)
        self.f_label = make(8, True)
        self.f_unit = make(13)

    def _build(self) -> None:
        p = self.px
        self.root.title("AutoStop")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        pad, gap = 22, 14
        left_w, right_w = 360, 430
        width = pad + left_w + gap + right_w + pad
        top = pad + 52 + gap
        status_h, settings_h, button_h = 180, 150, 56
        speed_h = 214
        column_h = status_h + gap + settings_h + gap + button_h
        queue_h = column_h - speed_h - gap
        height = top + column_h + 10 + 20 + pad - 6
        self.root.geometry(f"{p(width)}x{p(height)}")

        def canvas(x, y, w, h):
            c = tk.Canvas(self.root, width=p(w), height=p(h), bg=BG,
                          highlightthickness=0, bd=0)
            c.place(x=p(x), y=p(y))
            return c

        right_x = pad + left_w + gap
        self.c_header = canvas(pad, pad, width - 2 * pad, 52)
        self.c_status = canvas(pad, top, left_w, status_h)
        self.c_settings = canvas(pad, top + status_h + gap, left_w, settings_h)
        self.c_button = canvas(pad, top + status_h + gap + settings_h + gap, left_w, button_h)
        self.c_speed = canvas(right_x, top, right_w, speed_h)
        self.c_queue = canvas(right_x, top + speed_h + gap, right_w, queue_h)
        self.c_footer = canvas(pad, top + column_h + 10, width - 2 * pad, 20)

        self.c_speed.bind("<Motion>", lambda e: self._set_hover(e.x))
        self.c_speed.bind("<Leave>", lambda e: self._set_hover(None))
        self.c_button.bind("<Button-1>", lambda e: self._on_button())
        self.c_button.bind("<Enter>", lambda e: self._set_button_hover(True))
        self.c_button.bind("<Leave>", lambda e: self._set_button_hover(False))
        self.c_button.configure(cursor="hand2")

    def round_rect(self, c: tk.Canvas, x1, y1, x2, y2, r, **kw):
        x1, y1, x2, y2, r = (self.px(v) for v in (x1, y1, x2, y2, r))
        points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
                  x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return c.create_polygon(points, smooth=True, **kw)

    def card(self, c: tk.Canvas) -> tuple[float, float]:
        c.delete("all")
        w = c.winfo_reqwidth() / self.scale
        h = c.winfo_reqheight() / self.scale
        self.round_rect(c, 0, 0, w - 1, h - 1, 16, fill=CARD, outline=CARD_EDGE)
        return w, h

    def text(self, c, x, y, text, font, fill=TEXT, anchor="nw", width=None, **kw):
        if width:
            kw["width"] = self.px(width)
        return c.create_text(self.px(x), self.px(y), text=text, font=font,
                             fill=fill, anchor=anchor, **kw)

    def fit(self, text: str, font: tkfont.Font, width: float) -> str:
        width = self.px(width)
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    # ------------------------------------------------------------ stan

    def _status(self) -> tuple[str, str, str, float, str]:
        """Zwraca (kolor, tytuł, opis, wypełnienie pierścienia 0-1, środek)."""
        if self.error:
            return RED, "Błąd pomiaru", self.error.strip().splitlines()[-1], 1.0, "!"
        if self.countdown_left is not None:
            description = "Gry się pobrały. Kliknij „Anuluj wyłączanie”, aby przerwać."
            return (RED, "Wyłączam komputer", description,
                    self.countdown_left / self.countdown_seconds, str(self.countdown_left))
        if self.result:
            color, title, description = self.result
            return color, title, description, 1.0, "power"
        if not self.armed:
            return (FAINT, "Gotowy", "Włącz pobieranie gier i kliknij Start. "
                    "Wyłączę komputer, gdy wszystko się pobierze.", 0.0, "power")
        verdict = self.snapshot.verdict if self.snapshot else None
        if verdict is None or verdict.state == "waiting":
            return (ACCENT, "Czekam na pobieranie",
                    "Zacznę pilnować, gdy gry zaczną się pobierać.", -1.0, "spin")
        if verdict.state == "downloading":
            return (GREEN, "Gry się pobierają",
                    f"Wyłączę komputer po {self.idle_minutes} min bez pobierania.", 1.0, "arrow")
        remaining = max(0.0, verdict.quiet_limit - verdict.quiet_for)
        if not self.seen_download:
            return (ACCENT, "Gry czekają w kolejce",
                    "Czekam, aż ruszy pobieranie.", -1.0, "spin")
        description = f"Wyłączę za {autostop.format_duration(remaining)}, jeśli nic nie ruszy."
        if self.snapshot.games:
            description += " Coś czeka w kolejce, więc czekam dłużej."
        fraction = verdict.quiet_for / verdict.quiet_limit if verdict.quiet_limit else 1.0
        return (AMBER, "Pobieranie ustało", description, fraction,
                autostop.format_duration(remaining))

    def _drain_updates(self) -> None:
        changed = False
        try:
            while True:
                item = self.updates.get_nowait()
                changed = True
                if item[0] == "error":
                    self.error = item[1]
                    continue
                _, generation, snapshot, launchers = item
                self.error = None
                self.snapshot = snapshot
                self.history.append(snapshot.speed or 0.0)
                if launchers is not None:
                    self.launchers = launchers
                if not self.armed or generation != self.armed_generation:
                    continue
                if snapshot.verdict.state == "downloading":
                    self.seen_download = True
                if snapshot.verdict.state == "done" and self.countdown_left is None:
                    self._start_countdown()
        except queue.Empty:
            pass
        if changed:
            self.tick += 1
            self.redraw()
        self.root.after(100, self._drain_updates)

    # ------------------------------------------------------------ akcje

    def _on_button(self) -> None:
        if self.countdown_left is not None:
            self.countdown_left = None
            self.armed = False
            self.result = (FAINT, "Anulowano", "Komputer nie zostanie wyłączony.")
        elif self.armed:
            self.armed = False
        else:
            self.result = None
            self.monitor.reset(self.idle_minutes, STALL_MINUTES)
            self.armed_generation = self.monitor.generation
            self.armed = True
            self.seen_download = False
        self.redraw()

    def _start_countdown(self) -> None:
        self.countdown_left = self.countdown_seconds
        # Pokaż okno na wierzchu, żeby było widać odliczanie.
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(500, lambda: self.root.attributes("-topmost", False))
        self.root.after(1000, self._countdown_tick)

    def _countdown_tick(self) -> None:
        if self.countdown_left is None:
            return
        self.countdown_left -= 1
        if self.countdown_left > 0:
            self.redraw()
            self.root.after(1000, self._countdown_tick)
            return
        self.countdown_left = None
        self.armed = False
        if self.test_mode:
            self.result = (GREEN, "Test udany",
                           "Gry się pobrały. Bez trybu testowego komputer właśnie by się wyłączył.")
        else:
            self.result = (RED, "Wyłączam komputer...", "")
            self.redraw()
            self._shutdown()
        self.redraw()

    def _shutdown(self) -> None:
        flags = 0x08000000 if sys.platform == "win32" else 0  # bez czarnego okna
        try:
            subprocess.run(autostop.shutdown_command(False), check=True, creationflags=flags)
        except (OSError, subprocess.CalledProcessError) as error:
            self.result = (RED, "Nie udało się wyłączyć", str(error))

    def _change(self, setting: str, direction: int) -> None:
        if self.armed:
            return
        if setting == "idle":
            step = 1 if self.idle_minutes + direction <= 10 else 5
            self.idle_minutes = min(120, max(1, self.idle_minutes + direction * step))
        elif setting == "countdown":
            self.countdown_seconds = min(300, max(10, self.countdown_seconds + direction * 10))
        elif setting == "test":
            self.test_mode = not self.test_mode
        self.redraw()

    def _set_hover(self, x: float | None) -> None:
        self.hover_x = x
        self.draw_speed()

    def _set_button_hover(self, value: bool) -> None:
        self.button_hover = value
        self.draw_button()

    def _on_close(self) -> None:
        if self.armed and not messagebox.askyesno(
                "AutoStop", "Zamknąć AutoStop? Komputer nie wyłączy się sam po pobraniu gier."):
            return
        self.root.destroy()

    # ------------------------------------------------------------ rysowanie

    def redraw(self) -> None:
        self.draw_header()
        self.draw_status()
        self.draw_settings()
        self.draw_button()
        self.draw_speed()
        self.draw_queue()
        self.draw_footer()

    def draw_power_icon(self, c, cx, cy, r, color, width):
        p = self.px
        c.create_arc(p(cx - r), p(cy - r), p(cx + r), p(cy + r), start=125, extent=290,
                     style="arc", outline=color, width=p(width))
        c.create_line(p(cx), p(cy - r - 2), p(cx), p(cy - 1), fill=color,
                      width=p(width), capstyle="round")

    def draw_header(self) -> None:
        c = self.c_header
        c.delete("all")
        self.round_rect(c, 0, 2, 48, 50, 14, fill=ACCENT, outline=ACCENT)
        self.draw_power_icon(c, 24, 27, 11, "white", 3)
        self.text(c, 62, 2, "AutoStop", self.f_title)
        self.text(c, 63, 32, "Wyłączy komputer, gdy gry się pobiorą", self.f_text, MUTED)
        if self.test_mode:
            w = c.winfo_reqwidth() / self.scale
            label = "TRYB TESTOWY"
            tw = self.f_label.measure(label) / self.scale + 24
            self.round_rect(c, w - tw, 14, w, 38, 12, fill="#3a2f12", outline="#3a2f12")
            self.text(c, w - tw / 2, 26, label, self.f_label, AMBER, anchor="center")

    def draw_status(self) -> None:
        c = self.c_status
        w, h = self.card(c)
        p = self.px
        color, title, description, fraction, center = self._status()

        cx, cy, r = 72, h / 2, 48
        box = (p(cx - r), p(cy - r), p(cx + r), p(cy + r))
        c.create_oval(*box, outline=TRACK, width=p(10))
        if fraction < 0:  # kręcący się łuk
            c.create_arc(*box, start=90 - self.tick * 30, extent=-100, style="arc",
                         outline=color, width=p(10))
        elif fraction > 0:
            c.create_arc(*box, start=90, extent=-min(fraction, 0.9999) * 360, style="arc",
                         outline=color, width=p(10))

        if center == "power":
            self.draw_power_icon(c, cx, cy + 2, 16, color, 4)
        elif center == "arrow":
            c.create_line(p(cx), p(cy - 20), p(cx), p(cy + 18), fill=color, width=p(5),
                          arrow="last", arrowshape=(p(16), p(16), p(9)), capstyle="round")
            c.create_line(p(cx - 18), p(cy + 24), p(cx + 18), p(cy + 24), fill=color,
                          width=p(4), capstyle="round")
        elif center == "spin":
            for i, dx in enumerate((-14, 0, 14)):
                on = i == self.tick % 3
                c.create_oval(p(cx + dx - 4), p(cy - 4), p(cx + dx + 4), p(cy + 4),
                              fill=color if on else TRACK, outline="")
        else:
            font = self.f_huge if len(center) <= 3 else self.f_ring
            self.text(c, cx, cy, center, font, TEXT, anchor="center")

        # Etykieta, tytuł i opis jeden pod drugim, całość wyśrodkowana w pionie.
        tx, text_w = 140, w - 140 - 18
        c.create_oval(p(tx), p(3), p(tx + 8), p(11), fill=color, outline="", tags="block")
        self.text(c, tx + 14, 0, "STATUS", self.f_label, FAINT, tags="block")
        title_id = self.text(c, tx, 18, title, self.f_big, TEXT, width=text_w, tags="block")
        if description:
            bottom = c.bbox(title_id)[3] / self.scale
            self.text(c, tx, bottom + 6, description, self.f_text, MUTED, width=text_w,
                      tags="block")
        top, bottom = c.bbox("block")[1], c.bbox("block")[3]
        c.move("block", 0, (p(h) - (bottom - top)) / 2 - top)

    def draw_settings(self) -> None:
        c = self.c_settings
        w, h = self.card(c)
        p = self.px
        enabled = not self.armed
        ink = TEXT if enabled else FAINT

        rows = [
            ("Wyłącz po", "ile minut bez pobierania", "idle", f"{self.idle_minutes} min"),
            ("Odliczanie", "zanim komputer się wyłączy", "countdown",
             f"{self.countdown_seconds} s"),
            ("Tryb testowy", "nie wyłączaj naprawdę", "test", None),
        ]
        for i, (label, hint, key, value) in enumerate(rows):
            y = 26 + i * 42
            self.text(c, 20, y - 10, label, self.f_text_bold, ink)
            self.text(c, 20, y + 9, hint, self.f_small, FAINT)
            if value is None:
                self._draw_toggle(c, w - 20, y + 4, self.test_mode, enabled)
                continue
            for direction, sx, sign in ((-1, w - 128, "−"), (1, w - 34, "+")):
                tag = f"{key}{direction}"
                c.create_oval(p(sx - 14), p(y + 4 - 14), p(sx + 14), p(y + 4 + 14),
                              fill=TRACK, outline="", tags=tag)
                self.text(c, sx, y + 3, sign, self.f_button, ink, anchor="center", tags=tag)
                self._clickable(c, tag, lambda e, k=key, d=direction: self._change(k, d),
                                enabled)
            self.text(c, (w - 128 + w - 34) / 2, y + 4, value, self.f_text_bold, ink,
                      anchor="center")
        self._clickable(c, "test", lambda e: self._change("test", 0), enabled)

    def _draw_toggle(self, c, right, cy, on, enabled):
        p = self.px
        fill = (GREEN if on else TRACK) if enabled else CARD_EDGE
        self.round_rect(c, right - 44, cy - 12, right, cy + 12, 12, fill=fill, outline=fill,
                        tags="test")
        kx = right - 12 if on else right - 32
        knob = "white" if enabled else MUTED
        c.create_oval(p(kx - 9), p(cy - 9), p(kx + 9), p(cy + 9), fill=knob, outline="",
                      tags="test")

    def _clickable(self, c, tag, callback, enabled):
        if not enabled:
            return
        c.tag_bind(tag, "<Button-1>", callback)
        c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
        c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))

    def draw_button(self) -> None:
        c = self.c_button
        c.delete("all")
        w = c.winfo_reqwidth() / self.scale
        h = c.winfo_reqheight() / self.scale
        if self.countdown_left is not None:
            fill = RED_HOVER if self.button_hover else RED
            self.round_rect(c, 0, 0, w - 1, h - 1, 16, fill=fill, outline=fill)
            self.text(c, w / 2, h / 2, "Anuluj wyłączanie", self.f_button, "white",
                      anchor="center")
        elif self.armed:
            edge = MUTED if self.button_hover else CARD_EDGE
            self.round_rect(c, 0, 0, w - 1, h - 1, 16, fill=CARD, outline=edge, width=2)
            self.text(c, w / 2, h / 2, "Zatrzymaj", self.f_button, TEXT, anchor="center")
        else:
            fill = ACCENT_HOVER if self.button_hover else ACCENT
            self.round_rect(c, 0, 0, w - 1, h - 1, 16, fill=fill, outline=fill)
            self.text(c, w / 2, h / 2, "Start  ·  wyłącz po pobraniu", self.f_button,
                      "white", anchor="center")

    def draw_speed(self) -> None:
        c = self.c_speed
        w, h = self.card(c)
        p = self.px
        threshold = self.monitor.threshold
        speed = self.snapshot.speed if self.snapshot else None

        self.text(c, 22, 18, "PRĘDKOŚĆ POBIERANIA", self.f_label, FAINT)
        value = autostop.format_rate(speed) if speed is not None else "– KB/s"
        number, _, unit = value.partition(" ")
        self.text(c, 20, 32, number, self.f_huge, TEXT)
        nx = 20 + self.f_huge.measure(number) / self.scale + 6
        self.text(c, nx, 52, unit, self.f_unit, MUTED)

        active = speed is not None and speed >= threshold
        chip, chip_color = ("pobiera", GREEN) if active else ("cisza", MUTED)
        cw = self.f_small.measure(chip) / self.scale + 34
        self.round_rect(c, w - 20 - cw, 18, w - 20, 42, 12, fill=BG, outline=CARD_EDGE)
        c.create_oval(p(w - 20 - cw + 12), p(26), p(w - 20 - cw + 20), p(34),
                      fill=chip_color, outline="")
        self.text(c, w - 20 - cw + 26, 30, chip, self.f_small, MUTED, anchor="w")

        # Wykres ostatnich 2 minut
        x0, x1, y0, y1 = 22, w - 22, 100, h - 34
        data = list(self.history)
        top = max([threshold * 4, 1024 * 1024] + [v * 1.15 for v in data])

        def xy(i, v):
            x = x1 - (len(data) - 1 - i) * (x1 - x0) / (HISTORY_SECONDS - 1)
            return x, y1 - (y1 - y0) * min(v, top) / top

        c.create_line(p(x0), p(y1), p(x1), p(y1), fill=TRACK)
        if len(data) >= 2:
            points = [xy(i, v) for i, v in enumerate(data)]
            area = [points[0][0], y1] + [v for pt in points for v in pt] + [points[-1][0], y1]
            c.create_polygon([p(v) for v in area], fill=CHART_FILL, outline="")
            c.create_line([p(v) for pt in points for v in pt], fill=ACCENT, width=p(2),
                          joinstyle="round", capstyle="round")
            lx, ly = points[-1]
            c.create_oval(p(lx - 4), p(ly - 4), p(lx + 4), p(ly + 4), fill=ACCENT,
                          outline=CARD, width=p(2))
        # Próg: poniżej niego uznajemy, że nic się nie pobiera.
        ty = xy(0, threshold)[1]
        c.create_line(p(x0), p(ty), p(x1), p(ty), fill=MUTED, dash=(3, 4))
        label = self.text(c, x0 + 8, ty - 4, f"próg {autostop.format_rate(threshold)}",
                          self.f_small, MUTED, anchor="sw")
        bx1, by1, bx2, by2 = c.bbox(label)
        c.create_rectangle(bx1 - p(4), by1, bx2 + p(4), by2, fill=CARD, outline="")
        c.tag_raise(label)
        self.text(c, x0, y1 + 8, "2 min temu", self.f_small, FAINT)
        self.text(c, x1, y1 + 8, "teraz", self.f_small, FAINT, anchor="ne")

        # Podpowiedź po najechaniu myszką
        if self.hover_x is not None and len(data) >= 2:
            hx = self.hover_x / self.scale
            first_x = xy(0, 0)[0]
            if first_x - 6 <= hx <= x1 + 6:
                i = round((hx - first_x) / (x1 - first_x) * (len(data) - 1)) if x1 > first_x else 0
                i = min(max(i, 0), len(data) - 1)
                px_, py_ = xy(i, data[i])
                c.create_line(p(px_), p(y0 - 6), p(px_), p(y1), fill=MUTED)
                c.create_oval(p(px_ - 5), p(py_ - 5), p(px_ + 5), p(py_ + 5), fill=ACCENT,
                              outline=TEXT, width=p(2))
                ago = len(data) - 1 - i
                label = (f"{autostop.format_rate(data[i])}  ·  "
                         + (f"{ago} s temu" if ago else "teraz"))
                tw = self.f_small.measure(label) / self.scale + 20
                bx = min(max(px_ - tw / 2, x0), x1 - tw)
                self.round_rect(c, bx, y0 - 34, bx + tw, y0 - 10, 8, fill=BG, outline=CARD_EDGE)
                self.text(c, bx + tw / 2, y0 - 22, label, self.f_small, TEXT, anchor="center")

    def draw_queue(self) -> None:
        c = self.c_queue
        w, h = self.card(c)
        games = self.snapshot.games if self.snapshot else []

        self.text(c, 22, 18, "KOLEJKA STEAM / EPIC", self.f_label, FAINT)
        if games:
            label = polish_games(len(games))
            cw = self.f_small.measure(label) / self.scale + 20
            self.round_rect(c, w - 20 - cw, 12, w - 20, 34, 11, fill=BG, outline=CARD_EDGE)
            self.text(c, w - 20 - cw / 2, 23, label, self.f_small, MUTED, anchor="center")

        if not games:
            self.text(c, w / 2, h / 2 + 8,
                      "Nic nie czeka w kolejce.\nGOG, Battle.net, EA i Ubisoft wykrywam "
                      "po ruchu na dysku.", self.f_text, FAINT, anchor="center",
                      justify="center", width=w - 60)
            return

        rows = max(1, int((h - 50) // 46))
        for i, game in enumerate(games[:rows]):
            y = 48 + i * 46
            right = game.launcher
            right += f"  ·  {game.progress:.0%}" if game.progress is not None else "  ·  w kolejce"
            rw = self.f_small.measure(right) / self.scale
            self.text(c, w - 22, y, right, self.f_small, MUTED, anchor="ne")
            name = self.fit(game.name, self.f_text_bold, w - 44 - rw - 16)
            self.text(c, 22, y - 2, name, self.f_text_bold, TEXT)
            bar_y = y + 24
            self.round_rect(c, 22, bar_y, w - 22, bar_y + 6, 3, fill=TRACK, outline=TRACK)
            if game.progress is not None:
                end = 22 + (w - 44) * game.progress
                if game.progress > 0:
                    self.round_rect(c, 22, bar_y, max(end, 28), bar_y + 6, 3, fill=ACCENT,
                                    outline=ACCENT)
            else:  # nieznany postęp - przesuwający się pasek
                span = (w - 44) * 0.25
                start = 22 + ((self.tick * 0.08) % 1.0) * (w - 44 - span)
                self.round_rect(c, start, bar_y, start + span, bar_y + 6, 3, fill=ACCENT,
                                outline=ACCENT)
        if len(games) > rows:
            self.text(c, w - 22, h - 14, f"+{len(games) - rows} więcej", self.f_small, FAINT,
                      anchor="se")

    def draw_footer(self) -> None:
        c = self.c_footer
        c.delete("all")
        w = c.winfo_reqwidth() / self.scale
        watcher = self.monitor.watcher
        parts = [f"Steam: {'tak' if watcher.steam_root else 'nie znaleziono'}",
                 f"Epic: {'tak' if watcher.epic_dir else 'nie znaleziono'}",
                 "Launchery: " + (", ".join(self.launchers) or "żaden nie działa")]
        self.text(c, 2, 10, self.fit("   ·   ".join(parts), self.f_small, w - 200),
                  self.f_small, FAINT, anchor="w")
        self.text(c, w - 2, 10, f"odświeżanie co 1 s  ·  {time.strftime('%H:%M:%S')}",
                  self.f_small, FAINT, anchor="e")


def enable_sharp_text_on_windows() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def main() -> int:
    enable_sharp_text_on_windows()
    root = tk.Tk()
    if autostop.psutil is None:
        root.withdraw()
        messagebox.showerror("AutoStop", "Brakuje biblioteki psutil.\n\n"
                             "Zainstaluj ją poleceniem:\npy -m pip install psutil\n\n"
                             "albo uruchom start.bat - zrobi to sam.")
        return 1
    try:
        App(root)
    except Exception:
        root.withdraw()
        messagebox.showerror("AutoStop - błąd", traceback.format_exc())
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
