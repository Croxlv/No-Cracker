"""
AuthMe SHA256 Hash Kırıcı — Sıfırdan Tasarım
Mimari:
  - HashcatRunner  : hashcat sürecini yönetir (GUI'den bağımsız)
  - PasswordSaver  : kırılan şifreleri passwords.txt'e yazar
  - AuthMeGui      : arayüz (sadece görsel işlemler)
"""

import os
import sys
import sqlite3
import threading
import subprocess
import ctypes
import tkinter as tk
import hashlib
import random
import string
import requests
from tkinter import filedialog, messagebox, scrolledtext, ttk

# ──────────────────────────────────────────────────────────────────
# YAPILANDIRMA
# ──────────────────────────────────────────────────────────────────
SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
PASSWORDS_FILE  = os.path.join(SCRIPT_DIR, "passwords.txt")
WORK_DIR        = r"C:\tmp\hashcat_work"

DEFAULT_HASHCAT = r""

AUTO_DB_FILES = [
    r"",
    r"",
    r"",
]

# ──────────────────────────────────────────────────────────────────
# RENK PALETİ
# ──────────────────────────────────────────────────────────────────
BG       = "#0d0d0d"
SURFACE  = "#141414"
CARD     = "#1a1a1a"
BORDER   = "#2a2a2a"
ACCENT   = "#f97316"   # turuncu
ACCENT2  = "#fb923c"
DIM      = "#6b7280"
TEXT     = "#f3f4f6"
TEXTDIM  = "#9ca3af"
GREEN    = "#22c55e"
RED      = "#ef4444"
TERM_BG  = "#0a0a0a"
TERM_FG  = "#f97316"

FONT_UI   = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_MONO = ("Consolas",  9)
FONT_HEAD = ("Segoe UI", 15, "bold")
FONT_SUB  = ("Segoe UI",  8)


# ══════════════════════════════════════════════════════════════════
# BACKEND — PasswordSaver
# ══════════════════════════════════════════════════════════════════
class PasswordSaver:
    """Kırılan şifreleri passwords.txt'e kayıt eder."""

    @staticmethod
    def save(username: str, password: str, hash_val: str = ""):
        try:
            with open(PASSWORDS_FILE, "a", encoding="utf-8") as f:
                ts = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                line = f"[{ts}]  Kullanıcı: {username or 'bilinmiyor':20s}  |  Şifre: {password}"
                if hash_val:
                    line += f"  |  Hash: {hash_val[:40]}..."
                f.write(line + "\n")
            return True
        except Exception as e:
            print(f"[PasswordSaver] Yazma hatası: {e}")
            return False

    @staticmethod
    def read_all() -> str:
        try:
            if os.path.exists(PASSWORDS_FILE):
                with open(PASSWORDS_FILE, encoding="utf-8") as f:
                    return f.read()
        except Exception:
            pass
        return ""


# ══════════════════════════════════════════════════════════════════
# BACKEND — DB Okuyucu
# ══════════════════════════════════════════════════════════════════
def read_db(db_path: str) -> list[tuple[str, str]]:
    """AuthMe SQLite DB'den (kullanıcı, hash) çiftlerini döndürür."""
    results = []
    try:
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        for (table,) in cur.fetchall():
            try:
                cur.execute(
                    f"SELECT username, password FROM [{table}]"
                    f" WHERE password IS NOT NULL AND password != ''"
                )
                for username, pwd in cur.fetchall():
                    if isinstance(pwd, str) and pwd.startswith("$SHA$"):
                        results.append((username, pwd))
            except Exception:
                pass
        con.close()
    except Exception as e:
        print(f"[DB] {db_path}: {e}")
    return results


# ══════════════════════════════════════════════════════════════════
# BACKEND — Araçlar (Hash-Killer Mantığı)
# ══════════════════════════════════════════════════════════════════
class ToolsLogic:
    """Hash oluşturma ve Sözlük indirme işlemleri."""

    @staticmethod
    def generate_authme_sha256(password: str) -> str:
        """AuthMe formatında ($SHA$salt$hash) SHA256 oluşturur."""
        salt = ''.join(random.choices(string.hexdigest, k=16)).lower()
        hash_part = hashlib.sha256(hashlib.sha256(password.encode()).hexdigest().encode() + salt.encode()).hexdigest()
        return f"$SHA${salt}${hash_part}"

    @staticmethod
    def download_file(url: str, dest_path: str, progress_cb=None):
        """URL'den dosya indirir."""
        try:
            response = requests.get(url, stream=True, timeout=15)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            
            with open(dest_path, 'wb') as f:
                if total_size == 0:
                    f.write(response.content)
                else:
                    downloaded = 0
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb:
                                progress_cb(downloaded, total_size)
            return True, "İndirme başarılı."
        except Exception as e:
            return False, str(e)


# ══════════════════════════════════════════════════════════════════
# BACKEND — HashcatRunner
# ══════════════════════════════════════════════════════════════════
class HashcatRunner:
    """
    Hashcat sürecini çalıştırır.
    GUI tamamen ayrı — sadece callback'ler ile iletişim kurar.
    """

    def __init__(self, hashcat_exe: str, log_cb, done_cb):
        """
        log_cb(text)           : her satır için çağrılır
        done_cb(passwords)     : bitti; passwords = [(user, pwd, hash), ...]
        """
        self.exe    = hashcat_exe
        self.log    = log_cb
        self.done   = done_cb
        self._proc: subprocess.Popen | None = None

    # ── Yardımcılar ──────────────────────────────────────────────
    @staticmethod
    def _short_path(path: str) -> str:
        """Windows 8.3 kısa yola çevirir (Türkçe/boşluklu yollar için)."""
        try:
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.kernel32.GetShortPathNameW(path, buf, 512)
            return buf.value or path
        except Exception:
            return path

    @staticmethod
    def _kill_existing():
        """Arka planda kalmış hashcat süreçlerini sonlandırır."""
        try:
            subprocess.run(
                ["taskkill", "/f", "/im", "hashcat.exe"],
                capture_output=True, timeout=5
            )
        except Exception:
            pass

    def _build_cmd(self, mode: str, hash_file: str, out_file: str,
                   dict_file: str = "", mask: str = "",
                   cpu_only: bool = False) -> list[str]:
        """Hashcat komut listesini oluşturur."""
        exe  = self._short_path(self.exe)
        hf   = self._short_path(hash_file)
        of   = self._short_path(out_file)
        cwd  = os.path.dirname(os.path.abspath(self.exe))
        # NOT: -D 1 (CPU) çoğu sistemde CPU OpenCL runtime gerektiriyor,
        # kurulu değilse 'No devices found' hatası verir.
        # Bu nedenle her zaman GPU (-D 2) kullanıyoruz.
        # CPU Only kutusu sadece bilgi amaçlıdır.
        d_hw = ["-D", "2"]

        base = [exe,
                "--force",
                "--potfile-disable",
                "-O",                        # optimize kernel (hız)
                "-m", "20711",               # AuthMe SHA256
                "--status",                  # ilerleme bilgisi göster
                "--status-timer", "15",      # her 15 saniyede bir
                "--outfile-format", "2",     # sadece düz şifre çıktısı
                "--outfile", of]

        if mode == "dict":
            df = self._short_path(dict_file)
            cmd = base + ["-a", "0"] + d_hw + [hf, df]
            
            # Hashcat klasöründe best64.rule varsa ekle (daha etkili saldırı için)
            rule_path = os.path.join(cwd, "rules", "best64.rule")
            if os.path.exists(rule_path):
                cmd.insert(len(base), "-r")
                cmd.insert(len(base)+1, self._short_path(rule_path))
            
            return cmd
        else:
            return base + ["-a", "3"] + d_hw + [hf, mask]

    # ── Ana çalıştırıcı ──────────────────────────────────────────
    def run(self, mode: str, hashes: list[tuple[str, str]],
            dict_file: str = "", mask: str = "", cpu_only: bool = False):
        """
        Thread içinde çağrılır.
        hashes: [(username, hash_val), ...]
        """
        os.makedirs(WORK_DIR, exist_ok=True)

        # Geçici hash dosyası
        tmp_hash = os.path.join(WORK_DIR, "_hashes.txt")
        out_file = os.path.join(WORK_DIR, "_cracked.txt")

        # Eski çıktı dosyasını temizle
        for f in [tmp_hash, out_file]:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass

        # Hash → kullanıcı adı eşlemesi
        hash_to_user: dict[str, str] = {}
        with open(tmp_hash, "w", encoding="utf-8") as f:
            for username, hval in hashes:
                f.write(hval + "\n")
                if hval not in hash_to_user:
                    hash_to_user[hval] = username

        cmd = self._build_cmd(mode, tmp_hash, out_file, dict_file, mask, cpu_only)

        self.log(f"[KOMUT] {' '.join(cmd)}\n")

        # Eski süreci öldür
        self._kill_existing()

        # hashcat.exe'nin kendi klasöründen çalıştır (OpenCL/ için)
        cwd = os.path.dirname(os.path.abspath(self.exe))

        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            for line in self._proc.stdout:
                s = line.strip()
                if s:
                    self.log(s)

            self._proc.wait()
            rc = self._proc.returncode
            self.log(f"\n[BİTTİ] Çıkış kodu: {rc}")

        except FileNotFoundError:
            self.log(f"[HATA] hashcat.exe bulunamadı: {self.exe}")
            self.done([])
            return
        except Exception as e:
            self.log(f"[HATA] {e}")
            self.done([])
            return
        finally:
            try:
                os.remove(tmp_hash)
            except Exception:
                pass

        # Sonuçları oku
        found: list[tuple[str, str, str]] = []
        if os.path.exists(out_file):
            with open(out_file, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    pwd = line.strip()
                    if pwd:
                        # outfile-format 2 → sadece şifre; hangisi kırıldı bilmiyoruz
                        # Tek hash ise direkt eşleştir; birden fazlaysa "bilinmiyor"
                        if len(hash_to_user) == 1:
                            user = next(iter(hash_to_user.values()))
                            hval = next(iter(hash_to_user.keys()))
                        else:
                            user = "bilinmiyor"
                            hval = ""
                        found.append((user, pwd, hval))
            try:
                os.remove(out_file)
            except Exception:
                pass

        self.done(found)

    def run_info(self):
        """Donanım bilgisi sorgular (-I)."""
        cwd = os.path.dirname(os.path.abspath(self.exe))
        self._kill_existing()
        try:
            proc = subprocess.Popen(
                [self.exe, "-I", "--force"],
                cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace"
            )
            for line in proc.stdout:
                s = line.strip()
                if s:
                    self.log(s)
            proc.wait()
        except Exception as e:
            self.log(f"[HATA] {e}")


# ══════════════════════════════════════════════════════════════════
# GUI — Özel Widget'lar
# ══════════════════════════════════════════════════════════════════
class FlatButton(tk.Canvas):
    """Sade, köşe yuvarlak buton."""

    def __init__(self, parent, text, command=None,
                 w=180, h=36, color=ACCENT, text_color="#000",
                 font=FONT_BOLD, **kw):
        super().__init__(parent, width=w, height=h,
                         bg=parent.cget("bg"), highlightthickness=0, **kw)
        self._cmd   = command
        self._text  = text
        self._color = color
        self._hover = self._lighten(color, 30)
        self._tc    = text_color
        self._font  = font
        self._btn_w, self._btn_h = w, h
        self._disabled = False
        self._draw(self._color)
        self.bind("<Enter>",          lambda e: self._on_enter())
        self.bind("<Leave>",          lambda e: self._on_leave())
        self.bind("<ButtonRelease-1>",lambda e: self._on_click())

    @staticmethod
    def _lighten(hex_col: str, amt: int) -> str:
        r = min(255, int(hex_col[1:3], 16) + amt)
        g = min(255, int(hex_col[3:5], 16) + amt)
        b = min(255, int(hex_col[5:7], 16) + amt)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _rr(self, x1, y1, x2, y2, r, **kw):
        pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r,
               x2,y2-r, x2,y2, x2-r,y2, x1+r,y2,
               x1,y2, x1,y2-r, x1,y1+r, x1,y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def _draw(self, bg):
        self.delete("all")
        self._rr(0, 0, self._btn_w-1, self._btn_h-1, 8, fill=bg, outline="")
        self.create_text(self._btn_w//2, self._btn_h//2,
                         text=self._text, fill=self._tc,
                         font=self._font)

    def _on_enter(self):
        if not self._disabled:
            self._draw(self._hover)
            self.config(cursor="hand2")

    def _on_leave(self):
        if not self._disabled:
            self._draw(self._color)

    def _on_click(self):
        if not self._disabled and self._cmd:
            self._cmd()

    def set_state(self, enabled: bool):
        self._disabled = not enabled
        self._draw(BORDER if not enabled else self._color)
        self.config(cursor="" if not enabled else "hand2")


class Entry(tk.Frame):
    """Kenarlıklı giriş alanı."""

    def __init__(self, parent, width=50, default="", show="", **kw):
        super().__init__(parent, bg=BORDER, padx=1, pady=1)
        self._e = tk.Entry(self, width=width, bg=SURFACE, fg=TEXT,
                           insertbackground=ACCENT, relief="flat",
                           font=FONT_MONO, bd=0,
                           selectbackground=ACCENT,
                           selectforeground="#000",
                           show=show,
                           highlightthickness=0)
        self._e.pack(ipady=5, ipadx=6, fill=tk.X)
        if default:
            self._e.insert(0, default)
        self._e.bind("<FocusIn>",  lambda e: self.config(bg=ACCENT))
        self._e.bind("<FocusOut>", lambda e: self.config(bg=BORDER))

    def get(self) -> str:         return self._e.get()
    def set(self, val: str):      self._e.delete(0, tk.END); self._e.insert(0, val)
    def clear(self):              self._e.delete(0, tk.END)


def sep(parent, color=BORDER, h=1, pady=6):
    """Yatay ayraç çizgisi."""
    tk.Frame(parent, bg=color, height=h).pack(fill=tk.X, padx=16, pady=pady)


def label(parent, text, font=FONT_UI, fg=TEXTDIM, **kw):
    # Eğer kw içinde 'bg' varsa onu kullan, yoksa parent'ın bg'sini al
    bg = kw.pop('bg', parent.cget("bg"))
    return tk.Label(parent, text=text, font=font, bg=bg, fg=fg, **kw)


def card_frame(parent, title=""):
    """Başlıklı kart çerçevesi."""
    outer = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
    outer.pack(fill=tk.X, padx=16, pady=4)
    inner = tk.Frame(outer, bg=CARD)
    inner.pack(fill=tk.X)
    if title:
        hdr = tk.Frame(inner, bg=SURFACE)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text=f"  {title}", font=FONT_BOLD,
                 bg=SURFACE, fg=ACCENT, anchor="w", pady=6).pack(fill=tk.X)
        tk.Frame(inner, bg=BORDER, height=1).pack(fill=tk.X)
    return inner


# ══════════════════════════════════════════════════════════════════
# GUI — Ana Uygulama
# ══════════════════════════════════════════════════════════════════
class AuthMeGui:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AuthMe SHA256 Kırıcı  v2")
        self.root.geometry("800x900")
        self.root.config(bg=BG)
        self.root.resizable(False, True)

        # Durum
        self._running = False

        # Tab Sistemi (Notebook)
        style = ttk.Style()
        style.theme_use('default')
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=SURFACE, foreground=TEXTDIM, padding=[15, 5], font=FONT_BOLD)
        style.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", "#000")])

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Sekme 1: Ana Ekran
        self.tab_main = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_main, text="  🏠  Ana Ekran  ")

        # Sekme 2: Araçlar
        self.tab_tools = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_tools, text="  🛠️  Araçlar  ")

        # Ana Scrollable (Sekme 1 için)
        canvas = tk.Canvas(self.tab_main, bg=BG, highlightthickness=0)
        vsb = tk.Scrollbar(self.tab_main, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._main = tk.Frame(canvas, bg=BG)
        self._win  = canvas.create_window((0, 0), window=self._main, anchor="nw")

        self._main.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
            lambda e: canvas.itemconfig(self._win, width=e.width))
        canvas.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        self._build_ui()
        self._build_tools_ui()

    # ── Araçlar UI ────────────────────────────────────────────────
    def _build_tools_ui(self):
        p = self.tab_tools
        
        # Başlık
        tk.Frame(p, bg=ACCENT, height=3).pack(fill=tk.X)
        hf = tk.Frame(p, bg=BG)
        hf.pack(fill=tk.X, pady=(14, 6))
        label(hf, "🛠️  Ek Araçlar", font=FONT_HEAD, fg=ACCENT).pack()
        label(hf, "Sözlük İndirme ve Hash Üretme", fg=DIM, font=FONT_SUB).pack()
        tk.Frame(p, bg=BORDER, height=1).pack(fill=tk.X, padx=16, pady=8)

        # KART: Hash Oluşturucu
        c_gen = card_frame(p, "🔑  AuthMe SHA256 Hash Oluşturucu")
        rg = tk.Frame(c_gen, bg=CARD)
        rg.pack(fill=tk.X, padx=10, pady=8)
        
        label(rg, "Şifre Girin:", font=FONT_UI, fg=TEXTDIM, bg=CARD).pack(anchor="w")
        self.w_gen_pwd = Entry(rg, width=60, default="123456")
        self.w_gen_pwd.pack(fill=tk.X, pady=(2, 8))
        
        self.w_gen_res = Entry(rg, width=60, default="Sonuç burada görünecek...")
        self.w_gen_res.pack(fill=tk.X, pady=(2, 8))

        FlatButton(rg, "GÖSTER VE KOPYALA 📋", 
                   command=self._tool_gen_hash,
                   w=220, h=36, color=ACCENT, text_color="#000").pack(pady=5)

        # KART: Sözlük İndirici
        c_dl = card_frame(p, "🌐  Sözlük (Wordlist) İndirici")
        rd = tk.Frame(c_dl, bg=CARD)
        rd.pack(fill=tk.X, padx=10, pady=8)
        
        label(rd, "URL (Direkt Link):", font=FONT_UI, fg=TEXTDIM, bg=CARD).pack(anchor="w")
        self.w_dl_url = Entry(rd, width=60, default="https://raw.githubusercontent.com/danielmiessler/SecLists/master/Passwords/Common-Credentials/10-million-password-list-top-1000.txt")
        self.w_dl_url.pack(fill=tk.X, pady=(2, 8))

        label(rd, "Kaydedilecek İsim:", font=FONT_UI, fg=TEXTDIM, bg=CARD).pack(anchor="w")
        self.w_dl_name = Entry(rd, width=30, default="indirilen_sozluk.txt")
        self.w_dl_name.pack(anchor="w", pady=(2, 8))

        self.w_dl_prog = label(rd, "Hazır.", fg=DIM)
        self.w_dl_prog.pack(pady=5)

        FlatButton(rd, "İNDİRMEYİ BAŞLAT 📥", 
                   command=self._tool_download,
                   w=200, h=36, color=GREEN, text_color="#000").pack(pady=5)

    def _tool_gen_hash(self):
        pwd = self.w_gen_pwd.get().strip()
        if not pwd: return
        h = ToolsLogic.generate_authme_sha256(pwd)
        self.w_gen_res.set(h)
        self.root.clipboard_clear()
        self.root.clipboard_append(h)
        self._log(f"[ARAÇ] Hash oluşturuldu ve kopyalandı: {h}")

    def _tool_download(self):
        url = self.w_dl_url.get().strip()
        name = self.w_dl_name.get().strip()
        if not url or not name: return
        
        dest = os.path.join(SCRIPT_DIR, name)
        self.w_dl_prog.config(text="İndiriliyor...", fg=ACCENT)
        self._log(f"[ARAÇ] İndirme başlatıldı: {url}")

        def worker():
            def progress(current, total):
                if total > 0:
                    pct = int(current / total * 100)
                    self.root.after(0, lambda: self.w_dl_prog.config(text=f"İndiriliyor: %{pct}"))
            
            ok, msg = ToolsLogic.download_file(url, dest, progress)
            if ok:
                self.root.after(0, lambda: (
                    self.w_dl_prog.config(text="✓ Tamamlandı.", fg=GREEN),
                    messagebox.showinfo("Başarılı", f"Dosya indirildi:\n{dest}")
                ))
                self._log(f"[ARAÇ] İndirme tamamlandı: {dest}")
            else:
                self.root.after(0, lambda: (
                    self.w_dl_prog.config(text="X Hata!", fg=RED),
                    messagebox.showerror("Hata", f"İndirme başarısız:\n{msg}")
                ))
                self._log(f"[ARAÇ] İndirme hatası: {msg}")

        threading.Thread(target=worker, daemon=True).start()

    # ── UI kurulum ────────────────────────────────────────────────
    def _build_ui(self):
        p = self._main   # kısaltma

        # ── BAŞLIK ──
        tk.Frame(p, bg=ACCENT, height=3).pack(fill=tk.X)
        hf = tk.Frame(p, bg=BG)
        hf.pack(fill=tk.X, pady=(14, 6))
        label(hf, "🔓  AuthMe SHA256 Kırıcı",
              font=FONT_HEAD, fg=ACCENT).pack()
        label(hf, "Hashcat Destekli  ·  Mode 20711  ·  GPU/CPU",
              fg=DIM, font=FONT_SUB).pack()
        tk.Frame(p, bg=BORDER, height=1).pack(fill=tk.X, padx=16, pady=8)

        # ── KART 1 : Hashcat yolu ──
        c1 = card_frame(p, "⚙  Hashcat.exe Yolu")
        r1 = tk.Frame(c1, bg=CARD)
        r1.pack(fill=tk.X, padx=10, pady=8)
        self.w_hc_path = Entry(r1, width=62, default=DEFAULT_HASHCAT)
        self.w_hc_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,6))
        FlatButton(r1, "Seç 📁", command=self._browse_hc,
                   w=70, h=30, color=SURFACE, text_color=TEXTDIM).pack(side=tk.LEFT, padx=(0,4))
        FlatButton(r1, "Donanım 🔍", command=self._hw_info,
                   w=90, h=30, color=SURFACE, text_color=TEXTDIM).pack(side=tk.LEFT)

        # ── KART 2 : Saldırı modu ──
        c2 = card_frame(p, "🎯  Saldırı Modu")
        r2 = tk.Frame(c2, bg=CARD)
        r2.pack(pady=(8,4))

        self.w_mode = tk.StringVar(value="dict")

        # Radio butonlar — tanımla VE pack et aynı anda
        for val, txt in [("dict", "📖  Sözlük Saldırısı"), ("brute", "🔢  Brute Force")]:
            tk.Radiobutton(r2, text=txt, variable=self.w_mode, value=val,
                           command=self._toggle_mode,
                           bg=CARD, fg=TEXT, selectcolor=BG,
                           activebackground=CARD, activeforeground=ACCENT,
                           font=FONT_BOLD, cursor="hand2"
                           ).pack(side=tk.LEFT, padx=20)

        self.w_cpu = tk.BooleanVar(value=False)
        tk.Checkbutton(r2, text="CPU Only", variable=self.w_cpu,
                       bg=CARD, fg=TEXTDIM, selectcolor=BG,
                       activebackground=CARD, activeforeground=ACCENT,
                       font=FONT_UI, cursor="hand2"
                       ).pack(side=tk.LEFT, padx=20)

        # ── KART 3A : Sözlük ──
        self._dict_outer = tk.Frame(p, bg=BG)
        self._dict_outer.pack(fill=tk.X)
        cd = card_frame(self._dict_outer, "📄  Sözlük Dosyası (.txt)")
        rd = tk.Frame(cd, bg=CARD)
        rd.pack(fill=tk.X, padx=10, pady=8)
        self.w_dict = Entry(rd, width=60)
        self.w_dict.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,6))
        FlatButton(rd, "Seç 📁", command=self._browse_dict,
                   w=70, h=30, color=SURFACE, text_color=TEXTDIM).pack(side=tk.LEFT)

        # ── KART 3B : Brute Force maskesi (başlangıçta gizli) ──
        self._brute_outer = tk.Frame(p, bg=BG)
        cb = card_frame(self._brute_outer, "🔢  Brute Force Maskesi")
        rb = tk.Frame(cb, bg=CARD)
        rb.pack(fill=tk.X, padx=10, pady=8)
        self.w_mask = Entry(rb, width=30, default="?l?d?l?d?l?d")
        self.w_mask.pack(side=tk.LEFT, padx=(0,12))
        tk.Label(rb, text="?a=Tüm  ?l=Küçük  ?u=Büyük  ?d=Rakam",
                 font=("Segoe UI", 8), bg=CARD, fg=DIM).pack(side=tk.LEFT)

        sep(p)

        # ── KART 4 : Otomatik mod ──
        c_auto = card_frame(p, "⚡  Otomatik Mod  —  DB Dosyalarını Tara")
        for db in AUTO_DB_FILES:
            r = tk.Frame(c_auto, bg=CARD)
            r.pack(fill=tk.X, padx=10, pady=1)
            tk.Label(r, text="◆ ", fg=ACCENT, bg=CARD, font=("Segoe UI",7)).pack(side=tk.LEFT)
            exists = os.path.isfile(db)
            tk.Label(r, text=os.path.basename(db),
                     fg=TEXT if exists else RED,
                     bg=CARD, font=FONT_MONO).pack(side=tk.LEFT)
            tk.Label(r, text="  ✓" if exists else "  ✗ bulunamadı",
                     fg=GREEN if exists else RED,
                     bg=CARD, font=("Segoe UI",8)).pack(side=tk.LEFT)

        ba = tk.Frame(c_auto, bg=CARD)
        ba.pack(pady=10)
        self.w_auto_btn = FlatButton(
            ba, "▶  OTOMATIK TARA VE KIRMAYA BAŞLA",
            command=self._start_auto,
            w=360, h=42, color=GREEN, text_color="#000",
            font=("Segoe UI", 11, "bold"))
        self.w_auto_btn.pack()

        sep(p)

        # ── KART 5 : Manuel hash ──
        c_man = card_frame(p, "✏  Manuel Hash Kırma")
        rm = tk.Frame(c_man, bg=CARD)
        rm.pack(fill=tk.X, padx=10, pady=(8,4))

        # Kullanıcı adı satırı
        ru = tk.Frame(rm, bg=CARD)
        ru.pack(fill=tk.X, pady=(0,6))
        tk.Label(ru, text="Kullanıcı adı (isteğe bağlı):",
                 font=FONT_UI, fg=TEXTDIM, bg=CARD).pack(side=tk.LEFT, padx=(0,8))
        self.w_username = Entry(ru, width=28)
        self.w_username.pack(side=tk.LEFT)

        # Hash satırı
        tk.Label(rm, text="Hash:", font=FONT_UI, fg=TEXTDIM, bg=CARD,
                 anchor="w").pack(fill=tk.X)
        self.w_hash = Entry(rm, width=72,
            default="$SHA$3989e11a4e38e9fb$7c5370696d20750ae520e820706f29bd05aacaa5c2fc03befc90d0e37d32e5e9")
        self.w_hash.pack(fill=tk.X, pady=(2,8))

        bm = tk.Frame(c_man, bg=CARD)
        bm.pack(pady=(0,10))
        self.w_man_btn = FlatButton(
            bm, "🔨  HASHCAT İLE KIR",
            command=self._start_manual,
            w=230, h=40, color=ACCENT, text_color="#000",
            font=("Segoe UI", 11, "bold"))
        self.w_man_btn.pack()

        sep(p)

        # ── KART 6 : Log ──
        lf = tk.Frame(p, bg=BORDER, padx=1, pady=1)
        lf.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0,4))
        lh = tk.Frame(lf, bg=SURFACE)
        lh.pack(fill=tk.X)
        tk.Label(lh, text="  📋  İşlem Kayıtları",
                 font=FONT_BOLD, bg=SURFACE, fg=ACCENT,
                 anchor="w", pady=6).pack(side=tk.LEFT)

        # Log temizle butonu
        FlatButton(lh, "Temizle", command=lambda: self.w_log.delete(1.0, tk.END),
                   w=70, h=24, color=SURFACE, text_color=DIM,
                   font=("Segoe UI", 8)).pack(side=tk.RIGHT, padx=6, pady=4)

        self.w_log = scrolledtext.ScrolledText(
            lf, width=90, height=10,
            bg=TERM_BG, fg=TERM_FG,
            insertbackground=ACCENT,
            font=FONT_MONO, relief="flat", bd=0,
            selectbackground=ACCENT)
        self.w_log.pack(fill=tk.BOTH, expand=True)

        # ── Footer ──
        tk.Frame(p, bg=SURFACE, height=1).pack(fill=tk.X, pady=(4,0))
        ft = tk.Frame(p, bg=SURFACE)
        ft.pack(fill=tk.X)
        tk.Label(ft,
                 text=f"  🔐 AuthMe SHA256 Kırıcı v2  |  passwords.txt → {PASSWORDS_FILE}",
                 font=("Segoe UI", 8), bg=SURFACE, fg=DIM).pack(side=tk.LEFT, pady=4)

        # Başlangıç modu
        self._toggle_mode()

    # ── Yardımcılar ───────────────────────────────────────────────
    def _log(self, text: str):
        """GUI thread'inden VEYA başka thread'den güvenli log."""
        self.root.after(0, lambda t=text: (
            self.w_log.insert(tk.END, t + "\n"),
            self.w_log.see(tk.END)
        ))

    def _set_busy(self, busy: bool):
        self._running = busy
        self.root.after(0, lambda: (
            self.w_man_btn.set_state(not busy),
            self.w_auto_btn.set_state(not busy)
        ))

    def _toggle_mode(self):
        if self.w_mode.get() == "dict":
            self._brute_outer.pack_forget()
            self._dict_outer.pack(fill=tk.X, after=None)
        else:
            self._dict_outer.pack_forget()
            self._brute_outer.pack(fill=tk.X)

    def _get_hc(self) -> str:
        return self.w_hc_path.get().strip()

    def _validate_hc(self) -> bool:
        path = self._get_hc()
        if not path or not os.path.isfile(path):
            messagebox.showerror("Hata",
                f"hashcat.exe bulunamadı:\n{path}\n\n"
                "Hashcat.exe yolunu kontrol edin.")
            return False
        return True

    def _validate_mode_inputs(self) -> bool:
        mode = self.w_mode.get()
        if mode == "dict":
            dp = self.w_dict.get().strip()
            if not dp or not os.path.isfile(dp):
                messagebox.showerror("Hata", "Sözlük dosyası bulunamadı!\nBir .txt dosyası seçin.")
                return False
        else:
            if not self.w_mask.get().strip():
                messagebox.showerror("Hata", "Brute Force maskesi boş olamaz!")
                return False
        return True

    # ── Dosya seçiciler ──────────────────────────────────────────
    def _browse_hc(self):
        f = filedialog.askopenfilename(title="hashcat.exe seçin",
                                       filetypes=[("EXE", "*.exe")])
        if f:
            self.w_hc_path.set(f)

    def _browse_dict(self):
        f = filedialog.askopenfilename(title="Sözlük dosyası seçin",
                                       filetypes=[("TXT", "*.txt"),
                                                  ("Tüm dosyalar","*.*")])
        if f:
            self.w_dict.set(f)

    # ── Donanım bilgisi ──────────────────────────────────────────
    def _hw_info(self):
        if not self._validate_hc():
            return
        self._log("\n══ DONANIM BİLGİSİ ══")
        runner = HashcatRunner(self._get_hc(), self._log, lambda _: None)
        threading.Thread(target=runner.run_info, daemon=True).start()

    # ── İşlem tamamlandı callback ────────────────────────────────
    def _on_done(self, found: list[tuple[str, str, str]]):
        """HashcatRunner bitince çağrılır (worker thread'den)."""
        if found:
            self._log(f"\n🏆  {len(found)} ŞİFRE KIRILDI!")
            self._log("─" * 45)
            for user, pwd, hval in found:
                self._log(f"  🔑  Kullanıcı: {user:<20}  Şifre: {pwd}")
                PasswordSaver.save(user, pwd, hval)
            self._log(f"\n💾  Kaydedildi: {PASSWORDS_FILE}")
            self.root.after(0, lambda: messagebox.showinfo(
                "✅ Şifreler Kırıldı!",
                f"{len(found)} şifre kırıldı ve passwords.txt'e kaydedildi!\n\n"
                + "\n".join(f"{u} → {p}" for u, p, _ in found)
                + f"\n\n📁 {PASSWORDS_FILE}"
            ))
        else:
            self._log("\n⚠  Şifre kırılamadı.")
            self.root.after(0, lambda: messagebox.showinfo(
                "Tamamlandı",
                "İşlem bitti — hiçbir şifre kırılamadı.\n\n"
                "Farklı sözlük dene veya maske değiştir."
            ))
        self._log("═" * 45)
        self._set_busy(False)

    # ── Otomatik mod ─────────────────────────────────────────────
    def _start_auto(self):
        if self._running:
            return
        if not self._validate_hc():
            return
        if not self._validate_mode_inputs():
            return

        # Mevcut DB'lerin varlığını kontrol et
        available_dbs = [db for db in AUTO_DB_FILES if os.path.isfile(db)]
        if not available_dbs:
            messagebox.showerror(
                "Hata",
                "Hiçbir DB dosyası bulunamadı!\n\n"
                + "\n".join(AUTO_DB_FILES)
                + "\n\nDosya yollarını kontrol edin."
            )
            return

        self._set_busy(True)
        self.w_log.delete(1.0, tk.END)
        self._log("══════ OTOMATİK MOD ══════")

        def worker():
            hashes = []
            for db in available_dbs:
                self._log(f"[DB] Okunuyor: {os.path.basename(db)}")
                pairs = read_db(db)
                hashes.extend(pairs)
                self._log(f"     → {len(pairs)} hash bulundu.")

            # Tekrarları kaldır (hash bazında)
            seen = set()
            unique = []
            for u, h in hashes:
                if h not in seen:
                    seen.add(h)
                    unique.append((u, h))

            self._log(f"\n[TOPLAM] {len(unique)} eşsiz hash kırılacak.\n")

            if not unique:
                self._log("[UYARI] Hiçbir geçerli hash bulunamadı!")
                self._set_busy(False)
                return

            runner = HashcatRunner(self._get_hc(), self._log, self._on_done)
            runner.run(
                mode     = self.w_mode.get(),
                hashes   = unique,
                dict_file= self.w_dict.get().strip(),
                mask     = self.w_mask.get().strip(),
                cpu_only = self.w_cpu.get()
            )

        threading.Thread(target=worker, daemon=True).start()

    # ── Manuel mod ───────────────────────────────────────────────
    def _start_manual(self):
        if self._running:
            return
        if not self._validate_hc():
            return
        if not self._validate_mode_inputs():
            return

        hash_val = self.w_hash.get().strip()
        if not hash_val:
            messagebox.showerror("Hata", "Hash boş olamaz!")
            return
        if not hash_val.startswith("$SHA$"):
            if not messagebox.askyesno(
                "Uyarı",
                "Girilen hash '$SHA$' ile başlamıyor.\n"
                "AuthMe SHA256 formatı değil olabilir.\n\n"
                "Yine de devam et?"
            ):
                return

        username = self.w_username.get().strip() or "Manuel"

        self._set_busy(True)
        self.w_log.delete(1.0, tk.END)
        self._log("══════ MANUEL MOD ══════")
        self._log(f"[HASH]  {hash_val[:60]}...")
        self._log(f"[USER]  {username}\n")

        def worker():
            runner = HashcatRunner(self._get_hc(), self._log, self._on_done)
            runner.run(
                mode     = self.w_mode.get(),
                hashes   = [(username, hash_val)],
                dict_file= self.w_dict.get().strip(),
                mask     = self.w_mask.get().strip(),
                cpu_only = self.w_cpu.get()
            )

        threading.Thread(target=worker, daemon=True).start()


# ══════════════════════════════════════════════════════════════════
# GİRİŞ NOKTASI
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    root = tk.Tk()
    app  = AuthMeGui(root)
    root.mainloop()
