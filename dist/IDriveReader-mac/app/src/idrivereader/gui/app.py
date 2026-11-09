"""The IDriveReader main window. It only collects choices and shows progress; capture logic lives in
:mod:`idrivereader.capture.launch`."""
from __future__ import annotations

import ipaddress
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Callable, Optional

from .. import APP_NAME, __version__, host
from ..launch import capture_demo, capture_vehicle
from ..capture.context import SessionStatus
from ..capture.plan import CaptureOptions
from ..capture.service import CaptureResult
from . import system
from .bridge import DialogPrompt, ProgressView, UiDispatcher

_CHECKLIST = (
    "1) Laptopu prize takın; arabanın aküsüne şarj/destek cihazı bağlayın (oturum ~1 saat sürer).\n"
    "2) Telefondaki MHD uygulamasını ve MHD WiFi adaptörünü kapatın / çıkarın.\n"
    "3) Kontağı açın: frene BASMADAN START'a bir kez basın. Motoru çalıştırmayın.\n"
    "4) ENET kablosunu OBD portuna ve laptopun Ethernet girişine takın.\n"
    "5) 'Başlat'a basın. Program size ne zaman motoru çalıştıracağınızı soracak.")

_UI_FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica Neue"
_MONO_FONT = "Consolas" if sys.platform == "win32" else "Menlo"

CaptureJob = Callable[[ProgressView, DialogPrompt], Optional[CaptureResult]]


def format_duration(seconds: float) -> str:
    """'~1 sa 5 dk', '~12 dk', '~40 sn' — rounded, because it is an estimate."""
    if seconds < 60:
        return f"~{max(int(round(seconds / 10.0)) * 10, 10)} sn" if seconds > 0 else "bitti"
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        return f"~{minutes} dk"
    return f"~{minutes // 60} sa {minutes % 60} dk"


class MainWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._dispatcher = UiDispatcher()
        self._cancel_requested = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._output_root = host.resolve_output_root()
        self._last_folder: Optional[Path] = None
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._pump()

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        self.root.title(f"{APP_NAME} {__version__} - BMW salt okuma kaydi")
        self.root.minsize(820, 720)
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text=f"{APP_NAME} {__version__}", font=(_UI_FONT, 16, "bold")).pack(anchor="w")
        ttk.Label(main, text="SALT OKUMA: araca hiçbir yazma, silme veya kodlama komutu gönderilmez.",
                  foreground="#1a7f37", font=(_UI_FONT, 10, "bold")).pack(anchor="w", pady=(0, 8))

        prep = ttk.LabelFrame(main, text="Hazırlık", padding=8)
        prep.pack(fill="x")
        ttk.Label(prep, text=_CHECKLIST, justify="left").pack(anchor="w")

        self._build_options(main)

        buttons = ttk.Frame(main)
        buttons.pack(fill="x")
        self._start_button = ttk.Button(buttons, text="Başlat", command=self._start_capture)
        self._start_button.pack(side="left")
        self._stop_button = ttk.Button(buttons, text="Durdur", command=self._stop, state="disabled")
        self._stop_button.pack(side="left", padx=6)
        self._demo_button = ttk.Button(buttons, text="Deneme modu (sahte araç)", command=self._start_demo)
        self._demo_button.pack(side="left")
        ttk.Button(buttons, text="Kayıt klasörünü aç", command=self._open_output).pack(side="right")

        status = ttk.LabelFrame(main, text="Durum", padding=8)
        status.pack(fill="both", expand=True, pady=(8, 0))
        self._overall_label = ttk.Label(status, text="Genel ilerleme: -", font=(_UI_FONT, 12, "bold"))
        self._overall_label.pack(anchor="w")
        self._overall_bar = ttk.Progressbar(status, maximum=100.0)
        self._overall_bar.pack(fill="x", pady=(2, 8))
        self._step_label = ttk.Label(status, text="Hazır.", font=(_UI_FONT, 10, "bold"))
        self._step_label.pack(anchor="w")
        self._bar = ttk.Progressbar(status, maximum=1.0)
        self._bar.pack(fill="x", pady=4)
        self._detail_label = ttk.Label(status, text="")
        self._detail_label.pack(anchor="w")
        self._log = ScrolledText(status, height=14, font=(_MONO_FONT, 9 if sys.platform == "win32" else 11), state="disabled")
        self._log.pack(fill="both", expand=True, pady=(4, 0))
        self.append_log(f"Kayıtlar buraya yazılacak: {self._output_root}")

    def _build_options(self, parent: ttk.Frame) -> None:
        options = ttk.LabelFrame(parent, text="Okunacaklar", padding=8)
        options.pack(fill="x", pady=8)
        ttk.Label(options, text="Her zaman: VIN, modül listesi, yazılım sürümleri, arıza kodları, "
                                "head unit coding yedeği.").grid(row=0, column=0, columnspan=4, sticky="w")
        self._scan_dme = tk.BooleanVar(value=True)
        self._scan_head_unit = tk.BooleanVar(value=True)
        self._scan_gateway = tk.BooleanVar(value=True)
        self._record_live = tk.BooleanVar(value=True)
        self._live_minutes = tk.IntVar(value=1)
        self._manual_ip = tk.StringVar()
        rows = [("DME tam DID taraması (uzun sürer, kalan süre ekranda)", self._scan_dme),
                ("Head unit tam DID taraması (uzun sürer, kalan süre ekranda)", self._scan_head_unit),
                ("Gateway (ZGM) tam DID taraması (uzun sürer, kalan süre ekranda)", self._scan_gateway),
                ("Motor çalışırken canlı kayıt (DME taraması gerekir), dakika:", self._record_live)]
        for row, (text, variable) in enumerate(rows, start=1):
            ttk.Checkbutton(options, text=text, variable=variable).grid(row=row, column=0, sticky="w")
        ttk.Spinbox(options, from_=1, to=20, width=4, textvariable=self._live_minutes).grid(row=4, column=1, sticky="w")
        ttk.Label(options, text="Araç IP (boş bırakın, otomatik bulunur):").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(options, textvariable=self._manual_ip, width=18).grid(row=5, column=1, sticky="w", pady=(6, 0))

    # ---------------------------------------------------------- view updates
    def show_step(self, title: str) -> None:
        self._step_label.config(text=title)
        self._bar.config(value=0)
        self._detail_label.config(text="")
        self.append_log(f"=== {title}")

    def show_progress(self, fraction: float, detail: str) -> None:
        self._bar.config(value=fraction)
        self._detail_label.config(text=f"Bu adım: %{fraction * 100:.0f}  ·  {detail}")

    def show_session(self, status: SessionStatus) -> None:
        self._overall_bar.config(value=status.percent)
        self._overall_label.config(
            text=f"Genel ilerleme: %{status.percent:.0f}  ·  Adım {status.step_number}/{status.step_count}"
                 f"  ·  Tahmini kalan: {format_duration(status.remaining_seconds)}")
        self.root.title(f"%{status.percent:.0f} - {APP_NAME} {__version__}")

    def append_log(self, message: str) -> None:
        self._log.config(state="normal")
        self._log.insert("end", message + "\n")
        self._log.see("end")
        self._log.config(state="disabled")

    # --------------------------------------------------------------- actions
    def _chosen_options(self) -> CaptureOptions:
        try:
            minutes = max(1, min(20, int(self._live_minutes.get())))
        except (tk.TclError, ValueError):
            minutes = 1
        return CaptureOptions(scan_dme=self._scan_dme.get(), scan_head_unit=self._scan_head_unit.get(),
                              scan_gateway=self._scan_gateway.get(),
                              live_seconds=minutes * 60 if self._record_live.get() else 0)

    def _start_capture(self) -> None:
        manual_ip = self._manual_ip.get().strip() or None
        if manual_ip:
            try:
                ipaddress.IPv4Address(manual_ip)
            except ValueError:
                messagebox.showerror("Geçersiz IP", f"'{manual_ip}' geçerli bir IPv4 adresi değil.")
                return
        options = self._chosen_options()
        self._run_in_background(lambda progress, prompt: capture_vehicle(
            self._output_root, options, manual_ip=manual_ip, progress=progress, operator=prompt,
            cancel_requested=self._cancel_requested))

    def _start_demo(self) -> None:
        options = self._chosen_options()
        self._run_in_background(lambda progress, prompt: capture_demo(
            self._output_root, options, progress=progress, operator=prompt,
            cancel_requested=self._cancel_requested))

    def _stop(self) -> None:
        self._cancel_requested.set()
        self.append_log("Durduruluyor... (o ana kadar okunanlar kaydedilecek)")

    def _open_output(self) -> None:
        system.open_folder(self._last_folder or self._output_root)

    # -------------------------------------------------------------- workers
    def _run_in_background(self, job: CaptureJob) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._cancel_requested.clear()
        self._set_running(True)
        progress = ProgressView(self._dispatcher, self)
        prompt = DialogPrompt(self._dispatcher, self.root)

        def work() -> None:
            result: Optional[CaptureResult] = None
            try:
                with host.prevent_sleep():
                    result = job(progress, prompt)
            except Exception as error:  # surface any failure instead of dying silently
                progress.log(f"HATA: {error!r}")
            self._dispatcher.post(lambda: self._finished(result))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def _finished(self, result: Optional[CaptureResult]) -> None:
        self._set_running(False)
        self.root.title(f"{APP_NAME} {__version__} - BMW salt okuma kaydi")
        if result is None:
            self._step_label.config(text="Okuma yapılmadı.")
            self._overall_label.config(text="Genel ilerleme: -")
            return
        self._last_folder = result.folder
        problems = [f"- {r.title}: {r.outcome.summary}" for r in result.results if not r.outcome.is_ok]
        self._step_label.config(text="Bitti.")
        self._bar.config(value=1.0)
        self._overall_bar.config(value=100.0)
        self._overall_label.config(text="Genel ilerleme: %100  ·  Bitti")
        self.append_log(f"Kayıt klasörü: {result.folder}")
        self.append_log(f"Zip arşivi  : {result.archive or 'oluşturulamadı (klasör eksiksiz)'}")
        self.append_log(f"Kaydedilen ham çerçeve: {result.frame_count}")
        summary = "Tüm adımlar tamamlandı." if not problems else "Tamamlanmayan adımlar:\n" + "\n".join(problems)
        to_copy = (f"'{result.archive.name}' dosyasını" if result.archive
                   else f"'{result.folder.name}' klasörünün tamamını")
        messagebox.showinfo("Okuma bitti", f"{summary}\n\nKlasör:\n{result.folder}\n\n"
                                           f"Bilgisayarınıza {to_copy} kopyalayın.",
                            parent=self.root)

    def _set_running(self, running: bool) -> None:
        for button in (self._start_button, self._demo_button):
            button.config(state="disabled" if running else "normal")
        self._stop_button.config(state="normal" if running else "disabled")

    def _pump(self) -> None:
        self._dispatcher.run_pending()
        self.root.after(100, self._pump)

    def _on_close(self) -> None:
        if self._worker and self._worker.is_alive():
            if not messagebox.askyesno("Okuma sürüyor", "Okuma devam ediyor. Durdurup çıkmak istiyor musunuz?\n"
                                                         "(O ana kadar okunanlar kaydedilir.)", parent=self.root):
                return
            self._cancel_requested.set()
            self._close_when_idle()
            return
        self.root.destroy()

    def _close_when_idle(self) -> None:
        if self._worker and self._worker.is_alive():
            self.root.after(200, self._close_when_idle)
            return
        self.root.destroy()


def run() -> None:
    system.enable_high_dpi()
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista" if sys.platform == "win32" else "aqua")
    except tk.TclError:
        pass
    MainWindow(root)
    root.mainloop()
