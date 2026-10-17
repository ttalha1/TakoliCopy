import sys
import os
import re
import json
import time
import base64
import ctypes
import tempfile
import threading
import webbrowser
from datetime import datetime
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from pynput import keyboard, mouse

# Varsa yerel AI OCR motorunu yedek (offline fallback) olarak yükle
try:
    from rapidocr_onnxruntime import RapidOCR
    LOCAL_OCR_ENGINE = RapidOCR()
except Exception:
    LOCAL_OCR_ENGINE = None

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QLineEdit, QComboBox, QCheckBox,
    QSystemTrayIcon, QMenu, QScrollArea, QFrame, QSizeGrip,
    QSizePolicy, QDialog
)
from PyQt6.QtCore import (
    Qt, QRect, QPoint, QSize, pyqtSignal, QObject, QTimer, QBuffer, QIODevice
)
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QPixmap, QImage, QIcon, QLinearGradient,
    QGuiApplication, QCursor, QAction
)


# ---------------------------------------------------------------------------
# 0. WINDOWS YÖNETİCİ (ADMIN) YETKİSİ KONTROLÜ
# ---------------------------------------------------------------------------
def ensure_admin_on_windows():
    if sys.platform == "win32" and "--no-admin" not in sys.argv:
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin()
        except Exception:
            is_admin = False

        if not is_admin:
            try:
                script = os.path.abspath(sys.argv[0])
                params = " ".join([f'"{script}"'] + [f'"{a}"' for a in sys.argv[1:]] + ['"--no-admin"'])
                ret = ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", sys.executable, params, os.path.dirname(script), 1
                )
                if int(ret) > 32:
                    sys.exit(0)
            except Exception:
                pass


BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
}

HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update(BROWSER_HEADERS)

# Algılanan Dil Kodlarının Türkçe İsim Karşılıkları
LANG_NAMES_TR = {
    "tr": "Türkçe",
    "en": "İngilizce",
    "de": "Almanca",
    "fr": "Fransızca",
    "es": "İspanyolca",
    "ru": "Rusça",
    "it": "İtalyanca",
    "pt": "Portekizce",
    "nl": "Felemenkçe",
    "pl": "Lehçe",
    "uk": "Ukraynaca",
    "el": "Yunanca",
    "ar": "Arapça",
    "fa": "Farsça",
    "ja": "Japonca",
    "ko": "Korece",
    "zh": "Çince",
    "zh-cn": "Çince",
    "zh-tw": "Çince (Geleneksel)",
    "az": "Azerbaycan Türkçesi",
    "kk": "Kazakça",
    "uz": "Özbekçe",
    "sv": "İsveççe",
    "no": "Norveççe",
    "da": "Danca",
    "fi": "Fince",
    "cs": "Çekçe",
    "ro": "Rumence",
    "hu": "Macarca",
    "bg": "Bulgarca",
    "sr": "Sırpça",
    "hr": "Hırvatça",
    "hi": "Hintçe",
    "id": "Endonezce",
    "vi": "Vietnamca",
    "th": "Tayca",
    "he": "İbranice"
}


def get_language_display_name(lang_code: str) -> str:
    if not lang_code:
        return "Belirsiz"
    norm = lang_code.strip().lower()
    if norm in LANG_NAMES_TR:
        return LANG_NAMES_TR[norm]
    short = norm.split("-")[0]
    return LANG_NAMES_TR.get(short, lang_code.upper())


def guess_language_locally(text: str) -> str:
    """
    Çevrimdışı durumda hem özel harflerden hem de Türkçe/Avrupa dili kelime köklerinden
    yüksek doğrulukla dil tahmini yapar.
    """
    if not text or not text.strip():
        return "en"
    t = text.strip()

    # 1. Alfabe bazlı kesin kontroller
    if re.search(r"[\u0400-\u04FF]", t):
        return "ru"
    if re.search(r"[\u3040-\u30ff]", t):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", t):
        return "ko"
    if re.search(r"[\u4e00-\u9fff]", t):
        return "zh-CN"
    if re.search(r"[\u0600-\u06FF]", t):
        return "ar"
    if re.search(r"[\u0370-\u03FF]", t):
        return "el"

    # 2. Türkçeye özgü harfler
    if re.search(r"[ğşıĞŞİ]", t):
        return "tr"

    # 3. Noktalı harf içermeyen Türkçe kelimeler için kelime/ek analizi (Örn: "Yeni Metin Belgesi", "staj 1 hafta")
    words = set(re.findall(r"[a-zA-ZçöüÇÖÜ]+", t.lower()))
    tr_common = {
        "ve", "bir", "bu", "da", "de", "ile", "mi", "ne", "var", "yok", "gibi", "daha",
        "en", "kadar", "sonra", "olarak", "ben", "sen", "biz", "siz", "onlar", "ama",
        "fakat", "ancak", "veya", "ya", "hem", "yeni", "metin", "belgesi", "dosya",
        "klasor", "hafta", "staj", "rapor", "uygulama", "ekran", "secim", "kopyala",
        "ceviri", "ayarlar", "gecmis", "baslat", "kapat", "oku", "dinle", "ara", "kod"
    }
    if words & tr_common:
        return "tr"
    if re.search(r"\b\w+(lar|ler|mak|mek|iyor|acak|ecek|현|dir|tir|dan|den|nin|nun)\b", t.lower()):
        return "tr"
    if re.search(r"[çöüÇÖÜ]", t) and not re.search(r"[äßÄ]", t):
        return "tr"

    # 4. Almanca / Fransızca / İspanyolca
    if re.search(r"[äßÄ]", t) or (words & {"der", "die", "das", "und", "ist", "nicht", "mit", "auf", "für"}):
        return "de"
    if re.search(r"[éèêàùçœ]", t.lower()) or (words & {"le", "la", "les", "des", "est", "dans", "pour", "avec", "sur"}):
        return "fr"
    if re.search(r"[ñ¡¿]", t.lower()) or (words & {"el", "los", "las", "del", "una", "por", "para", "con", "como"}):
        return "es"

    return "en"


# ---------------------------------------------------------------------------
# 1. KALICI AYARLAR, TEMİZ MODEL LİSTESİ VE AI API YÖNETİMİ
# ---------------------------------------------------------------------------
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".screenpix_takoli_v12.json")

AI_PROVIDERS = {
    "gemini": {
        "name": "Google Gemini (Metin & Görsel)",
        "default_model": "gemini-2.5-flash",
        "models": [
            "gemini-2.5-flash",
            "gemini-3.1-pro-preview",
            "gemini-3-flash-preview",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash"
        ],
        "base_url": "https://generativelanguage.googleapis.com/v1beta"
    },
    "openai": {
        "name": "OpenAI (ChatGPT - Metin & Görsel)",
        "default_model": "gpt-4o-mini",
        "models": [
            "gpt-4o-mini",
            "gpt-4o",
            "gpt-4.1-mini",
            "gpt-4.1",
            "o3-mini"
        ],
        "base_url": "https://api.openai.com/v1/chat/completions"
    },
    "groq": {
        "name": "Groq (Ultra Hızlı Llama / Qwen)",
        "default_model": "llama-3.3-70b-versatile",
        "models": [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "qwen-qwq-32b"
        ],
        "base_url": "https://api.groq.com/openai/v1/chat/completions"
    },
    "deepseek": {
        "name": "DeepSeek API",
        "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "base_url": "https://api.deepseek.com/v1/chat/completions"
    },
    "openrouter": {
        "name": "OpenRouter (Tüm Modeller)",
        "default_model": "google/gemini-2.5-flash",
        "models": [
            "google/gemini-2.5-flash",
            "anthropic/claude-3.7-sonnet",
            "openai/gpt-4o-mini",
            "deepseek/deepseek-chat"
        ],
        "base_url": "https://openrouter.ai/api/v1/chat/completions"
    },
    "ollama": {
        "name": "Ollama (Yerel / Ücretsiz / Çevrimdışı)",
        "default_model": "llama3.2",
        "models": ["llama3.2", "llava", "qwen2.5-coder", "deepseek-r1"],
        "base_url": "http://localhost:11434/v1/chat/completions"
    },
    "custom": {
        "name": "Özel API (OpenAI Uyumlu Endpoint)",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini"],
        "base_url": "https://api.openai.com/v1/chat/completions"
    }
}

DEFAULT_SETTINGS = {
    "hotkey": {"ctrl": False, "alt": True, "shift": False, "key": "s", "label": "Alt + S"},
    "menuHotkey": {"ctrl": False, "alt": True, "shift": False, "key": "m", "label": "Alt + M"},
    "holdKey": {"type": "modifier", "key": "alt", "label": "Alt"},
    "targetLang": "tr",
    "autoCopy": False,
    "panelPosMode": "cursor",
    "menuPosMode": "bottom_right",
    "panelPinned": False,
    "menuPinned": False,
    "savedPanelPos": None,
    "savedMenuPos": None,
    "savedPanelSize": [465, 445],
    "savedMenuSize": [395, 560],
    "layoutMode": "tabbed",
    "showVisualBar": True,
    "showOcrSection": True,
    "showTranslateSection": True,
    "showAiSection": True,
    "aiProvider": "gemini",
    "aiApiKeys": {},
    "aiVerifiedModels": {},
    "aiConnected": False,
    "aiModel": "gemini-2.5-flash",
    "aiCustomUrl": "https://api.openai.com/v1/chat/completions",
    "aiSystemPrompt": "Sen hızlı ve net yanıt veren bir asistansın. Yanıtlarını Türkçe ve doğrudan ver.",
    "aiSendImage": True,
    "aiAutoRun": False,
    "history": []
}


def is_clean_gemini_model_id(m_id: str) -> bool:
    m = m_id.lower().strip()
    if not m.startswith("gemini-"):
        return False
    if "2.5-pro" in m or "gemini-1." in m or "gemini-pro" in m:
        return False
    junk_tokens = (
        "-001", "-002", "-latest", "-tuning", "tts", "audio",
        "embedding", "aqa", "imagen", "veo", "learnlm", "robotics",
        "computer-use", "thinking", "-8b", "-exp-"
    )
    if any(tok in m for tok in junk_tokens):
        return False
    if re.search(r"-\d{2}-\d{2}$", m) or re.search(r"-\d{4}$", m):
        return False
    return True


def is_clean_openai_compatible_model_id(prov: str, m_id: str) -> bool:
    """OpenAI, Groq, DeepSeek ve OpenRouter için ses/resim/eski kopya modelleri temizler."""
    m = m_id.lower().strip()
    if not m:
        return False
    bad_keywords = (
        "whisper", "tts", "dall-e", "embedding", "moderation", "audio",
        "realtime", "babbage", "davinci", "curie", "ada", "instruct",
        "guard", "vision-preview", "search-preview", "transcribe"
    )
    if any(b in m for b in bad_keywords):
        return False
    # Tarihli alt kopyaları (örn: gpt-4o-2024-08-06, gpt-3.5-turbo-0125) ele
    if re.search(r"-\d{4}-\d{2}-\d{2}$", m) or re.search(r"-\d{4}$", m):
        return False
    if prov == "openai" and not (m.startswith("gpt-4") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4")):
        return False
    return True


class StorageManager:
    @staticmethod
    def load() -> dict:
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    data = DEFAULT_SETTINGS.copy()
                    data.update(saved)
                    if not isinstance(data.get("aiApiKeys"), dict):
                        data["aiApiKeys"] = {}
                    if not isinstance(data.get("aiVerifiedModels"), dict):
                        data["aiVerifiedModels"] = {}
                    if "2.5-pro" in str(data.get("aiModel", "")):
                        data["aiModel"] = "gemini-2.5-flash"
                    return data
            except Exception:
                pass
        return DEFAULT_SETTINGS.copy()

    @staticmethod
    def save(data: dict):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def is_ai_currently_connected(settings: dict) -> bool:
    prov = settings.get("aiProvider", "gemini")
    if prov == "ollama":
        return bool(settings.get("aiConnected", False))
    key = settings.get("aiApiKeys", {}).get(prov, "").strip()
    return bool(key) and bool(settings.get("aiConnected", False))


# ---------------------------------------------------------------------------
# 2. DONANIM SEVİYESİ WINDOWS PENCERE VE TUŞ YARDIMCILARI
# ---------------------------------------------------------------------------
def force_bring_to_front(widget: QWidget):
    widget.show()
    widget.raise_()
    widget.activateWindow()
    widget.update()
    widget.repaint()
    if sys.platform == "win32":
        try:
            hwnd = int(widget.winId())
            user32 = ctypes.windll.user32
            fg_hwnd = user32.GetForegroundWindow()
            fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
            cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
            if fg_thread != cur_thread:
                user32.AttachThreadInput(fg_thread, cur_thread, True)
                user32.SetForegroundWindow(hwnd)
                user32.BringWindowToTop(hwnd)
                user32.AttachThreadInput(fg_thread, cur_thread, False)
            else:
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass


WIN_VK_MAP = {
    "alt": 0x12,
    "ctrl": 0x11,
    "shift": 0x10,
    "space": 0x20,
    "ş": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "ö": 0xBF,
    "ğ": 0xDB, "ç": 0xDC, "ü": 0xDD, "i": 0xDE
}


def is_vk_down_win32(vk_code: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000)
    except Exception:
        return False


def key_name_to_vk(key_name: str) -> int:
    k = key_name.lower().strip()
    if k in WIN_VK_MAP:
        return WIN_VK_MAP[k]
    if len(k) == 1 and ("a" <= k <= "z" or "0" <= k <= "9"):
        return ord(k.upper())
    if k.startswith("f") and k[1:].isdigit():
        fn = int(k[1:])
        if 1 <= fn <= 12:
            return 0x70 + (fn - 1)
    return 0


def is_key_physically_down(key_name: str, fallback_set: set) -> bool:
    if sys.platform == "win32":
        vk = key_name_to_vk(key_name)
        if vk:
            return is_vk_down_win32(vk)
    return key_name.lower() in fallback_set


def is_left_mouse_pressed() -> bool:
    if sys.platform == "win32":
        return is_vk_down_win32(0x01)
    return bool(QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton)


# ---------------------------------------------------------------------------
# 3. PENCERE KONUMLANDIRMA VE LOGO YARDIMCILARI
# ---------------------------------------------------------------------------
def compute_window_position(
    mode: str,
    win_w: int,
    win_h: int,
    reference_rect: QRect = None,
    saved_pos: list = None
) -> QPoint:
    if reference_rect is not None and not reference_rect.isNull():
        ref_pt = reference_rect.center()
    else:
        ref_pt = QCursor.pos()

    screen = QGuiApplication.screenAt(ref_pt) or QGuiApplication.primaryScreen()
    geo = screen.availableGeometry()
    margin = 16

    if mode == "remember" and isinstance(saved_pos, list) and len(saved_pos) == 2:
        x = min(max(geo.left() + 8, int(saved_pos[0])), geo.right() - win_w - 8)
        y = min(max(geo.top() + 8, int(saved_pos[1])), geo.bottom() - win_h - 8)
        return QPoint(x, y)

    if mode == "top_right":
        return QPoint(geo.right() - win_w - margin, geo.top() + margin)
    if mode == "bottom_right":
        return QPoint(geo.right() - win_w - margin, geo.bottom() - win_h - margin)
    if mode == "top_left":
        return QPoint(geo.left() + margin, geo.top() + margin)
    if mode == "bottom_left":
        return QPoint(geo.left() + margin, geo.bottom() - win_h - margin)
    if mode == "center":
        return QPoint(
            geo.left() + (geo.width() - win_w) // 2,
            geo.top() + (geo.height() - win_h) // 2
        )

    if reference_rect is not None and not reference_rect.isNull():
        left = min(max(geo.left() + 12, reference_rect.left()), geo.right() - win_w - 16)
        if reference_rect.bottom() + win_h + 20 < geo.bottom():
            top = reference_rect.bottom() + 10
        else:
            top = max(geo.top() + 12, reference_rect.top() - win_h - 10)
        return QPoint(left, top)

    cursor_pt = QCursor.pos()
    left = min(max(geo.left() + 12, cursor_pt.x() - win_w // 2), geo.right() - win_w - 16)
    top = min(max(geo.top() + 12, cursor_pt.y() + 18), geo.bottom() - win_h - 16)
    return QPoint(left, top)


def get_app_logo(size: int = 128) -> QPixmap:
    local_icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
    if os.path.exists(local_icon):
        pm = QPixmap(local_icon)
        if not pm.isNull():
            return pm.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    bg_grad = QLinearGradient(0, 0, size, size)
    bg_grad.setColorAt(0.0, QColor("#0f172a"))
    bg_grad.setColorAt(1.0, QColor("#1e1b4b"))
    painter.setBrush(bg_grad)
    painter.setPen(Qt.PenStyle.NoPen)
    radius = size * 0.22
    painter.drawRoundedRect(0, 0, size, size, radius, radius)

    pen = QPen(QColor("#38bdf8"))
    pen.setWidthF(max(2.0, size * 0.042))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    m = size * 0.14
    ln = size * 0.14

    painter.drawLine(int(m), int(m + ln), int(m), int(m))
    painter.drawLine(int(m), int(m), int(m + ln), int(m))
    painter.drawLine(int(size - m - ln), int(m), int(size - m), int(m))
    painter.drawLine(int(size - m), int(m), int(size - m), int(m + ln))
    painter.drawLine(int(m), int(size - m - ln), int(m), int(size - m))
    painter.drawLine(int(m), int(size - m), int(m + ln), int(size - m))
    painter.drawLine(int(size - m - ln), int(size - m), int(size - m), int(size - m))
    painter.drawLine(int(size - m), int(size - m), int(size - m), int(size - m - ln))

    t_grad = QLinearGradient(size * 0.26, size * 0.26, size * 0.74, size * 0.76)
    t_grad.setColorAt(0.0, QColor("#38bdf8"))
    t_grad.setColorAt(1.0, QColor("#818cf8"))
    painter.setBrush(t_grad)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(int(size * 0.26), int(size * 0.26), int(size * 0.48), int(size * 0.12), 4, 4)
    painter.drawRoundedRect(int(size * 0.43), int(size * 0.26), int(size * 0.14), int(size * 0.48), 4, 4)

    painter.setBrush(QColor("#a5f3fc"))
    painter.drawRoundedRect(int(size * 0.19), int(size * 0.49), int(size * 0.62), max(2, int(size * 0.03)), 2, 2)

    painter.end()
    return pixmap


# ---------------------------------------------------------------------------
# 4. ÇOK DİLLİ DOĞAL SESLİ OKUMA MOTORU (Algılanan Dilin Sesiyle Okur)
# ---------------------------------------------------------------------------
class NativeTTSController(QObject):
    state_changed = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self.is_playing = False
        self.active_target = ""
        self._stop_event = threading.Event()
        self._current_alias = None

    def toggle_speak(self, text: str, lang_code: str, target_id: str):
        clean = text.strip()
        if not clean:
            return

        if self.is_playing:
            prev_target = self.active_target
            self.stop()
            if prev_target == target_id:
                return

        tts_lang = (lang_code or "").strip()
        if not tts_lang or tts_lang == "auto":
            tts_lang = guess_language_locally(clean)

        self._stop_event.clear()
        self.is_playing = True
        self.active_target = target_id
        self.state_changed.emit(True, target_id)

        threading.Thread(
            target=self._tts_worker,
            args=(clean, tts_lang, target_id),
            daemon=True
        ).start()

    def stop(self):
        self._stop_event.set()
        if sys.platform == "win32" and self._current_alias:
            try:
                ctypes.windll.winmm.mciSendStringW(f"stop {self._current_alias}", None, 0, 0)
                ctypes.windll.winmm.mciSendStringW(f"close {self._current_alias}", None, 0, 0)
            except Exception:
                pass
        self.is_playing = False
        self.active_target = ""
        self.state_changed.emit(False, "")

    def _tts_worker(self, text: str, lang_code: str, target_id: str):
        try:
            chunks = self._split_text(text, max_len=180)
            for idx, chunk in enumerate(chunks):
                if self._stop_event.is_set():
                    break

                url = "https://translate.googleapis.com/translate_tts"
                params = {
                    "ie": "UTF-8",
                    "client": "tw-ob",
                    "tl": lang_code,
                    "q": chunk
                }
                resp = HTTP_SESSION.get(url, params=params, timeout=7)
                if resp.status_code != 200 or self._stop_event.is_set():
                    break

                tmp_mp3 = os.path.join(tempfile.gettempdir(), f"sp_tts_{int(time.time() * 1000)}_{idx}.mp3")
                with open(tmp_mp3, "wb") as f:
                    f.write(resp.content)

                self._play_mp3_file(tmp_mp3)
                try:
                    os.remove(tmp_mp3)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            if not self._stop_event.is_set():
                self.is_playing = False
                self.active_target = ""
                self.state_changed.emit(False, "")

    def _play_mp3_file(self, filepath: str):
        if sys.platform == "win32":
            alias = f"sp_{int(time.time() * 1000)}"
            self._current_alias = alias
            mci = ctypes.windll.winmm.mciSendStringW
            mci(f'open "{filepath}" type mpegvideo alias {alias}', None, 0, 0)
            mci(f"play {alias}", None, 0, 0)

            buf = ctypes.create_unicode_buffer(128)
            while not self._stop_event.is_set():
                mci(f"status {alias} mode", buf, 128, 0)
                if buf.value.lower() != "playing":
                    break
                time.sleep(0.06)

            mci(f"close {alias}", None, 0, 0)
            self._current_alias = None
        else:
            import subprocess
            cmd = ["afplay", filepath] if sys.platform == "darwin" else ["ffplay", "-nodisp", "-autoexit", filepath]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                while proc.poll() is None:
                    if self._stop_event.is_set():
                        proc.terminate()
                        break
                    time.sleep(0.06)
            except Exception:
                pass

    @staticmethod
    def _split_text(text: str, max_len: int = 180) -> list:
        words = text.split()
        chunks, current = [], ""
        for w in words:
            if len(current) + len(w) + 1 <= max_len:
                current = f"{current} {w}".strip()
            else:
                if current:
                    chunks.append(current)
                current = w[:max_len]
        if current:
            chunks.append(current)
        return chunks


# ---------------------------------------------------------------------------
# 5. ARKA PLAN MOTORU (Kusursuz Dil Algılama & Çok Dilli Çeviri & AI)
# ---------------------------------------------------------------------------
class BackgroundEngine(QObject):
    ocr_finished = pyqtSignal(dict)
    retranslate_finished = pyqtSignal(str, str)
    lens_finished = pyqtSignal()
    ai_finished = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.tts = NativeTTSController()

    def handle_ocr_and_translate(self, primary_b64_jpg: str, secondary_b64_jpg: str, target_lang: str):
        def worker():
            try:
                extracted = ""

                # 1. ÖNCE BULUT OCR ENGINE 2 (Türkçe ve tüm dünya dillerinin özel harflerini %100 korur)
                extracted = self._perform_cloud_ocr(primary_b64_jpg, engine="2")

                # 2. Boşsa alternatif kontrastlı görüntüyü dene
                if not extracted or not extracted.strip():
                    extracted = self._perform_cloud_ocr(secondary_b64_jpg, engine="2")

                # 3. İnternet/API sorunu varsa Yerel AI OCR (RapidOCR) yedek olarak devreye girsin
                if (not extracted or not extracted.strip()) and LOCAL_OCR_ENGINE is not None:
                    try:
                        img_bytes = base64.b64decode(primary_b64_jpg)
                        res, _ = LOCAL_OCR_ENGINE(img_bytes)
                        if res:
                            extracted = "\n".join(line[1] for line in res if len(line) > 1)
                    except Exception:
                        pass

                if not extracted or not extracted.strip():
                    self.ocr_finished.emit({
                        "text": "",
                        "translatedText": "",
                        "detectedLang": "",
                        "error": "Metin algılanamadı. Görseli Lens ile aratabilir veya 🤖 Yapay Zeka sekmesinden doğrudan görseli yorumlatabilirsiniz."
                    })
                    return

                clean_text = extracted.strip()
                translated_text, src_lang = self._translate_text_resilient(clean_text, target_lang)

                self.ocr_finished.emit({
                    "text": clean_text,
                    "translatedText": translated_text,
                    "detectedLang": src_lang,
                    "error": None
                })
            except Exception as err:
                self.ocr_finished.emit({
                    "text": "",
                    "translatedText": "",
                    "detectedLang": "",
                    "error": f"OCR Bağlantı Hatası: {str(err)}"
                })

        threading.Thread(target=worker, daemon=True).start()

    def translate_only(self, text: str, target_lang: str):
        def worker():
            tr, detected_src = self._translate_text_resilient(text, target_lang)
            self.retranslate_finished.emit(tr, detected_src)

        threading.Thread(target=worker, daemon=True).start()

    def ask_ai(self, settings: dict, ocr_text: str, user_prompt: str, image_b64: str = None):
        def worker():
            try:
                answer = self._call_ai_provider(settings, ocr_text, user_prompt, image_b64)
                settings["aiConnected"] = True
                StorageManager.save(settings)
                self.ai_finished.emit({"answer": answer, "error": None, "connected": True})
            except Exception as e:
                settings["aiConnected"] = False
                StorageManager.save(settings)
                self.ai_finished.emit({"answer": "", "error": str(e), "connected": False})

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def ping_single_gemini_model(api_key: str, model_id: str) -> bool:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": [{"text": "1"}]}],
                "generationConfig": {"maxOutputTokens": 2}
            }
            r = HTTP_SESSION.post(url, json=payload, timeout=6)
            return r.status_code == 200
        except Exception:
            return False

    def _call_ai_provider(self, settings: dict, ocr_text: str, user_prompt: str, image_b64: str = None) -> str:
        provider = settings.get("aiProvider", "gemini")
        api_keys = settings.get("aiApiKeys", {})
        api_key = api_keys.get(provider, "").strip()
        model = settings.get("aiModel", "").strip() or AI_PROVIDERS.get(provider, {}).get("default_model", "gemini-2.5-flash")
        sys_prompt = settings.get("aiSystemPrompt", "Sen hızlı ve net yanıt veren bir asistansın. Yanıtlarını Türkçe ver.")
        send_img = bool(settings.get("aiSendImage", True)) and bool(image_b64)

        if provider != "ollama" and not api_key:
            raise RuntimeError(
                f"🔑 {AI_PROVIDERS.get(provider, {}).get('name', provider)} için henüz API Anahtarı girilmedi.\n\n"
                "Sağ üstteki '🔑 API Ayarla' butonuna tıklayarak kendi API anahtarınızı bağlayabilirsiniz."
            )

        full_user_message = user_prompt.strip()
        if ocr_text and ocr_text.strip():
            full_user_message += f"\n\n--- Ekrandan Yakalanan Metin ---\n{ocr_text.strip()}"
        elif not send_img:
            full_user_message += "\n\n(Not: Ekranda metin algılanamadı, lütfen genel bilgi ver.)"

        if provider == "gemini":
            parts = [{"text": f"{sys_prompt}\n\nKullanıcı İsteği: {full_user_message}"}]
            if send_img:
                parts.append({
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": image_b64
                    }
                })
            payload = {"contents": [{"parts": parts}]}

            fallback_models = [
                model,
                "gemini-2.5-flash",
                "gemini-3-flash-preview",
                "gemini-3.1-pro-preview",
                "gemini-2.5-flash-lite",
                "gemini-2.0-flash"
            ]
            tried = []
            last_error = ""

            for candidate_model in fallback_models:
                if not candidate_model or "2.5-pro" in candidate_model or candidate_model in tried:
                    continue
                tried.append(candidate_model)

                url = f"https://generativelanguage.googleapis.com/v1beta/models/{candidate_model}:generateContent?key={api_key}"
                resp = HTTP_SESSION.post(url, json=payload, timeout=25)

                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        if candidate_model != settings.get("aiModel"):
                            settings["aiModel"] = candidate_model
                            StorageManager.save(settings)
                        res_parts = candidates[0].get("content", {}).get("parts", [])
                        return "".join(p.get("text", "") for p in res_parts).strip()

                try:
                    err_detail = resp.json().get("error", {}).get("message", resp.text[:200])
                except Exception:
                    err_detail = resp.text[:200]
                last_error = f"Gemini API Hatası ({resp.status_code}): {err_detail}"

                if "API key not valid" in err_detail or resp.status_code in (401, 403):
                    break

            raise RuntimeError(last_error or "Gemini API yanıt vermedi.")

        if provider == "custom":
            endpoint = settings.get("aiCustomUrl", "").strip() or "https://api.openai.com/v1/chat/completions"
        else:
            endpoint = AI_PROVIDERS.get(provider, {}).get("base_url", "https://api.openai.com/v1/chat/completions")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": BROWSER_HEADERS["User-Agent"]
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        vision_capable = provider in ("openai", "openrouter", "custom") or (
            "vision" in model.lower() or "llava" in model.lower() or "4o" in model.lower() or "gemini" in model.lower() or "scout" in model.lower()
        )

        if send_img and vision_capable:
            user_content = [
                {"type": "text", "text": full_user_message},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]
        else:
            user_content = full_user_message

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_content}
            ],
            "temperature": 0.4
        }

        resp = HTTP_SESSION.post(endpoint, headers=headers, json=payload, timeout=25)
        if resp.status_code != 200:
            try:
                err_msg = resp.json().get("error", {}).get("message", resp.text[:200])
            except Exception:
                err_msg = resp.text[:200]
            raise RuntimeError(f"API Hatası ({resp.status_code}): {err_msg}")

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("API boş yanıt döndürdü.")
        return choices[0].get("message", {}).get("content", "").strip()

    def _perform_cloud_ocr(self, base64_jpg: str, engine: str = "2") -> str:
        api_keys = ["helloworld", "K89674372888957"]

        for key in api_keys:
            try:
                payload = {
                    "base64Image": f"data:image/jpeg;base64,{base64_jpg}",
                    "apikey": key,
                    "OCREngine": engine,
                    "scale": "true"
                }
                resp = HTTP_SESSION.post(
                    "https://api.ocr.space/parse/image",
                    data=payload,
                    timeout=8
                )
                if resp.status_code != 200:
                    continue

                data = resp.json()
                if data.get("IsErroredOnProcessing"):
                    continue

                parsed = data.get("ParsedResults", [])
                text = parsed[0].get("ParsedText", "") if parsed else ""
                if text and text.strip():
                    return text
            except Exception:
                continue
        return ""

    def _translate_text_resilient(self, text: str, target_lang: str) -> tuple:
        """
        POST + dj=1 JSON protokolüyle çalışan, 429 hatasına takılmayan ve hem
        kaynak dili (src) hem de çok satırlı çevirileri %100 doğru döndüren 4 katmanlı motor.
        """
        clean_q = text.strip()
        if not clean_q:
            return "", "en"

        fallback_src = guess_language_locally(clean_q)

        # 1. & 2. & 3. Katman: Google Translate POST dj=1 uç noktaları (Farklı sunucu havuzları)
        google_endpoints = [
            ("https://translate.google.com/translate_a/single", "at"),
            ("https://clients5.google.com/translate_a/single", "at"),
            ("https://translate.googleapis.com/translate_a/single", "gtx")
        ]

        for url, client_id in google_endpoints:
            try:
                params = {
                    "client": client_id,
                    "sl": "auto",
                    "tl": target_lang,
                    "dt": "t",
                    "dj": "1"
                }
                # POST isteği URL uzunluk sınırına ve GET 429 önbelleğine takılmaz
                resp = requests.post(
                    url,
                    params=params,
                    data={"q": clean_q},
                    headers=BROWSER_HEADERS,
                    timeout=6
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        sentences = data.get("sentences", [])
                        translated = "".join(s.get("trans", "") for s in sentences if isinstance(s, dict))
                        detected_src = data.get("src") or fallback_src
                        if translated.strip():
                            return translated.strip(), str(detected_src)
                    elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                        translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
                        detected_src = data[2] if len(data) > 2 and isinstance(data[2], str) and data[2] else fallback_src
                        if translated.strip():
                            return translated.strip(), str(detected_src)
            except Exception:
                continue

        # 4. Katman: clients5 dict-chrome-ex (Hem dict hem list yanıtını tam çözer)
        try:
            r_c5 = requests.post(
                "https://clients5.google.com/translate_a/t",
                params={"client": "dict-chrome-ex", "sl": "auto", "tl": target_lang},
                data={"q": clean_q},
                headers=BROWSER_HEADERS,
                timeout=6
            )
            if r_c5.status_code == 200:
                d5 = r_c5.json()
                if isinstance(d5, dict):
                    sents = d5.get("sentences", [])
                    tr_txt = "".join(s.get("trans", "") for s in sents if isinstance(s, dict))
                    src_c5 = d5.get("src") or fallback_src
                    if tr_txt.strip():
                        return tr_txt.strip(), str(src_c5)
                elif isinstance(d5, list) and len(d5) > 0:
                    first = d5[0]
                    if isinstance(first, list):
                        return str(first[0]).strip(), (str(first[1]) if len(first) > 1 and first[1] else fallback_src)
                    return str(first).strip(), fallback_src
        except Exception:
            pass

        # 5. Katman: MyMemory API (Doğru algılanmış kaynak dil ile)
        try:
            src_lang = fallback_src
            if src_lang.lower() == target_lang.lower():
                return clean_q, src_lang
            r_mm = HTTP_SESSION.get(
                "https://api.mymemory.translated.net/get",
                params={"q": clean_q[:500], "langpair": f"{src_lang}|{target_lang}"},
                timeout=6
            )
            if r_mm.status_code == 200:
                d_mm = r_mm.json()
                tr_mm = d_mm.get("responseData", {}).get("translatedText")
                if tr_mm:
                    return tr_mm.strip(), src_lang
        except Exception:
            pass

        return clean_q, fallback_src

    def open_google_lens_with_auto_upload(self, base64_jpg: str):
        def worker():
            try:
                html_code = f"""<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8">
  <title>Google Lens • Görsel Arama</title>
</head>
<body style="margin:0;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;background:#0f172a;color:#38bdf8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <h2 style="margin:0 0 8px 0;">Görsel Google Lens'e Aktarılıyor...</h2>
  <p style="color:#cbd5e1;margin:0;">Sonuçlar otomatik açılıyor, lütfen bekleyin.</p>
  <script>
    (function() {{
      const bstr = atob("{base64_jpg}");
      let n = bstr.length;
      const u8arr = new Uint8Array(n);
      while (n--) {{
        u8arr[n] = bstr.charCodeAt(n);
      }}
      const file = new File([u8arr], "capture.jpg", {{ type: "image/jpeg" }});

      const form = document.createElement("form");
      form.method = "POST";
      form.enctype = "multipart/form-data";
      form.action = "https://lens.google.com/v3/upload?hl=tr&st=" + Date.now();
      form.style.display = "none";

      const fileInput = document.createElement("input");
      fileInput.type = "file";
      fileInput.name = "encoded_image";

      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(file);
      fileInput.files = dataTransfer.files;

      const dimInput = document.createElement("input");
      dimInput.type = "hidden";
      dimInput.name = "processed_image_dimensions";
      dimInput.value = "400,400";

      form.appendChild(fileInput);
      form.appendChild(dimInput);
      document.body.appendChild(form);
      form.submit();
    }})();
  </script>
</body>
</html>"""
                tmp_path = os.path.join(tempfile.gettempdir(), f"sp_lens_{int(time.time() * 1000)}.html")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(html_code)
                webbrowser.open(f"file:///{tmp_path.replace(os.sep, '/')}")
            finally:
                self.lens_finished.emit()

        threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# 6. YAPAY ZEKA (AI) API AYARLARI PENCERESİ (Tüm Sağlayıcılar İçin Akıllı Liste)
# ---------------------------------------------------------------------------
class AiConfigDialog(QDialog):
    config_saved = pyqtSignal()

    def __init__(self, settings: dict, engine: BackgroundEngine, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.engine = engine
        self.setWindowTitle("🤖 Yapay Zeka (AI) API Ayarları")
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.CustomizeWindowHint |
            Qt.WindowType.WindowTitleHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setFixedWidth(460)
        self.setStyleSheet("""
            QDialog { background-color: #0f172a; color: #f8fafc; }
            QLabel { color: #f1f5f9; font-size: 12px; }
            QLineEdit, QComboBox {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 6px 8px;
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #38bdf8; }
            QComboBox QAbstractItemView {
                background-color: #1e293b;
                color: #f8fafc;
                selection-background-color: #0284c7;
            }
            QCheckBox { color: #cbd5e1; font-size: 12px; }
            QPushButton {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 7px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #334155; }
        """)
        self._init_ui()
        self._load_current()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("🤖 Kendi Yapay Zeka API'nızı Bağlayın")
        title.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 800;")
        layout.addWidget(title)

        desc = QLabel(
            "API anahtarınızı yapıştırıp '🔍 Çalışan Modelleri Bul' butonuna bastığınızda "
            "seçtiğiniz sağlayıcıdaki (Gemini, OpenAI, Groq, DeepSeek, OpenRouter, Ollama) aktif modeller listelenir."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #94a3b8; font-size: 11px;")
        layout.addWidget(desc)

        layout.addWidget(QLabel("1. Yapay Zeka Sağlayıcısı (Provider):"))
        self.provider_combo = QComboBox()
        for key, info in AI_PROVIDERS.items():
            self.provider_combo.addItem(info["name"], key)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        layout.addWidget(self.provider_combo)

        key_header = QHBoxLayout()
        key_header.addWidget(QLabel("2. API Anahtarı (API Key):"))
        self.btn_toggle_key = QPushButton("👁️ Göster")
        self.btn_toggle_key.setFixedWidth(75)
        self.btn_toggle_key.setStyleSheet("padding: 2px 6px; font-size: 10px;")
        self.btn_toggle_key.clicked.connect(self._toggle_key_visibility)
        key_header.addStretch()
        key_header.addWidget(self.btn_toggle_key)
        layout.addLayout(key_header)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("API anahtarınızı buraya yapıştırın (Örn: AIzaSy... veya sk-...)")
        layout.addWidget(self.api_key_input)

        model_header = QHBoxLayout()
        model_header.addWidget(QLabel("3. Kullanılacak Model:"))
        self.btn_fetch_models = QPushButton("🔍 Çalışan Modelleri Bul")
        self.btn_fetch_models.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fetch_models.setStyleSheet(
            "QPushButton { background-color: #1e1b4b; color: #38bdf8; border: 1px solid #4338ca; "
            "padding: 3px 9px; font-size: 11px; font-weight: 700; border-radius: 5px; }"
            "QPushButton:hover { background-color: #312e81; color: #ffffff; }"
        )
        self.btn_fetch_models.clicked.connect(self._discover_working_models)
        model_header.addStretch()
        model_header.addWidget(self.btn_fetch_models)
        layout.addLayout(model_header)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        layout.addWidget(self.model_combo)

        self.url_label = QLabel("4. Özel API Endpoint (Base URL):")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://api.openai.com/v1/chat/completions")
        layout.addWidget(self.url_label)
        layout.addWidget(self.url_input)

        self.chk_vision = QCheckBox("👁️ Seçilen ekran görüntüsünü de AI'ya gönder (Vision / Görsel Analiz)")
        self.chk_auto = QCheckBox("⚡ Ekran seçimi yapıldığında AI analizini otomatik başlat")
        layout.addWidget(self.chk_vision)
        layout.addWidget(self.chk_auto)

        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("color: #38bdf8; font-size: 11px;")
        layout.addWidget(self.status_lbl)

        btn_row = QHBoxLayout()
        self.btn_test = QPushButton("⚡ Bağlantıyı Test Et")
        self.btn_test.clicked.connect(self._test_connection)

        btn_save = QPushButton("💾 Kaydet ve Bağla")
        btn_save.setStyleSheet("background-color: #0284c7; border-color: #0369a1; color: #ffffff;")
        btn_save.clicked.connect(self._save_and_close)

        btn_row.addWidget(self.btn_test)
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)

    def _toggle_key_visibility(self):
        if self.api_key_input.echoMode() == QLineEdit.EchoMode.Password:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_key.setText("🙈 Gizle")
        else:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_key.setText("👁️ Göster")

    def _discover_working_models(self):
        prov = self.provider_combo.currentData()
        api_key = self.api_key_input.text().strip()

        if prov != "ollama" and not api_key:
            self.status_lbl.setText("⚠️ Lütfen önce 2. kutuya API anahtarınızı yapıştırın.")
            self.status_lbl.setStyleSheet("color: #fbbf24; font-size: 11px;")
            return

        self.status_lbl.setText("⏳ Sağlayıcıdaki aktif modeller sorgulanıyor ve doğrulanıyor...")
        self.status_lbl.setStyleSheet("color: #38bdf8; font-size: 11px;")
        QApplication.processEvents()

        try:
            # A) GOOGLE GEMINI
            if prov == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                resp = HTTP_SESSION.get(url, timeout=8)
                if resp.status_code != 200:
                    raise RuntimeError(f"API Anahtarı geçersiz veya bağlantı hatası (HTTP {resp.status_code}).")

                raw_models = resp.json().get("models", [])
                candidates = []
                for m in raw_models:
                    if "generateContent" in m.get("supportedGenerationMethods", []):
                        m_id = m.get("name", "").replace("models/", "")
                        if is_clean_gemini_model_id(m_id) and m_id not in candidates:
                            candidates.append(m_id)

                priority_order = [
                    "gemini-2.5-flash",
                    "gemini-3.1-pro-preview",
                    "gemini-3-flash-preview",
                    "gemini-2.5-flash-lite",
                    "gemini-2.0-flash",
                    "gemini-2.0-flash-lite"
                ]
                ordered_candidates = [m for m in priority_order if m in candidates]
                for c in candidates:
                    if c not in ordered_candidates and len(ordered_candidates) < 8:
                        ordered_candidates.append(c)

                if not ordered_candidates:
                    ordered_candidates = priority_order[:5]

                working_models = []
                with ThreadPoolExecutor(max_workers=6) as ex:
                    future_map = {
                        ex.submit(self.engine.ping_single_gemini_model, api_key, mid): mid
                        for mid in ordered_candidates
                    }
                    for fut in as_completed(future_map):
                        mid = future_map[fut]
                        try:
                            if fut.result():
                                working_models.append(mid)
                        except Exception:
                            pass

                working_models.sort(key=lambda x: ordered_candidates.index(x) if x in ordered_candidates else 99)
                if not working_models:
                    raise RuntimeError("Bu API anahtarında kota açık olan aktif bir Gemini modeli bulunamadı.")

                self._save_verified_model_list(prov, working_models)
                return

            # B) YEREL OLLAMA
            if prov == "ollama":
                clean_list = []
                try:
                    r_ol = HTTP_SESSION.get("http://localhost:11434/api/tags", timeout=5)
                    if r_ol.status_code == 200:
                        for item in r_ol.json().get("models", []):
                            name = item.get("name")
                            if name:
                                clean_list.append(name)
                except Exception:
                    pass
                if not clean_list:
                    raise RuntimeError("Yerel Ollama servisine (localhost:11434) ulaşılamadı veya kurulu model yok.")
                self._save_verified_model_list(prov, clean_list[:12])
                return

            # C) OPENAI / GROQ / DEEPSEEK / OPENROUTER / CUSTOM
            base_ep = self.url_input.text().strip() if prov == "custom" else AI_PROVIDERS.get(prov, {}).get("base_url", "")
            models_url = re.sub(r"/chat/completions/?$", "/models", base_ep)
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = HTTP_SESSION.get(models_url, headers=headers, timeout=8)
            if resp.status_code != 200:
                raise RuntimeError(f"API Anahtarı doğrulanamadı (HTTP {resp.status_code}).")

            data = resp.json()
            clean_list = []
            for item in data.get("data", []):
                m_id = str(item.get("id", ""))
                if is_clean_openai_compatible_model_id(prov, m_id) and m_id not in clean_list:
                    clean_list.append(m_id)

            # En popüler modelleri en üste sırala
            preferred = AI_PROVIDERS.get(prov, {}).get("models", [])
            clean_list.sort(key=lambda x: (0 if x in preferred else 1, x))
            clean_list = clean_list[:10]

            if not clean_list:
                clean_list = preferred[:]

            self._save_verified_model_list(prov, clean_list)

        except Exception as e:
            self.settings["aiConnected"] = False
            StorageManager.save(self.settings)
            self.config_saved.emit()
            self.status_lbl.setText(f"❌ {str(e)}")
            self.status_lbl.setStyleSheet("color: #f87171; font-size: 11px;")

    def _save_verified_model_list(self, prov: str, model_list: list):
        self.model_combo.clear()
        for m in model_list:
            self.model_combo.addItem(m)
        self.model_combo.setCurrentIndex(0)

        if "aiVerifiedModels" not in self.settings or not isinstance(self.settings["aiVerifiedModels"], dict):
            self.settings["aiVerifiedModels"] = {}
        self.settings["aiVerifiedModels"][prov] = model_list
        self.settings["aiConnected"] = True
        self._apply_to_settings()

        self.status_lbl.setText(
            f"✅ API Doğrulandı! Kullanıma hazır {len(model_list)} ana model listelendi."
        )
        self.status_lbl.setStyleSheet("color: #34d399; font-size: 11px;")
        self.config_saved.emit()
        self.model_combo.showPopup()

    def _load_current(self):
        prov = self.settings.get("aiProvider", "gemini")
        idx = next((i for i, k in enumerate(AI_PROVIDERS.keys()) if k == prov), 0)
        self.provider_combo.blockSignals(True)
        self.provider_combo.setCurrentIndex(idx)
        self.provider_combo.blockSignals(False)

        self._populate_provider_fields(prov)
        saved_model = self.settings.get("aiModel", "")
        if saved_model and "2.5-pro" not in saved_model:
            if self.model_combo.findText(saved_model) == -1:
                self.model_combo.insertItem(0, saved_model)
            self.model_combo.setCurrentText(saved_model)

        self.chk_vision.setChecked(bool(self.settings.get("aiSendImage", True)))
        self.chk_auto.setChecked(bool(self.settings.get("aiAutoRun", False)))

        if is_ai_currently_connected(self.settings):
            self.status_lbl.setText(f"✅ API Bağlı (Aktif Model: {self.settings.get('aiModel')})")
            self.status_lbl.setStyleSheet("color: #34d399; font-size: 11px;")

    def _on_provider_changed(self):
        prov = self.provider_combo.currentData()
        self._populate_provider_fields(prov)

    def _populate_provider_fields(self, prov: str):
        info = AI_PROVIDERS.get(prov, AI_PROVIDERS["gemini"])
        keys_dict = self.settings.get("aiApiKeys", {})
        self.api_key_input.setText(keys_dict.get(prov, ""))

        verified = self.settings.get("aiVerifiedModels", {}).get(prov, [])
        model_list = verified if verified else info["models"]

        self.model_combo.clear()
        for m in model_list:
            self.model_combo.addItem(m)
        self.model_combo.setCurrentText(model_list[0] if model_list else info["default_model"])

        if prov in ("custom", "ollama"):
            self.url_label.show()
            self.url_input.show()
            self.url_input.setText(self.settings.get("aiCustomUrl", info["base_url"]))
        else:
            self.url_label.hide()
            self.url_input.hide()

    def _apply_to_settings(self):
        prov = self.provider_combo.currentData()
        self.settings["aiProvider"] = prov
        if "aiApiKeys" not in self.settings or not isinstance(self.settings["aiApiKeys"], dict):
            self.settings["aiApiKeys"] = {}
        key_val = self.api_key_input.text().strip()
        self.settings["aiApiKeys"][prov] = key_val
        self.settings["aiModel"] = self.model_combo.currentText().strip()
        if prov in ("custom", "ollama"):
            self.settings["aiCustomUrl"] = self.url_input.text().strip()
        self.settings["aiSendImage"] = self.chk_vision.isChecked()
        self.settings["aiAutoRun"] = self.chk_auto.isChecked()
        if prov != "ollama" and not key_val:
            self.settings["aiConnected"] = False
        StorageManager.save(self.settings)

    def _verify_api_live(self) -> bool:
        self._apply_to_settings()
        prov = self.settings.get("aiProvider", "gemini")
        key_val = self.settings.get("aiApiKeys", {}).get(prov, "").strip()
        if prov != "ollama" and not key_val:
            self.settings["aiConnected"] = False
            StorageManager.save(self.settings)
            self.status_lbl.setText("⚠️ API anahtarı boş. Bağlantı aktif değil.")
            self.status_lbl.setStyleSheet("color: #fbbf24; font-size: 11px;")
            return False

        self.status_lbl.setText("⏳ API bağlantısı doğrulanıyor...")
        self.status_lbl.setStyleSheet("color: #fbbf24; font-size: 11px;")
        QApplication.processEvents()

        try:
            self.engine._call_ai_provider(
                self.settings,
                ocr_text="",
                user_prompt="Sadece 'OK' yaz.",
                image_b64=None
            )
            self.settings["aiConnected"] = True
            StorageManager.save(self.settings)
            self.model_combo.setCurrentText(self.settings.get("aiModel", ""))
            self.status_lbl.setText(f"✅ API Bağlandı! Aktif Model: {self.settings['aiModel']}")
            self.status_lbl.setStyleSheet("color: #34d399; font-size: 11px;")
            return True
        except Exception as e:
            self.settings["aiConnected"] = False
            StorageManager.save(self.settings)
            self.status_lbl.setText(f"❌ {str(e)}")
            self.status_lbl.setStyleSheet("color: #f87171; font-size: 11px;")
            return False

    def _test_connection(self):
        self._verify_api_live()
        self.config_saved.emit()

    def _save_and_close(self):
        prov = self.provider_combo.currentData()
        key_val = self.api_key_input.text().strip()
        if prov == "ollama" or key_val:
            ok = self._verify_api_live()
            self.config_saved.emit()
            if ok:
                self.accept()
        else:
            self._apply_to_settings()
            self.settings["aiConnected"] = False
            StorageManager.save(self.settings)
            self.config_saved.emit()
            self.accept()


# ---------------------------------------------------------------------------
# 7. SAĞ ALT "AKTİF" ROZETİ VE YAKALAMA KALKANI
# ---------------------------------------------------------------------------
class StatusPillWidget(QWidget):
    clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        pill = QFrame()
        pill.setStyleSheet("""
            QFrame {
                background-color: #0f172a;
                border: 1px solid #38bdf8;
                border-radius: 13px;
            }
        """)
        p_layout = QHBoxLayout(pill)
        p_layout.setContentsMargins(12, 6, 12, 6)
        p_layout.setSpacing(6)

        dot = QLabel()
        dot.setFixedSize(7, 7)
        dot.setStyleSheet("background-color: #22c55e; border-radius: 3px; border: none;")

        text_lbl = QLabel("Seçim Modu Aktif")
        text_lbl.setStyleSheet("color: #f8fafc; font-size: 11px; font-weight: 600; border: none;")

        p_layout.addWidget(dot)
        p_layout.addWidget(text_lbl)
        layout.addWidget(pill)

        self.adjustSize()
        self.place_bottom_right()

    def place_bottom_right(self):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 16, screen.bottom() - self.height() - 16)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class CaptureShieldOverlay(QWidget):
    capture_completed = pyqtSignal(QRect, QPixmap, str, str, str, str)
    cancelled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self.start_pt = None
        self.current_pt = None
        self.is_dragging = False
        self.is_hold_mode = False
        self.desktop_snapshot = None
        self.captured_timestamp = ""

        self.drag_poll_timer = QTimer(self)
        self.drag_poll_timer.setInterval(15)
        self.drag_poll_timer.timeout.connect(self._poll_cursor_during_hold)

    def activate_shield(self, start_immediately: bool = False):
        cursor_global = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_global) or QGuiApplication.primaryScreen()
        self.desktop_snapshot = screen.grabWindow(0)
        self.captured_timestamp = datetime.now().strftime("%H:%M:%S")

        self.setGeometry(screen.geometry())
        force_bring_to_front(self)

        if start_immediately:
            local_pt = self.mapFromGlobal(cursor_global)
            self.start_pt = local_pt
            self.current_pt = local_pt
            self.is_dragging = True
            self.is_hold_mode = True
            self.drag_poll_timer.start()
            self.update()
        else:
            self.start_pt = None
            self.current_pt = None
            self.is_dragging = False
            self.is_hold_mode = False
            self.drag_poll_timer.stop()

    def _poll_cursor_during_hold(self):
        if not self.isVisible() or not self.is_hold_mode:
            self.drag_poll_timer.stop()
            return

        if not is_left_mouse_pressed():
            self.drag_poll_timer.stop()
            self.finish_hold_drag()
            return

        new_pt = self.mapFromGlobal(QCursor.pos())
        if new_pt != self.current_pt:
            self.current_pt = new_pt
            self.update()

    def finish_hold_drag(self):
        self.drag_poll_timer.stop()
        if not self.is_dragging or not self.start_pt or not self.current_pt:
            self.hide()
            return
        self.is_dragging = False
        self.is_hold_mode = False
        rect = QRect(self.start_pt, self.current_pt).normalized()
        self.hide()
        if rect.width() >= 10 and rect.height() >= 10:
            self._crop_and_enhance(rect)
        else:
            self.cancelled.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pt = event.pos()
            self.current_pt = event.pos()
            self.is_dragging = True
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self.drag_poll_timer.stop()
            self.hide()
            self.cancelled.emit()

    def mouseMoveEvent(self, event):
        if self.is_dragging:
            self.current_pt = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_dragging:
            self.drag_poll_timer.stop()
            self.is_dragging = False
            self.is_hold_mode = False
            rect = QRect(self.start_pt, self.current_pt).normalized()
            self.hide()
            if rect.width() >= 10 and rect.height() >= 10:
                self._crop_and_enhance(rect)
            else:
                self.cancelled.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.drag_poll_timer.stop()
            self.hide()
            self.cancelled.emit()

    def _crop_and_enhance(self, rect: QRect):
        if not self.desktop_snapshot or self.desktop_snapshot.isNull():
            return

        scale_x = self.desktop_snapshot.width() / max(1, self.width())
        scale_y = self.desktop_snapshot.height() / max(1, self.height())

        raw_crop = QRect(
            int(rect.x() * scale_x),
            int(rect.y() * scale_y),
            int(rect.width() * scale_x),
            int(rect.height() * scale_y)
        ).intersected(QRect(0, 0, self.desktop_snapshot.width(), self.desktop_snapshot.height()))

        if raw_crop.width() < 5 or raw_crop.height() < 5:
            self.cancelled.emit()
            return

        cropped_pm = self.desktop_snapshot.copy(raw_crop)

        target_w = min(1500, cropped_pm.width() * 2)
        target_h = min(1500, cropped_pm.height() * 2)
        upscaled_pm = cropped_pm.scaled(
            target_w, target_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )

        buf1 = QBuffer()
        buf1.open(QIODevice.OpenModeFlag.WriteOnly)
        upscaled_pm.save(buf1, "JPG", quality=88)
        b64_normal = bytes(buf1.data().toBase64()).decode("utf-8")

        qimg = upscaled_pm.toImage().convertToFormat(QImage.Format.Format_RGB32)
        sample = qimg.scaled(16, 16)
        total_luma = 0
        for y in range(sample.height()):
            for x in range(sample.width()):
                c = sample.pixelColor(x, y)
                total_luma += (c.red() * 299 + c.green() * 587 + c.blue() * 114) // 1000
        avg_luma = total_luma / max(1, sample.width() * sample.height())

        qimg.invertPixels(QImage.InvertMode.InvertRgb)
        buf2 = QBuffer()
        buf2.open(QIODevice.OpenModeFlag.WriteOnly)
        qimg.save(buf2, "JPG", quality=88)
        b64_inverted = bytes(buf2.data().toBase64()).decode("utf-8")

        if avg_luma < 110:
            ocr_primary, ocr_secondary = b64_inverted, b64_normal
        else:
            ocr_primary, ocr_secondary = b64_normal, b64_inverted

        global_rect = QRect(self.mapToGlobal(rect.topLeft()), rect.size())
        self.capture_completed.emit(
            global_rect,
            cropped_pm,
            b64_normal,
            ocr_primary,
            ocr_secondary,
            self.captured_timestamp
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.desktop_snapshot:
            painter.drawPixmap(self.rect(), self.desktop_snapshot)

        if self.start_pt and self.current_pt:
            sel = QRect(self.start_pt, self.current_pt).normalized()
            painter.fillRect(sel, QColor(56, 189, 248, 45))
            pen = QPen(QColor("#38bdf8"), 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(sel)


# ---------------------------------------------------------------------------
# 8. MODÜLER SONUÇ PENCERESİ (Anında Çeviri Dili Değişimi + Doğru Dil Algılama)
# ---------------------------------------------------------------------------
class ResultPanelWindow(QWidget):
    history_updated = pyqtSignal()
    open_settings_requested = pyqtSignal()
    layout_settings_changed = pyqtSignal()

    def __init__(self, engine: BackgroundEngine, settings: dict):
        super().__init__()
        self.engine = engine
        self.settings = settings
        self.current_pixmap = None
        self.current_b64 = ""
        self.last_capture_rect = None
        self.captured_time = ""
        self.detected_src_lang = "en"
        self.detected_url = None
        self.is_code_mode = False
        self.drag_offset = None
        self.active_tab_key = "ocr"

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setMinimumSize(410, 290)

        saved_size = self.settings.get("savedPanelSize", [465, 445])
        self.resize(int(saved_size[0]), int(saved_size[1]))

        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(300)
        self.debounce_timer.timeout.connect(self._trigger_retranslate)

        self._init_ui()
        self.sync_pin_state()
        self.apply_modular_layout()

        self.engine.ocr_finished.connect(self._on_ocr_result)
        self.engine.retranslate_finished.connect(self._on_retranslate_result)
        self.engine.lens_finished.connect(self._on_lens_finished)
        self.engine.ai_finished.connect(self._on_ai_result)
        self.engine.tts.state_changed.connect(self._on_tts_state_changed)

    def _init_ui(self):
        self.setStyleSheet("background-color: #0f172a;")
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self.panel = QFrame()
        self.panel.setObjectName("TakoliPanel")
        self.panel.setStyleSheet("""
            QFrame#TakoliPanel {
                background-color: #0f172a;
                border: 2px solid #334155;
                border-radius: 8px;
            }
            QLabel { color: #f8fafc; border: none; }
            QTextEdit, QLineEdit {
                background-color: #1e293b;
                color: #f1f5f9;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px;
                font-size: 13px;
            }
            QTextEdit:focus, QLineEdit:focus { border: 1px solid #38bdf8; }
            QComboBox {
                background-color: #1e293b;
                color: #e2e8f0;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 11px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e293b;
                color: #f8fafc;
                selection-background-color: #0284c7;
            }
            QPushButton.iconBtn {
                background-color: #1e293b;
                color: #cbd5e1;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton.iconBtn:hover { background-color: #334155; color: #ffffff; }
            QPushButton.actionBtn {
                background-color: #1e293b;
                color: #e2e8f0;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 9px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton.actionBtn:hover { background-color: #334155; }
            QPushButton.primaryBtn {
                background-color: #0284c7;
                border: 1px solid #0369a1;
                color: #ffffff;
                border-radius: 6px;
                padding: 6px 9px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton.primaryBtn:hover { background-color: #0369a1; }
            QPushButton.lensBtn {
                background-color: #4f46e5;
                border: 1px solid #4338ca;
                color: #ffffff;
                border-radius: 6px;
                padding: 6px 9px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton.lensBtn:hover { background-color: #4338ca; }
            QPushButton.aiChip {
                background-color: #1e1b4b;
                border: 1px solid #4338ca;
                color: #c7d2fe;
                border-radius: 5px;
                padding: 4px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton.aiChip:hover { background-color: #312e81; color: #ffffff; }
        """)

        p_layout = QVBoxLayout(self.panel)
        p_layout.setContentsMargins(12, 10, 12, 6)
        p_layout.setSpacing(7)

        # 1. ÜST BAŞLIK
        header = QHBoxLayout()
        header.setSpacing(4)
        brand_group = QHBoxLayout()
        brand_group.setSpacing(6)

        logo_lbl = QLabel()
        logo_lbl.setPixmap(get_app_logo(19))
        brand_txt = QLabel("TakoliCopy")
        brand_txt.setStyleSheet("color: #38bdf8; font-size: 13px; font-weight: 800;")

        self.time_badge = QLabel()
        self.time_badge.setStyleSheet(
            "background-color: #1e293b; color: #fbbf24; border: 1px solid #334155; "
            "font-size: 10px; font-weight: 600; padding: 2px 5px; border-radius: 4px;"
        )

        self.auto_copy_badge = QLabel("✓ Kopyalandı")
        self.auto_copy_badge.setStyleSheet(
            "background-color: #065f46; color: #6ee7b7; font-size: 10px; "
            "font-weight: 600; padding: 2px 5px; border-radius: 4px;"
        )
        self.auto_copy_badge.hide()

        brand_group.addWidget(logo_lbl)
        brand_group.addWidget(brand_txt)
        brand_group.addWidget(self.time_badge)
        brand_group.addWidget(self.auto_copy_badge)
        brand_group.addStretch()

        self.btn_modules = QPushButton("🧩 Bölümler")
        self.btn_modules.setProperty("class", "iconBtn")
        self.btn_modules.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_modules.setToolTip("Ana pencerede görünecek bölümleri (Metin, Çeviri, AI) ekle/çıkar")
        self.btn_modules.clicked.connect(self._show_modules_menu)

        self.btn_settings = QPushButton("⚙️")
        self.btn_settings.setProperty("class", "iconBtn")
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setToolTip("Ayarlar ve Geçmiş Menüsü")
        self.btn_settings.clicked.connect(self.open_settings_requested.emit)

        self.btn_pin = QPushButton("📌 Sabitle")
        self.btn_pin.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pin.clicked.connect(self._toggle_pin)

        close_btn = QPushButton("✕")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: none; color: #94a3b8;
                font-size: 15px; padding: 2px 6px; border-radius: 4px;
            }
            QPushButton:hover { background: #1e293b; color: #ffffff; }
        """)
        close_btn.clicked.connect(self._on_close_clicked)

        header.addLayout(brand_group)
        header.addWidget(self.btn_modules)
        header.addWidget(self.btn_settings)
        header.addWidget(self.btn_pin)
        header.addWidget(close_btn)
        p_layout.addLayout(header)

        # 2. GÖRSEL ÖNİZLEME VE GOOGLE LENS ÇUBUĞU
        self.visual_bar = QFrame()
        self.visual_bar.setFixedHeight(48)
        self.visual_bar.setStyleSheet("background-color: #1e293b; border: 1px solid #334155; border-radius: 8px;")
        vb_layout = QHBoxLayout(self.visual_bar)
        vb_layout.setContentsMargins(8, 5, 8, 5)
        vb_layout.setSpacing(8)

        self.thumb_preview = QLabel()
        self.thumb_preview.setFixedSize(46, 34)
        self.thumb_preview.setStyleSheet("border: 1px solid #475569; border-radius: 4px; background-color: #090d16;")
        self.thumb_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_lens = QPushButton("🔍 Görseli Ara (Lens)")
        self.btn_lens.setProperty("class", "lensBtn")
        self.btn_lens.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_lens.clicked.connect(self._on_lens_clicked)

        self.btn_copy_img = QPushButton("🖼️ Resmi Kopyala")
        self.btn_copy_img.setProperty("class", "actionBtn")
        self.btn_copy_img.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_img.clicked.connect(self._on_copy_img_clicked)

        vb_layout.addWidget(self.thumb_preview)
        vb_layout.addWidget(self.btn_lens, 1)
        vb_layout.addWidget(self.btn_copy_img, 1)
        p_layout.addWidget(self.visual_bar)

        # 3. SEKME & GÖRÜNÜM ÇUBUĞU
        self.nav_bar = QFrame()
        self.nav_bar.setFixedHeight(34)
        self.nav_bar.setStyleSheet("background-color: #131c31; border: 1px solid #1e293b; border-radius: 6px;")
        nav_layout = QHBoxLayout(self.nav_bar)
        nav_layout.setContentsMargins(4, 3, 4, 3)
        nav_layout.setSpacing(4)

        self.tab_btn_ocr = QPushButton("📝 Metin")
        self.tab_btn_ocr.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tab_btn_ocr.clicked.connect(lambda: self._switch_tab("ocr"))

        self.tab_btn_trans = QPushButton("🌍 Çeviri")
        self.tab_btn_trans.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tab_btn_trans.clicked.connect(lambda: self._switch_tab("translate"))

        self.tab_btn_ai = QPushButton("🤖 Yapay Zeka")
        self.tab_btn_ai.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tab_btn_ai.clicked.connect(lambda: self._switch_tab("ai"))

        self.btn_view_mode = QPushButton("🗂️ Sekmeli")
        self.btn_view_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_mode.setToolTip("Sekmeli Görünüm (Kompakt) ile Alt Alta Birleşik Görünüm arasında geçiş yap")
        self.btn_view_mode.setStyleSheet(
            "QPushButton { background-color: #0f172a; color: #94a3b8; border: 1px solid #334155; "
            "border-radius: 4px; padding: 2px 7px; font-size: 10px; font-weight: 600; }"
            "QPushButton:hover { color: #ffffff; border-color: #38bdf8; }"
        )
        self.btn_view_mode.clicked.connect(self._toggle_layout_mode)

        nav_layout.addWidget(self.tab_btn_ocr, 1)
        nav_layout.addWidget(self.tab_btn_trans, 1)
        nav_layout.addWidget(self.tab_btn_ai, 1)
        nav_layout.addWidget(self.btn_view_mode)
        p_layout.addWidget(self.nav_bar)

        # 4. YÜKLENİYOR DURUM YAZISI
        self.status_lbl = QLabel("Metin okunuyor ve çevriliyor...")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setStyleSheet("color: #38bdf8; font-size: 12px; padding: 8px 4px;")
        p_layout.addWidget(self.status_lbl)

        # 5. ANA İÇERİK KONTEYNERİ
        self.main_body = QWidget()
        self.main_body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        body_layout = QVBoxLayout(self.main_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)

        # --- BÖLÜM A: YAKALANAN METİN (ALGILANAN DİL GÖSTERGELİ) ---
        self.section_ocr = QWidget()
        ocr_layout = QVBoxLayout(self.section_ocr)
        ocr_layout.setContentsMargins(0, 0, 0, 0)
        ocr_layout.setSpacing(4)

        orig_top = QHBoxLayout()
        orig_top.setSpacing(6)
        orig_lbl = QLabel("📝 Yakalanan Metin")
        orig_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 700;")

        self.detected_lang_badge = QLabel("🌐 Algılanan Dil: -")
        self.detected_lang_badge.setStyleSheet(
            "background-color: #1e293b; color: #38bdf8; border: 1px solid #334155; "
            "border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 700;"
        )

        self.btn_speak_orig = QPushButton("🔊 Oku")
        self.btn_speak_orig.setProperty("class", "iconBtn")
        self.btn_speak_orig.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_speak_orig.setToolTip("Algılanan dilin kendi orijinal sesiyle okur")
        self.btn_speak_orig.clicked.connect(self._on_speak_orig_clicked)

        self.btn_code_mode = QPushButton("</> Kod")
        self.btn_code_mode.setProperty("class", "iconBtn")
        self.btn_code_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_code_mode.clicked.connect(self._on_code_mode_clicked)

        self.btn_copy_orig = QPushButton("📋 Kopyala")
        self.btn_copy_orig.setProperty("class", "iconBtn")
        self.btn_copy_orig.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_orig.clicked.connect(
            lambda: self._copy_with_feedback(self.btn_copy_orig, self.ocr_input.toPlainText())
        )

        orig_top.addWidget(orig_lbl)
        orig_top.addWidget(self.detected_lang_badge)
        orig_top.addStretch()
        orig_top.addWidget(self.btn_speak_orig)
        orig_top.addWidget(self.btn_code_mode)
        orig_top.addWidget(self.btn_copy_orig)
        ocr_layout.addLayout(orig_top)

        self.ocr_input = QTextEdit()
        self.ocr_input.setMinimumHeight(60)
        self.ocr_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.ocr_input.textChanged.connect(lambda: self.debounce_timer.start())
        ocr_layout.addWidget(self.ocr_input, 1)

        ocr_bottom = QHBoxLayout()
        ocr_bottom.setSpacing(6)
        self.btn_copy_time = QPushButton("⏱ Not Kopyala")
        self.btn_copy_time.setProperty("class", "actionBtn")
        self.btn_copy_time.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_time.clicked.connect(self._on_copy_time_note)

        self.btn_open_url = QPushButton("🔗 Linke Git")
        self.btn_open_url.setProperty("class", "actionBtn")
        self.btn_open_url.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_url.clicked.connect(self._on_open_url)
        self.btn_open_url.hide()

        self.btn_search = QPushButton("🌐 Web'de Ara")
        self.btn_search.setProperty("class", "actionBtn")
        self.btn_search.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_search.clicked.connect(self._on_web_search)

        self.btn_send_to_ai = QPushButton("🤖 AI ile İncele")
        self.btn_send_to_ai.setProperty("class", "primaryBtn")
        self.btn_send_to_ai.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_send_to_ai.clicked.connect(lambda: self._run_ai_quick("Bu içeriği detaylıca açıkla ve özetle."))

        ocr_bottom.addWidget(self.btn_copy_time)
        ocr_bottom.addWidget(self.btn_open_url)
        ocr_bottom.addWidget(self.btn_search)
        ocr_bottom.addWidget(self.btn_send_to_ai)
        ocr_layout.addLayout(ocr_bottom)

        body_layout.addWidget(self.section_ocr, 1)

        # --- BÖLÜM B: ANINDA ÇEVİRİ ---
        self.section_translate = QWidget()
        trans_layout = QVBoxLayout(self.section_translate)
        trans_layout.setContentsMargins(0, 0, 0, 0)
        trans_layout.setSpacing(4)

        trans_top = QHBoxLayout()
        trans_lbl = QLabel("🌍 Anında Çeviri")
        trans_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 700;")

        self.btn_speak_trans = QPushButton("🔊 Dinle")
        self.btn_speak_trans.setProperty("class", "iconBtn")
        self.btn_speak_trans.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_speak_trans.clicked.connect(self._on_speak_trans_clicked)

        self.lang_select = QComboBox()
        self.languages = [
            ("tr", "Türkçe"), ("en", "İngilizce"), ("de", "Almanca"),
            ("fr", "Fransızca"), ("es", "İspanyolca"), ("ru", "Rusça")
        ]
        for code, label in self.languages:
            self.lang_select.addItem(label, code)
        # Hedef dil değiştirildiği anda beklemeden anında çeviriyi tetikle!
        self.lang_select.currentIndexChanged.connect(self._on_target_lang_combo_changed)

        self.btn_copy_trans = QPushButton("📋 Çeviriyi Kopyala")
        self.btn_copy_trans.setProperty("class", "iconBtn")
        self.btn_copy_trans.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_trans.clicked.connect(
            lambda: self._copy_with_feedback(self.btn_copy_trans, self.translated_output.toPlainText())
        )

        trans_top.addWidget(trans_lbl)
        trans_top.addStretch()
        trans_top.addWidget(self.btn_speak_trans)
        trans_top.addWidget(self.lang_select)
        trans_top.addWidget(self.btn_copy_trans)
        trans_layout.addLayout(trans_top)

        self.translated_output = QTextEdit()
        self.translated_output.setReadOnly(True)
        self.translated_output.setMinimumHeight(60)
        self.translated_output.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.translated_output.setStyleSheet("""
            QTextEdit {
                background-color: #172554;
                border: 1px solid #1e3a8a;
                color: #e0f2fe;
                border-radius: 6px;
                padding: 6px;
                font-size: 13px;
            }
        """)
        trans_layout.addWidget(self.translated_output, 1)

        body_layout.addWidget(self.section_translate, 1)

        # --- BÖLÜM C: YAPAY ZEKA (AI) SONUCU ---
        self.section_ai = QWidget()
        ai_layout = QVBoxLayout(self.section_ai)
        ai_layout.setContentsMargins(0, 0, 0, 0)
        ai_layout.setSpacing(5)

        ai_top = QHBoxLayout()
        self.ai_header_lbl = QLabel("🤖 Yapay Zeka Sonucu")
        self.ai_header_lbl.setStyleSheet("color: #a5b4fc; font-size: 11px; font-weight: 700;")

        self.chk_ai_vision = QCheckBox("👁️ Görseli Gönder")
        self.chk_ai_vision.setToolTip("Seçilen ekran görüntüsünü de AI modeline gönderir")
        self.chk_ai_vision.setStyleSheet("color: #94a3b8; font-size: 10px;")
        self.chk_ai_vision.setChecked(bool(self.settings.get("aiSendImage", True)))
        self.chk_ai_vision.stateChanged.connect(self._on_vision_toggle)

        self.btn_ai_api_cfg = QPushButton("🔑 API Ayarla")
        self.btn_ai_api_cfg.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ai_api_cfg.clicked.connect(self.open_ai_config_dialog)

        self.btn_copy_ai = QPushButton("📋 Kopyala")
        self.btn_copy_ai.setProperty("class", "iconBtn")
        self.btn_copy_ai.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_ai.clicked.connect(
            lambda: self._copy_with_feedback(self.btn_copy_ai, self.ai_output.toPlainText())
        )

        ai_top.addWidget(self.ai_header_lbl)
        ai_top.addStretch()
        ai_top.addWidget(self.chk_ai_vision)
        ai_top.addWidget(self.btn_ai_api_cfg)
        ai_top.addWidget(self.btn_copy_ai)
        ai_layout.addLayout(ai_top)

        chips_row = QHBoxLayout()
        chips_row.setSpacing(4)
        quick_tasks = [
            ("💡 Açıkla", "Bu metni ve görseli anlaşılır şekilde açıkla ve özetle."),
            ("🛠️ Kodu Çöz", "Bu kodu analiz et, ne işe yaradığını anlat ve varsa hatasını düzelt."),
            ("❓ Soruyu Çöz", "Ekrandaki soruyu adım adım çöz ve doğru cevabı belirt."),
            ("✍️ Yanıt Yaz", "Bu mesaja/içeriğe uygun profesyonel bir yanıt hazırla.")
        ]
        for label, prompt_txt in quick_tasks:
            b = QPushButton(label)
            b.setProperty("class", "aiChip")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda checked, p=prompt_txt: self._run_ai_quick(p))
            chips_row.addWidget(b)
        ai_layout.addLayout(chips_row)

        self.ai_output = QTextEdit()
        self.ai_output.setReadOnly(True)
        self.ai_output.setMinimumHeight(65)
        self.ai_output.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.ai_output.setPlaceholderText(
            "🤖 Üstteki hızlı butonlardan birine tıklayın veya alttaki kutudan AI'ya istediğiniz soruyu sorun..."
        )
        self.ai_output.setStyleSheet("""
            QTextEdit {
                background-color: #111827;
                border: 1px solid #3730a3;
                color: #e0e7ff;
                border-radius: 6px;
                padding: 7px;
                font-size: 13px;
            }
        """)
        ai_layout.addWidget(self.ai_output, 1)

        ask_row = QHBoxLayout()
        ask_row.setSpacing(5)
        self.ai_prompt_input = QLineEdit()
        self.ai_prompt_input.setPlaceholderText("Seçilen ekran/metin hakkında AI'ya özel soru sor (Enter)...")
        self.ai_prompt_input.returnPressed.connect(self._on_ask_custom_ai)

        self.btn_ask_ai = QPushButton("🚀 Sor")
        self.btn_ask_ai.setProperty("class", "lensBtn")
        self.btn_ask_ai.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ask_ai.clicked.connect(self._on_ask_custom_ai)

        ask_row.addWidget(self.ai_prompt_input, 1)
        ask_row.addWidget(self.btn_ask_ai)
        ai_layout.addLayout(ask_row)

        body_layout.addWidget(self.section_ai, 1)

        p_layout.addWidget(self.main_body, 1)

        # 6. SAĞ ALT BOYUTLANDIRMA TUTAMACI
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        self.grip_hint = QLabel("↘ Boyutlandır")
        self.grip_hint.setStyleSheet("color: #64748b; font-size: 10px;")
        grip_row.addStretch()
        grip_row.addWidget(self.grip_hint)
        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(14, 14)
        grip_row.addWidget(self.size_grip)
        p_layout.addLayout(grip_row)

        root_layout.addWidget(self.panel)

    def _update_detected_lang_ui(self, lang_code: str):
        if not lang_code:
            self.detected_lang_badge.setText("🌐 Algılanan Dil: Belirsiz")
            return
        self.detected_src_lang = lang_code
        lang_name = get_language_display_name(lang_code)
        self.detected_lang_badge.setText(f"🌐 Algılanan Dil: {lang_name}")
        self.btn_speak_orig.setToolTip(f"🔊 {lang_name} seslendiricisi ile oku")

    def _update_ai_button_badge(self):
        if is_ai_currently_connected(self.settings):
            self.btn_ai_api_cfg.setText("✅ API Bağlı")
            self.btn_ai_api_cfg.setStyleSheet(
                "QPushButton { background-color: #065f46; color: #6ee7b7; border: 1px solid #10b981; "
                "border-radius: 4px; padding: 3px 8px; font-size: 11px; font-weight: 700; }"
                "QPushButton:hover { background-color: #047857; color: #ffffff; }"
            )
        else:
            self.btn_ai_api_cfg.setText("🔑 API Ayarla")
            self.btn_ai_api_cfg.setStyleSheet(
                "QPushButton { background-color: #1e293b; color: #cbd5e1; border: 1px solid #334155; "
                "border-radius: 4px; padding: 3px 8px; font-size: 11px; font-weight: 600; }"
                "QPushButton:hover { background-color: #334155; color: #ffffff; }"
            )

    def _show_modules_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid #38bdf8;
                border-radius: 6px;
                padding: 4px;
                font-size: 12px;
            }
            QMenu::item { padding: 6px 14px; border-radius: 4px; }
            QMenu::item:selected { background-color: #0284c7; }
        """)

        act_vis = QAction("🖼️ Görsel & Lens Çubuğunu Göster", menu, checkable=True)
        act_vis.setChecked(bool(self.settings.get("showVisualBar", True)))
        act_vis.triggered.connect(lambda v: self._set_module_visibility("showVisualBar", v))
        menu.addAction(act_vis)

        act_ocr = QAction("📝 Yakalanan Metin Bölümünü Göster", menu, checkable=True)
        act_ocr.setChecked(bool(self.settings.get("showOcrSection", True)))
        act_ocr.triggered.connect(lambda v: self._set_module_visibility("showOcrSection", v))
        menu.addAction(act_ocr)

        act_tr = QAction("🌍 Anında Çeviri Bölümünü Göster", menu, checkable=True)
        act_tr.setChecked(bool(self.settings.get("showTranslateSection", True)))
        act_tr.triggered.connect(lambda v: self._set_module_visibility("showTranslateSection", v))
        menu.addAction(act_tr)

        act_ai = QAction("🤖 Yapay Zeka (AI) Bölümünü Göster", menu, checkable=True)
        act_ai.setChecked(bool(self.settings.get("showAiSection", True)))
        act_ai.triggered.connect(lambda v: self._set_module_visibility("showAiSection", v))
        menu.addAction(act_ai)

        menu.addSeparator()

        is_tabbed = self.settings.get("layoutMode", "tabbed") == "tabbed"
        act_mode = QAction("🗂️ Sekmeli Görünüm (Pencere Şişmez)" if not is_tabbed else "📋 Alt Alta Birleşik Görünüm", menu)
        act_mode.triggered.connect(self._toggle_layout_mode)
        menu.addAction(act_mode)

        act_api = QAction("🔑 Yapay Zeka API Ayarları...", menu)
        act_api.triggered.connect(self.open_ai_config_dialog)
        menu.addAction(act_api)

        menu.exec(self.btn_modules.mapToGlobal(QPoint(0, self.btn_modules.height() + 4)))

    def _set_module_visibility(self, key: str, value: bool):
        self.settings[key] = bool(value)
        if not any([
            self.settings.get("showOcrSection"),
            self.settings.get("showTranslateSection"),
            self.settings.get("showAiSection")
        ]):
            self.settings["showOcrSection"] = True
        StorageManager.save(self.settings)
        self.apply_modular_layout()
        self.layout_settings_changed.emit()

    def _toggle_layout_mode(self):
        cur = self.settings.get("layoutMode", "tabbed")
        self.settings["layoutMode"] = "stacked" if cur == "tabbed" else "tabbed"
        StorageManager.save(self.settings)
        self.apply_modular_layout()
        self.layout_settings_changed.emit()

    def _switch_tab(self, tab_key: str):
        self.active_tab_key = tab_key
        if self.settings.get("layoutMode", "tabbed") != "tabbed":
            self.settings["layoutMode"] = "tabbed"
            StorageManager.save(self.settings)
        self.apply_modular_layout()

    def apply_modular_layout(self):
        show_vis = bool(self.settings.get("showVisualBar", True))
        show_ocr = bool(self.settings.get("showOcrSection", True))
        show_tr = bool(self.settings.get("showTranslateSection", True))
        show_ai = bool(self.settings.get("showAiSection", True))
        mode = self.settings.get("layoutMode", "tabbed")

        self.visual_bar.setVisible(show_vis)
        self.tab_btn_ocr.setVisible(show_ocr)
        self.tab_btn_trans.setVisible(show_tr)
        self.tab_btn_ai.setVisible(show_ai)

        enabled_tabs = []
        if show_ocr: enabled_tabs.append("ocr")
        if show_tr: enabled_tabs.append("translate")
        if show_ai: enabled_tabs.append("ai")
        if self.active_tab_key not in enabled_tabs and enabled_tabs:
            self.active_tab_key = enabled_tabs[0]

        prov = self.settings.get("aiProvider", "gemini")
        model = self.settings.get("aiModel", "")
        self.ai_header_lbl.setText(f"🤖 Yapay Zeka ({prov.upper()} • {model[:22]})")
        self._update_ai_button_badge()

        if mode == "tabbed":
            self.btn_view_mode.setText("🗂️ Sekmeli")
            self.section_ocr.setVisible(show_ocr and self.active_tab_key == "ocr")
            self.section_translate.setVisible(show_tr and self.active_tab_key == "translate")
            self.section_ai.setVisible(show_ai and self.active_tab_key == "ai")
        else:
            self.btn_view_mode.setText("📋 Birleşik")
            self.section_ocr.setVisible(show_ocr)
            self.section_translate.setVisible(show_tr)
            self.section_ai.setVisible(show_ai)

        self._style_tab_btn(self.tab_btn_ocr, mode == "tabbed" and self.active_tab_key == "ocr")
        self._style_tab_btn(self.tab_btn_trans, mode == "tabbed" and self.active_tab_key == "translate")
        self._style_tab_btn(self.tab_btn_ai, mode == "tabbed" and self.active_tab_key == "ai")

        self.panel.layout().activate()
        self.panel.update()
        self.update()

    @staticmethod
    def _style_tab_btn(btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet(
                "QPushButton { background-color: #0284c7; color: #ffffff; border: 1px solid #38bdf8; "
                "border-radius: 5px; padding: 4px 8px; font-size: 11px; font-weight: 700; }"
            )
        else:
            btn.setStyleSheet(
                "QPushButton { background-color: transparent; color: #94a3b8; border: none; "
                "border-radius: 5px; padding: 4px 8px; font-size: 11px; font-weight: 600; }"
                "QPushButton:hover { background-color: #1e293b; color: #f8fafc; }"
            )

    def open_ai_config_dialog(self):
        dlg = AiConfigDialog(self.settings, self.engine, self)
        dlg.config_saved.connect(lambda: (
            self.chk_ai_vision.setChecked(bool(self.settings.get("aiSendImage", True))),
            self.apply_modular_layout(),
            self.layout_settings_changed.emit()
        ))
        dlg.exec()

    def _on_vision_toggle(self):
        self.settings["aiSendImage"] = self.chk_ai_vision.isChecked()
        StorageManager.save(self.settings)

    def _run_ai_quick(self, prompt_text: str):
        if not self.settings.get("showAiSection", True):
            self.settings["showAiSection"] = True
            StorageManager.save(self.settings)
        self._switch_tab("ai")
        self.ai_output.setPlainText("🤖 Yapay zeka inceliyor, lütfen bekleyin...")
        img_to_send = self.current_b64 if self.settings.get("aiSendImage", True) else None
        self.engine.ask_ai(self.settings, self.ocr_input.toPlainText(), prompt_text, img_to_send)

    def _on_ask_custom_ai(self):
        q = self.ai_prompt_input.text().strip()
        if not q:
            q = "Bu içeriği açıkla."
        self._run_ai_quick(q)

    def _on_ai_result(self, res: dict):
        self.apply_modular_layout()
        self.layout_settings_changed.emit()
        if res.get("error"):
            self.ai_output.setPlainText(f"⚠️ {res['error']}")
        else:
            self.ai_output.setPlainText(res.get("answer", ""))
        self.ai_output.viewport().update()
        self.repaint()

    def sync_pin_state(self):
        pinned = bool(self.settings.get("panelPinned", False))
        if pinned:
            self.btn_pin.setText("🔒 Sabit")
            self.btn_pin.setStyleSheet(
                "QPushButton { background-color: #0284c7; color: #ffffff; border: 1px solid #38bdf8; "
                "border-radius: 4px; padding: 3px 8px; font-size: 11px; font-weight: 700; }"
            )
            self.size_grip.hide()
            self.grip_hint.setText("🔒 Konum ve Boyut Kilitli")
        else:
            self.btn_pin.setText("📌 Sabitle")
            self.btn_pin.setStyleSheet(
                "QPushButton { background-color: #1e293b; color: #94a3b8; border: 1px solid #334155; "
                "border-radius: 4px; padding: 3px 8px; font-size: 11px; font-weight: 600; }"
                "QPushButton:hover { background-color: #334155; color: #ffffff; }"
            )
            self.size_grip.show()
            self.grip_hint.setText("↘ Boyutlandır")

    def _toggle_pin(self):
        pinned = not bool(self.settings.get("panelPinned", False))
        self.settings["panelPinned"] = pinned
        if pinned:
            self.settings["savedPanelPos"] = [self.x(), self.y()]
        StorageManager.save(self.settings)
        self.sync_pin_state()

    def apply_configured_position(self, reference_rect: QRect = None):
        if reference_rect is not None:
            self.last_capture_rect = reference_rect
        ref = reference_rect if reference_rect is not None else self.last_capture_rect

        pos_mode = self.settings.get("panelPosMode", "cursor")

        if pos_mode in ("top_right", "bottom_right", "top_left", "bottom_left", "center"):
            target_pt = compute_window_position(pos_mode, self.width(), self.height(), reference_rect=ref)
            self.move(target_pt)
            self.settings["savedPanelPos"] = [target_pt.x(), target_pt.y()]
            StorageManager.save(self.settings)
            return

        if pos_mode == "remember" or self.settings.get("panelPinned"):
            saved_pos = self.settings.get("savedPanelPos")
            if isinstance(saved_pos, list) and len(saved_pos) == 2:
                target_pt = compute_window_position(
                    "remember", self.width(), self.height(), reference_rect=ref, saved_pos=saved_pos
                )
                self.move(target_pt)
                return

        target_pt = compute_window_position(
            "cursor",
            self.width(),
            self.height(),
            reference_rect=ref,
            saved_pos=self.settings.get("savedPanelPos")
        )
        self.move(target_pt)

    def _on_close_clicked(self):
        self.engine.tts.stop()
        self.hide()

    def _on_speak_orig_clicked(self):
        lang = self.detected_src_lang if self.detected_src_lang else guess_language_locally(self.ocr_input.toPlainText())
        self.engine.tts.toggle_speak(self.ocr_input.toPlainText(), lang, "orig")

    def _on_speak_trans_clicked(self):
        lang = self.lang_select.currentData() or "tr"
        self.engine.tts.toggle_speak(self.translated_output.toPlainText(), lang, "trans")

    def _on_tts_state_changed(self, playing: bool, target_id: str):
        if playing and target_id == "orig":
            self.btn_speak_orig.setText("⏹️ Durdur")
            self.btn_speak_orig.setStyleSheet(
                "background-color: #dc2626; color: #fff; border: 1px solid #ef4444; border-radius: 4px; padding: 2px 7px; font-size: 11px; font-weight: 700;"
            )
        else:
            self.btn_speak_orig.setText("🔊 Oku")
            self.btn_speak_orig.setStyleSheet("")

        if playing and target_id == "trans":
            self.btn_speak_trans.setText("⏹️ Durdur")
            self.btn_speak_trans.setStyleSheet(
                "background-color: #dc2626; color: #fff; border: 1px solid #ef4444; border-radius: 4px; padding: 2px 7px; font-size: 11px; font-weight: 700;"
            )
        else:
            self.btn_speak_trans.setText("🔊 Dinle")
            self.btn_speak_trans.setStyleSheet("")
        self.panel.update()

    def render_result_panel(
        self,
        rect: QRect,
        pixmap: QPixmap,
        normal_b64: str,
        ocr_primary_b64: str,
        ocr_secondary_b64: str,
        timestamp: str
    ):
        self.engine.tts.stop()
        self.current_pixmap = pixmap
        self.current_b64 = normal_b64
        self.captured_time = timestamp
        self.is_code_mode = False
        self.auto_copy_badge.hide()
        self.detected_lang_badge.setText("🌐 Algılanıyor...")
        self.sync_pin_state()
        self.apply_modular_layout()

        self.time_badge.setText(f"⏱ {timestamp}")
        thumb = pixmap.scaled(
            46, 34,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation
        )
        self.thumb_preview.setPixmap(thumb)

        active_lang = self.settings.get("targetLang", "tr")
        idx = next((i for i, (code, _) in enumerate(self.languages) if code == active_lang), 0)
        self.lang_select.blockSignals(True)
        self.lang_select.setCurrentIndex(idx)
        self.lang_select.blockSignals(False)

        self.ocr_input.blockSignals(True)
        self.ocr_input.setPlainText("")
        self.ocr_input.blockSignals(False)
        self.translated_output.setPlainText("")
        self.ai_output.setPlainText("")

        self.status_lbl.setText("⏳ Metin okunuyor ve dili algılanıyor...")
        self.status_lbl.setStyleSheet("color: #38bdf8; font-size: 12px; padding: 6px 4px;")
        self.status_lbl.show()

        self.apply_configured_position(rect)
        force_bring_to_front(self)
        self.apply_configured_position(rect)

        self.engine.handle_ocr_and_translate(ocr_primary_b64, ocr_secondary_b64, active_lang)

        only_ai_enabled = (
            self.settings.get("showAiSection", True)
            and not self.settings.get("showOcrSection", True)
            and not self.settings.get("showTranslateSection", True)
        )
        if self.settings.get("aiAutoRun", False) or only_ai_enabled:
            self.ai_output.setPlainText("🤖 Yapay zeka inceliyor...")
            img_to_send = self.current_b64 if self.settings.get("aiSendImage", True) else None
            self.engine.ask_ai(
                self.settings,
                "",
                "Ekrandaki bu içeriği analiz et, açıkla ve çözümünü sun.",
                img_to_send
            )

    def _on_ocr_result(self, res: dict):
        if res.get("error"):
            self.status_lbl.setText(res["error"])
            self.status_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; padding: 6px 4px;")
            self._update_detected_lang_ui("")
            self.panel.update()
            self.repaint()
            return

        text = res.get("text", "")
        translated = res.get("translatedText", "")
        detected_lang = res.get("detectedLang") or guess_language_locally(text)
        self._update_detected_lang_ui(detected_lang)

        self.status_lbl.hide()
        self.ocr_input.blockSignals(True)
        self.ocr_input.setPlainText(text)
        self.ocr_input.blockSignals(False)
        self.translated_output.setPlainText(translated)

        if self.settings.get("autoCopy") and text:
            QApplication.clipboard().setText(text)
            self.auto_copy_badge.show()

        if text:
            history = self.settings.get("history", [])
            history = [item for item in history if item.get("text") != text]
            history.insert(0, {
                "text": text,
                "translated": translated,
                "lang": get_language_display_name(detected_lang),
                "time": int(time.time() * 1000)
            })
            self.settings["history"] = history[:25]
            StorageManager.save(self.settings)
            self.history_updated.emit()

        url_match = re.search(r"(https?://[^\s]+|www\.[^\s]+\.[a-z]{2,})", text, re.IGNORECASE)
        if url_match:
            self.detected_url = url_match.group(0)
            self.btn_open_url.show()
        else:
            self.detected_url = None
            self.btn_open_url.hide()

        self.apply_modular_layout()
        self.repaint()

    def _on_target_lang_combo_changed(self):
        """Kullanıcı çeviri kutusundan hedef dili değiştirdiği anda anında çevirir."""
        self.debounce_timer.stop()
        txt = self.ocr_input.toPlainText().strip()
        lang = self.lang_select.currentData()
        if txt and lang:
            self.translated_output.setPlainText("⏳ Çevriliyor...")
            self.translated_output.viewport().update()
            self.engine.translate_only(txt, lang)

    def _on_retranslate_result(self, translated_text: str, detected_src_lang: str):
        self.translated_output.setPlainText(translated_text)
        if detected_src_lang:
            self._update_detected_lang_ui(detected_src_lang)
        self.translated_output.viewport().update()
        self.repaint()

    def _trigger_retranslate(self):
        txt = self.ocr_input.toPlainText().strip()
        lang = self.lang_select.currentData()
        if txt and lang:
            self.engine.translate_only(txt, lang)

    def _on_code_mode_clicked(self):
        self.is_code_mode = not self.is_code_mode
        if self.is_code_mode:
            self.ocr_input.setStyleSheet("""
                QTextEdit {
                    font-family: "JetBrains Mono", Consolas, monospace;
                    font-size: 12px;
                    background-color: #090d16;
                    color: #38bdf8;
                    border: 1px solid #334155;
                    border-radius: 6px;
                    padding: 6px;
                }
            """)
            lines = [line.rstrip() for line in self.ocr_input.toPlainText().split("\n")]
            filtered = [
                line for idx, line in enumerate(lines)
                if not (line == "" and idx > 0 and lines[idx - 1] == "")
            ]
            self.ocr_input.blockSignals(True)
            self.ocr_input.setPlainText("\n".join(filtered))
            self.ocr_input.blockSignals(False)
        else:
            self.ocr_input.setStyleSheet("""
                QTextEdit {
                    background-color: #1e293b;
                    color: #f1f5f9;
                    border: 1px solid #334155;
                    border-radius: 6px;
                    padding: 6px;
                    font-size: 13px;
                }
            """)
        self.ocr_input.viewport().update()

    def _on_lens_clicked(self):
        self.btn_lens.setText("Sonuçlar Açılıyor...")
        if self.current_pixmap:
            QApplication.clipboard().setPixmap(self.current_pixmap)
        self.engine.open_google_lens_with_auto_upload(self.current_b64)

    def _on_lens_finished(self):
        self.btn_lens.setText("🔍 Görseli Ara (Lens)")

    def _on_copy_img_clicked(self):
        if self.current_pixmap:
            QApplication.clipboard().setPixmap(self.current_pixmap)
            orig = self.btn_copy_img.text()
            self.btn_copy_img.setText("Kopyalandı ✓")
            QTimer.singleShot(1200, lambda: self.btn_copy_img.setText(orig))

    def _copy_with_feedback(self, btn: QPushButton, text_to_copy: str):
        QApplication.clipboard().setText(text_to_copy)
        orig = btn.text()
        btn.setText("✓ Kopyalandı")
        QTimer.singleShot(1200, lambda: btn.setText(orig))

    def _on_copy_time_note(self):
        lang_name = get_language_display_name(self.detected_src_lang)
        note = (
            f"[{self.captured_time} • {lang_name}] {self.ocr_input.toPlainText().strip()}\n"
            f"Çeviri: {self.translated_output.toPlainText().strip()}"
        )
        self._copy_with_feedback(self.btn_copy_time, note)

    def _on_open_url(self):
        if self.detected_url:
            target = self.detected_url if self.detected_url.startswith("http") else f"https://{self.detected_url}"
            webbrowser.open(target)

    def _on_web_search(self):
        query = self.ocr_input.toPlainText().strip()
        if query:
            webbrowser.open(f"https://www.google.com/search?q={quote(query)}")

    def mousePressEvent(self, event):
        if self.settings.get("panelPinned"):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.settings.get("panelPinned"):
            return
        if self.drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self.drag_offset)

    def mouseReleaseEvent(self, event):
        if self.drag_offset is not None:
            self.drag_offset = None
            self.settings["savedPanelPos"] = [self.x(), self.y()]
            StorageManager.save(self.settings)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.settings["savedPanelSize"] = [self.width(), self.height()]
        StorageManager.save(self.settings)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and not self.settings.get("panelPinned"):
            self.engine.tts.stop()
            self.hide()


# ---------------------------------------------------------------------------
# 9. AYARLAR, BÖLÜM SEÇİCİ VE AI API YÖNETİM MENÜSÜ
# ---------------------------------------------------------------------------
class PopupMenuWindow(QWidget):
    toggle_requested = pyqtSignal()
    settings_updated = pyqtSignal()
    panel_pos_changed = pyqtSignal()
    open_ai_dialog_requested = pyqtSignal()

    def __init__(self, settings: dict):
        super().__init__()
        self.settings = settings
        self.recording_mode = None
        self.drag_offset = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setMinimumSize(365, 460)

        saved_size = self.settings.get("savedMenuSize", [395, 560])
        self.resize(int(saved_size[0]), int(saved_size[1]))

        self._init_ui()
        self.refresh_view()

    def _init_ui(self):
        self.setStyleSheet("background-color: #0f172a;")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("PopupCard")
        card.setStyleSheet("""
            QFrame#PopupCard {
                background-color: #0f172a;
                border: 2px solid #334155;
                border-radius: 8px;
            }
            QLabel { border: none; }
            QFrame.settingRow {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
            }
            QComboBox {
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QComboBox QAbstractItemView {
                background-color: #1e293b;
                color: #f8fafc;
                selection-background-color: #0284c7;
            }
            QCheckBox { color: #e2e8f0; font-size: 11px; font-weight: 600; }
        """)

        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(14, 12, 14, 6)
        c_layout.setSpacing(6)

        header = QHBoxLayout()
        logo_lbl = QLabel()
        logo_lbl.setPixmap(get_app_logo(22))
        brand_lbl = QLabel("TakoliCopy")
        brand_lbl.setStyleSheet("color: #38bdf8; font-size: 15px; font-weight: 800;")

        btn_start = QPushButton("Seçimi Başlat")
        btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_start.setStyleSheet("""
            QPushButton {
                background-color: #0284c7; color: #ffffff; border: none;
                padding: 5px 10px; border-radius: 6px; font-size: 11px; font-weight: 600;
            }
            QPushButton:hover { background-color: #0369a1; }
        """)
        btn_start.clicked.connect(self._on_start)

        self.btn_pin_menu = QPushButton("📌 Sabitle")
        self.btn_pin_menu.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pin_menu.clicked.connect(self._toggle_menu_pin)

        btn_close = QPushButton("✕")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet("background:transparent; border:none; color:#94a3b8; font-size:14px; padding:2px 6px;")
        btn_close.clicked.connect(self.hide)

        header.addWidget(logo_lbl)
        header.addWidget(brand_lbl)
        header.addStretch()
        header.addWidget(btn_start)
        header.addWidget(self.btn_pin_menu)
        header.addWidget(btn_close)
        c_layout.addLayout(header)

        row1, self.btn_combo_key = self._build_key_row(
            "Seçim Modu Kısayolu",
            "Seçimi başlatır/kapatır (Örn: Alt + S)",
            lambda: self._start_recording("combo")
        )
        c_layout.addWidget(row1)

        row_menu_hk, self.btn_menu_key = self._build_key_row(
            "Menüyü Aç / Kapat Kısayolu",
            "Her pencerede bu menüyü çağırır",
            lambda: self._start_recording("menu")
        )
        c_layout.addWidget(row_menu_hk)

        row2, self.btn_hold_key = self._build_key_row(
            "Basılı Tutup Seçme Tuşu",
            "Tuşa basılı tut + Sol tık sürükle",
            lambda: self._start_recording("hold")
        )
        c_layout.addWidget(row2)

        ai_mod_frame = QFrame()
        ai_mod_frame.setProperty("class", "settingRow")
        am_layout = QVBoxLayout(ai_mod_frame)
        am_layout.setContentsMargins(10, 7, 10, 7)
        am_layout.setSpacing(6)

        am_top = QHBoxLayout()
        am_title = QLabel("🧩 Pencere Bölümleri & 🤖 Yapay Zeka API")
        am_title.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: 700;")

        self.btn_ai_cfg = QPushButton("🔑 AI API Bağla")
        self.btn_ai_cfg.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ai_cfg.clicked.connect(self.open_ai_dialog_requested.emit)

        am_top.addWidget(am_title)
        am_top.addStretch()
        am_top.addWidget(self.btn_ai_cfg)
        am_layout.addLayout(am_top)

        chk_row = QHBoxLayout()
        chk_row.setSpacing(8)
        self.chk_mod_ocr = QCheckBox("📝 Metin")
        self.chk_mod_tr = QCheckBox("🌍 Çeviri")
        self.chk_mod_ai = QCheckBox("🤖 AI")
        self.chk_mod_tab = QCheckBox("🗂️ Sekmeli")
        self.chk_mod_tab.setToolTip("İşaretliyse bölümler sekme olarak açılır, pencere şişmez")

        for chk in (self.chk_mod_ocr, self.chk_mod_tr, self.chk_mod_ai, self.chk_mod_tab):
            chk.stateChanged.connect(self._on_module_checkbox_changed)
            chk_row.addWidget(chk)
        am_layout.addLayout(chk_row)
        c_layout.addWidget(ai_mod_frame)

        self.pos_options = [
            ("cursor", "İmleç Yanı (Serbest)"),
            ("remember", "Son Sürüklenen Yeri Hatırla"),
            ("top_right", "Sağ Üst Köşe"),
            ("bottom_right", "Sağ Alt Köşe"),
            ("top_left", "Sol Üst Köşe"),
            ("bottom_left", "Sol Alt Köşe"),
            ("center", "Ekranın Tam Ortası")
        ]
        row_panel_pos, self.sel_panel_pos = self._build_combo_row(
            "Çıkan Ekran Konumu",
            "Sonuç kartının açılacağı yer",
            self.pos_options,
            self._on_panel_pos_change
        )
        c_layout.addWidget(row_panel_pos)

        row_menu_pos, self.sel_menu_pos = self._build_combo_row(
            "Menü Konumu",
            "Bu menünün açılacağı yer",
            self.pos_options,
            self._on_menu_pos_change
        )
        c_layout.addWidget(row_menu_pos)

        self.languages = [
            ("tr", "Türkçe"), ("en", "İngilizce"), ("de", "Almanca"),
            ("fr", "Fransızca"), ("es", "İspanyolca"), ("ru", "Rusça")
        ]
        row3, self.sel_target_lang = self._build_combo_row(
            "Varsayılan Çeviri Dili",
            "Otomatik çevrilecek hedef dil",
            self.languages,
            self._on_lang_change
        )
        c_layout.addWidget(row3)

        row4 = QFrame()
        row4.setProperty("class", "settingRow")
        r4_layout = QHBoxLayout(row4)
        r4_layout.setContentsMargins(10, 5, 10, 5)
        r4_info = QVBoxLayout()
        t4 = QLabel("Seçince Otomatik Kopyala")
        t4.setStyleSheet("color: #f1f5f9; font-size: 12px; font-weight: 600;")
        d4 = QLabel("Metni anında panoya kopyalar")
        d4.setStyleSheet("color: #94a3b8; font-size: 10px;")
        r4_info.addWidget(t4)
        r4_info.addWidget(d4)

        self.chk_auto_copy = QCheckBox()
        self.chk_auto_copy.stateChanged.connect(self._on_auto_copy_change)

        r4_layout.addLayout(r4_info)
        r4_layout.addStretch()
        r4_layout.addWidget(self.chk_auto_copy)
        c_layout.addWidget(row4)

        hist_top = QHBoxLayout()
        hist_lbl = QLabel("SON YAKALANANLAR (TIKLA KOPYALA)")
        hist_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 700;")
        btn_clear = QPushButton("Temizle")
        btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear.setStyleSheet("background:none; border:none; color:#ef4444; font-size:11px; font-weight:600;")
        btn_clear.clicked.connect(self._on_clear_history)
        hist_top.addWidget(hist_lbl)
        hist_top.addStretch()
        hist_top.addWidget(btn_clear)
        c_layout.addLayout(hist_top)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(75)
        self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        self.history_container = QWidget()
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(5)
        self.scroll.setWidget(self.history_container)
        c_layout.addWidget(self.scroll, 1)

        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        self.grip_hint = QLabel("↘ Boyutlandır")
        self.grip_hint.setStyleSheet("color: #64748b; font-size: 10px;")
        grip_row.addStretch()
        grip_row.addWidget(self.grip_hint)
        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(14, 14)
        grip_row.addWidget(self.size_grip)
        c_layout.addLayout(grip_row)

        outer.addWidget(card)

    def _build_key_row(self, title: str, desc: str, callback):
        row = QFrame()
        row.setProperty("class", "settingRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 5, 10, 5)
        info = QVBoxLayout()
        t = QLabel(title)
        t.setStyleSheet("color: #f1f5f9; font-size: 12px; font-weight: 600;")
        d = QLabel(desc)
        d.setStyleSheet("color: #94a3b8; font-size: 10px;")
        info.addWidget(t)
        info.addWidget(d)

        btn = QPushButton()
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(callback)

        layout.addLayout(info)
        layout.addStretch()
        layout.addWidget(btn)
        return row, btn

    def _build_combo_row(self, title: str, desc: str, items: list, callback):
        row = QFrame()
        row.setProperty("class", "settingRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 5, 10, 5)
        info = QVBoxLayout()
        t = QLabel(title)
        t.setStyleSheet("color: #f1f5f9; font-size: 12px; font-weight: 600;")
        d = QLabel(desc)
        d.setStyleSheet("color: #94a3b8; font-size: 10px;")
        info.addWidget(t)
        info.addWidget(d)

        combo = QComboBox()
        for code, label in items:
            combo.addItem(label, code)
        combo.currentIndexChanged.connect(callback)

        layout.addLayout(info)
        layout.addStretch()
        layout.addWidget(combo)
        return row, combo

    def _on_module_checkbox_changed(self):
        self.settings["showOcrSection"] = self.chk_mod_ocr.isChecked()
        self.settings["showTranslateSection"] = self.chk_mod_tr.isChecked()
        self.settings["showAiSection"] = self.chk_mod_ai.isChecked()
        if not any([
            self.settings["showOcrSection"],
            self.settings["showTranslateSection"],
            self.settings["showAiSection"]
        ]):
            self.settings["showOcrSection"] = True
            self.chk_mod_ocr.blockSignals(True)
            self.chk_mod_ocr.setChecked(True)
            self.chk_mod_ocr.blockSignals(False)

        self.settings["layoutMode"] = "tabbed" if self.chk_mod_tab.isChecked() else "stacked"
        StorageManager.save(self.settings)
        self.settings_updated.emit()

    def _apply_key_btn_style(self, btn: QPushButton, recording: bool):
        if recording:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #451a03; border: 1px solid #f59e0b; color: #fbbf24;
                    padding: 5px 10px; border-radius: 6px; font-weight: 700; font-size: 11px; min-width: 96px;
                }
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #0f172a; border: 1px solid #475569; color: #38bdf8;
                    padding: 5px 10px; border-radius: 6px; font-weight: 700; font-size: 11px; min-width: 96px;
                }
                QPushButton:hover { border-color: #38bdf8; }
            """)

    def _toggle_menu_pin(self):
        pinned = not bool(self.settings.get("menuPinned", False))
        self.settings["menuPinned"] = pinned
        if pinned:
            self.settings["savedMenuPos"] = [self.x(), self.y()]
        StorageManager.save(self.settings)
        self.refresh_view()

    def refresh_view(self):
        pinned = bool(self.settings.get("menuPinned", False))
        if pinned:
            self.btn_pin_menu.setText("🔒 Sabitlendi")
            self.btn_pin_menu.setStyleSheet(
                "QPushButton { background-color: #0284c7; color: #fff; border: 1px solid #38bdf8; "
                "border-radius: 5px; padding: 4px 8px; font-size: 11px; font-weight: 700; }"
            )
            self.size_grip.hide()
            self.grip_hint.setText("🔒 Konum ve Boyut Kilitli")
        else:
            self.btn_pin_menu.setText("📌 Sabitle")
            self.btn_pin_menu.setStyleSheet(
                "QPushButton { background-color: #1e293b; color: #94a3b8; border: 1px solid #334155; "
                "border-radius: 5px; padding: 4px 8px; font-size: 11px; font-weight: 600; }"
                "QPushButton:hover { background-color: #334155; color: #fff; }"
            )
            self.size_grip.show()
            self.grip_hint.setText("↘ Boyutlandır")

        if is_ai_currently_connected(self.settings):
            self.btn_ai_cfg.setText("✅ API Bağlı")
            self.btn_ai_cfg.setStyleSheet(
                "QPushButton { background-color: #059669; color: #ffffff; border: 1px solid #10b981; "
                "border-radius: 5px; padding: 4px 10px; font-size: 11px; font-weight: 700; }"
                "QPushButton:hover { background-color: #047857; }"
            )
        else:
            self.btn_ai_cfg.setText("🔑 AI API Bağla")
            self.btn_ai_cfg.setStyleSheet(
                "QPushButton { background-color: #4f46e5; color: #ffffff; border: none; "
                "border-radius: 5px; padding: 4px 10px; font-size: 11px; font-weight: 700; }"
                "QPushButton:hover { background-color: #4338ca; }"
            )

        self._apply_key_btn_style(self.btn_combo_key, self.recording_mode == "combo")
        self._apply_key_btn_style(self.btn_menu_key, self.recording_mode == "menu")
        self._apply_key_btn_style(self.btn_hold_key, self.recording_mode == "hold")

        if self.recording_mode != "combo":
            self.btn_combo_key.setText(self.settings["hotkey"]["label"])
        if self.recording_mode != "menu":
            self.btn_menu_key.setText(self.settings["menuHotkey"]["label"])
        if self.recording_mode != "hold":
            self.btn_hold_key.setText(self.settings["holdKey"]["label"])

        for chk, val in [
            (self.chk_mod_ocr, self.settings.get("showOcrSection", True)),
            (self.chk_mod_tr, self.settings.get("showTranslateSection", True)),
            (self.chk_mod_ai, self.settings.get("showAiSection", True)),
            (self.chk_mod_tab, self.settings.get("layoutMode", "tabbed") == "tabbed")
        ]:
            chk.blockSignals(True)
            chk.setChecked(bool(val))
            chk.blockSignals(False)

        p_mode = self.settings.get("panelPosMode", "cursor")
        p_idx = next((i for i, (c, _) in enumerate(self.pos_options) if c == p_mode), 0)
        self.sel_panel_pos.blockSignals(True)
        self.sel_panel_pos.setCurrentIndex(p_idx)
        self.sel_panel_pos.blockSignals(False)

        m_mode = self.settings.get("menuPosMode", "bottom_right")
        m_idx = next((i for i, (c, _) in enumerate(self.pos_options) if c == m_mode), 3)
        self.sel_menu_pos.blockSignals(True)
        self.sel_menu_pos.setCurrentIndex(m_idx)
        self.sel_menu_pos.blockSignals(False)

        lang = self.settings.get("targetLang", "tr")
        l_idx = next((i for i, (c, _) in enumerate(self.languages) if c == lang), 0)
        self.sel_target_lang.blockSignals(True)
        self.sel_target_lang.setCurrentIndex(l_idx)
        self.sel_target_lang.blockSignals(False)

        self.chk_auto_copy.blockSignals(True)
        self.chk_auto_copy.setChecked(bool(self.settings.get("autoCopy", False)))
        self.chk_auto_copy.blockSignals(False)

        self._render_history()
        self.update()
        self.repaint()

    def _render_history(self):
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        history_list = self.settings.get("history", [])
        if not history_list:
            empty_lbl = QLabel("Henüz yakalanan bir metin yok.")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setStyleSheet("color: #64748b; font-size: 11px; padding: 12px 0;")
            self.history_layout.addWidget(empty_lbl)
            return

        for entry in history_list:
            btn = QPushButton()
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            orig_line = entry.get("text", "").replace("\n", " ")[:40]
            trans_line = entry.get("translated", "").replace("\n", " ")[:40]
            lang_tag = f"[{entry.get('lang')}] " if entry.get("lang") else ""
            btn.setText(f"{lang_tag}{orig_line}\n{trans_line}")
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #1e293b; border: 1px solid #334155;
                    border-radius: 8px; padding: 6px 9px; color: #f1f5f9;
                    text-align: left; font-size: 11px;
                }
                QPushButton:hover { border-color: #38bdf8; }
            """)
            full_text = entry.get("text", "")
            btn.clicked.connect(lambda checked, b=btn, t=full_text: self._copy_history_entry(b, t))
            self.history_layout.addWidget(btn)

        self.history_layout.addStretch()

    def _copy_history_entry(self, btn: QPushButton, text: str):
        QApplication.clipboard().setText(text)
        orig = btn.text()
        btn.setText("✓ Panoya Kopyalandı!")
        QTimer.singleShot(900, lambda: btn.setText(orig))

    def _on_clear_history(self):
        self.settings["history"] = []
        StorageManager.save(self.settings)
        self._render_history()

    def _start_recording(self, mode: str):
        self.recording_mode = mode
        self.refresh_view()
        if mode == "combo":
            self.btn_combo_key.setText("Tuşlara basın...")
        elif mode == "menu":
            self.btn_menu_key.setText("Tuşlara basın...")
        elif mode == "hold":
            self.btn_hold_key.setText("Bir tuşa basın...")

    def keyPressEvent(self, event):
        if not self.recording_mode:
            if event.key() == Qt.Key.Key_Escape and not self.settings.get("menuPinned"):
                self.hide()
            return

        if event.key() == Qt.Key.Key_Escape:
            self.recording_mode = None
            self.refresh_view()
            return

        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        is_modifier_only = event.key() in (
            Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_AltGr,
            Qt.Key.Key_Shift, Qt.Key.Key_Meta
        )

        if self.recording_mode in ("combo", "menu"):
            target_btn = self.btn_combo_key if self.recording_mode == "combo" else self.btn_menu_key
            setting_key = "hotkey" if self.recording_mode == "combo" else "menuHotkey"

            if is_modifier_only:
                held = []
                if ctrl: held.append("Ctrl")
                if alt: held.append("Alt")
                if shift: held.append("Shift")
                target_btn.setText(" + ".join(held) + " + ...")
                return

            key_str = qt_key_to_name(event.key())
            parts = []
            if ctrl: parts.append("Ctrl")
            if alt: parts.append("Alt")
            if shift: parts.append("Shift")
            parts.append(key_str.upper())

            self.settings[setting_key] = {
                "ctrl": ctrl,
                "alt": alt,
                "shift": shift,
                "key": key_str.lower(),
                "label": " + ".join(parts)
            }
            self.recording_mode = None
            StorageManager.save(self.settings)
            self.refresh_view()
            self.settings_updated.emit()
            return

        if self.recording_mode == "hold":
            if event.key() in (Qt.Key.Key_Alt, Qt.Key.Key_AltGr):
                self.settings["holdKey"] = {"type": "modifier", "key": "alt", "label": "Alt"}
            elif event.key() == Qt.Key.Key_Control:
                self.settings["holdKey"] = {"type": "modifier", "key": "ctrl", "label": "Ctrl"}
            elif event.key() == Qt.Key.Key_Shift:
                self.settings["holdKey"] = {"type": "modifier", "key": "shift", "label": "Shift"}
            else:
                k_name = qt_key_to_name(event.key())
                self.settings["holdKey"] = {"type": "key", "key": k_name.lower(), "label": k_name.upper()}

            self.recording_mode = None
            StorageManager.save(self.settings)
            self.refresh_view()
            self.settings_updated.emit()

    def keyReleaseEvent(self, event):
        if self.recording_mode in ("combo", "menu"):
            target_btn = self.btn_combo_key if self.recording_mode == "combo" else self.btn_menu_key
            mods = event.modifiers()
            held = []
            if mods & Qt.KeyboardModifier.ControlModifier: held.append("Ctrl")
            if mods & Qt.KeyboardModifier.AltModifier: held.append("Alt")
            if mods & Qt.KeyboardModifier.ShiftModifier: held.append("Shift")
            target_btn.setText(" + ".join(held) + " + ..." if held else "Tuşlara basın...")

    def _on_panel_pos_change(self):
        self.settings["panelPosMode"] = self.sel_panel_pos.currentData()
        StorageManager.save(self.settings)
        self.panel_pos_changed.emit()

    def _on_menu_pos_change(self):
        mode = self.sel_menu_pos.currentData()
        self.settings["menuPosMode"] = mode
        pt = compute_window_position(
            mode,
            self.width(),
            self.height(),
            saved_pos=self.settings.get("savedMenuPos")
        )
        self.move(pt)
        self.settings["savedMenuPos"] = [pt.x(), pt.y()]
        StorageManager.save(self.settings)

    def _on_lang_change(self):
        self.settings["targetLang"] = self.sel_target_lang.currentData()
        StorageManager.save(self.settings)
        self.settings_updated.emit()

    def _on_auto_copy_change(self):
        self.settings["autoCopy"] = self.chk_auto_copy.isChecked()
        StorageManager.save(self.settings)
        self.settings_updated.emit()

    def _on_start(self):
        if not self.settings.get("menuPinned"):
            self.hide()
        self.toggle_requested.emit()

    def mousePressEvent(self, event):
        if self.settings.get("menuPinned"):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.settings.get("menuPinned"):
            return
        if self.drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self.drag_offset)

    def mouseReleaseEvent(self, event):
        if self.drag_offset is not None:
            self.drag_offset = None
            self.settings["savedMenuPos"] = [self.x(), self.y()]
            StorageManager.save(self.settings)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.settings["savedMenuSize"] = [self.width(), self.height()]
        StorageManager.save(self.settings)


def qt_key_to_name(qt_key: int) -> str:
    if 0x20 <= qt_key <= 0x7E:
        return chr(qt_key)
    return Qt.Key(qt_key).name.replace("Key_", "")


# ---------------------------------------------------------------------------
# 10. HİBRİT DONANIM SEVİYESİ KLAVYE VE FARE KÖPRÜSÜ
# ---------------------------------------------------------------------------
VK_SPECIAL_MAP = {
    186: "ş", 187: "=", 188: ",", 189: "-", 190: ".", 191: "ö",
    219: "ğ", 220: "ç", 221: "ü", 222: "i", 32: "space"
}


class GlobalHookListener(QObject):
    combo_pressed = pyqtSignal()
    menu_combo_pressed = pyqtSignal()
    hold_drag_begin = pyqtSignal()

    def __init__(self, get_settings_fn, is_recording_fn):
        super().__init__()
        self.get_settings = get_settings_fn
        self.is_recording = is_recording_fn
        self.pressed_keys = set()
        self.combo_locked = False
        self.menu_locked = False
        self.hold_mouse_locked = False

        self.kb_listener = keyboard.Listener(on_press=self._on_key_down, on_release=self._on_key_up)
        self.mouse_listener = mouse.Listener(on_click=self._on_mouse_click)
        self.kb_listener.start()
        self.mouse_listener.start()

        if sys.platform == "win32":
            self.hw_poll_timer = QTimer(self)
            self.hw_poll_timer.setInterval(30)
            self.hw_poll_timer.timeout.connect(self._poll_win32_hardware_keys)
            self.hw_poll_timer.start()

    def _poll_win32_hardware_keys(self):
        if self.is_recording():
            return
        cfg = self.get_settings()

        if self._matches_hk_hardware(cfg.get("hotkey", {})):
            if not self.combo_locked:
                self.combo_locked = True
                self.combo_pressed.emit()
        else:
            self.combo_locked = False

        if self._matches_hk_hardware(cfg.get("menuHotkey", {})):
            if not self.menu_locked:
                self.menu_locked = True
                self.menu_combo_pressed.emit()
        else:
            self.menu_locked = False

        hold_k = cfg.get("holdKey", {}).get("key", "alt").lower()
        left_down = is_left_mouse_pressed()
        hold_down = is_key_physically_down(hold_k, self.pressed_keys)

        if left_down and hold_down:
            if not self.hold_mouse_locked:
                self.hold_mouse_locked = True
                self.hold_drag_begin.emit()
        elif not left_down:
            self.hold_mouse_locked = False

    def _matches_hk_hardware(self, hk: dict) -> bool:
        if not hk:
            return False
        target_k = hk.get("key", "").lower()
        if not target_k:
            return False
        c_down = is_key_physically_down("ctrl", self.pressed_keys)
        a_down = is_key_physically_down("alt", self.pressed_keys)
        s_down = is_key_physically_down("shift", self.pressed_keys)
        k_down = is_key_physically_down(target_k, self.pressed_keys)

        return (
            c_down == bool(hk.get("ctrl")) and
            a_down == bool(hk.get("alt")) and
            s_down == bool(hk.get("shift")) and
            k_down
        )

    def _normalize(self, key) -> set:
        tokens = set()
        if isinstance(key, keyboard.Key):
            name = key.name.lower()
            if "ctrl" in name:
                tokens.add("ctrl")
            elif "alt" in name:
                tokens.add("alt")
            elif "shift" in name:
                tokens.add("shift")
            else:
                tokens.add(name)
            return tokens

        if isinstance(key, keyboard.KeyCode):
            if key.vk is not None:
                vk = int(key.vk)
                if 48 <= vk <= 57 or 65 <= vk <= 90:
                    tokens.add(chr(vk).lower())
                elif 112 <= vk <= 123:
                    tokens.add(f"f{vk - 111}")
                elif vk in VK_SPECIAL_MAP:
                    tokens.add(VK_SPECIAL_MAP[vk])

            if key.char:
                ch = key.char
                if ord(ch) < 32:
                    tokens.add(chr(ord(ch) + 96).lower())
                else:
                    tokens.add(ch.lower())
        return tokens

    def _on_key_down(self, key):
        if self.is_recording():
            return
        tokens = self._normalize(key)
        self.pressed_keys.update(tokens)

        if sys.platform != "win32":
            cfg = self.get_settings()
            if self._matches_hk_hardware(cfg.get("hotkey", {})):
                if not self.combo_locked:
                    self.combo_locked = True
                    self.combo_pressed.emit()
                return
            if self._matches_hk_hardware(cfg.get("menuHotkey", {})):
                if not self.menu_locked:
                    self.menu_locked = True
                    self.menu_combo_pressed.emit()

    def _on_key_up(self, key):
        tokens = self._normalize(key)
        for t in tokens:
            self.pressed_keys.discard(t)
        if sys.platform != "win32":
            self.combo_locked = False
            self.menu_locked = False

    def _on_mouse_click(self, x, y, button, pressed):
        if sys.platform == "win32":
            return
        if button != mouse.Button.left or not pressed or self.is_recording():
            return
        hold_k = self.get_settings().get("holdKey", {}).get("key", "alt").lower()
        if is_key_physically_down(hold_k, self.pressed_keys):
            self.hold_drag_begin.emit()


# ---------------------------------------------------------------------------
# 11. ANA UYGULAMA KONTROLCÜSÜ
# ---------------------------------------------------------------------------
class TakoliCopyApplication:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        self.settings = StorageManager.load()
        self.is_mode_active = False

        self.app_icon = QIcon(get_app_logo(128))
        self.app.setWindowIcon(self.app_icon)

        self.engine = BackgroundEngine()
        self.status_pill = StatusPillWidget()
        self.shield_overlay = CaptureShieldOverlay()
        self.result_panel = ResultPanelWindow(self.engine, self.settings)
        self.popup_window = PopupMenuWindow(self.settings)

        self.global_hooks = GlobalHookListener(
            lambda: self.settings,
            lambda: self.popup_window.isVisible() and (self.popup_window.recording_mode is not None)
        )

        self.status_pill.clicked.connect(lambda: self.toggle_selection_mode(False))
        self.shield_overlay.capture_completed.connect(self._on_capture_done)
        self.shield_overlay.cancelled.connect(lambda: self.toggle_selection_mode(False))

        self.result_panel.history_updated.connect(self.popup_window.refresh_view)
        self.result_panel.open_settings_requested.connect(self.open_popup_menu)
        self.result_panel.layout_settings_changed.connect(self.popup_window.refresh_view)

        self.popup_window.toggle_requested.connect(lambda: self.toggle_selection_mode())
        self.popup_window.panel_pos_changed.connect(lambda: self.result_panel.apply_configured_position())
        self.popup_window.settings_updated.connect(self.result_panel.apply_modular_layout)
        self.popup_window.open_ai_dialog_requested.connect(self.result_panel.open_ai_config_dialog)

        self.global_hooks.combo_pressed.connect(lambda: self.toggle_selection_mode())
        self.global_hooks.menu_combo_pressed.connect(self.toggle_popup_menu)
        self.global_hooks.hold_drag_begin.connect(self._on_hold_drag_begin)

        self._init_system_tray()

    def _init_system_tray(self):
        self.tray = QSystemTrayIcon(self.app_icon, self.app)
        self.tray.setToolTip("TakoliCopy / ScreenPix - OCR, Çeviri, Lens & Yapay Zeka")

        menu = QMenu()
        act_toggle = menu.addAction("⚡ Seçimi Başlat / Kapat")
        act_toggle.triggered.connect(lambda: self.toggle_selection_mode())

        act_popup = menu.addAction("⚙️ Ayarlar & Geçmiş Menüsü")
        act_popup.triggered.connect(self.open_popup_menu)

        act_ai = menu.addAction("🤖 Yapay Zeka (AI) API Ayarları")
        act_ai.triggered.connect(self.result_panel.open_ai_config_dialog)

        menu.addSeparator()
        act_quit = menu.addAction("❌ Çıkış")
        act_quit.triggered.connect(self.app.quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        QTimer.singleShot(200, self.open_popup_menu)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_popup_menu()

    def toggle_popup_menu(self):
        if self.popup_window.isVisible():
            self.popup_window.hide()
        else:
            self.open_popup_menu()

    def open_popup_menu(self):
        self.popup_window.refresh_view()
        pos_mode = self.settings.get("menuPosMode", "bottom_right")
        if pos_mode in ("top_right", "bottom_right", "top_left", "bottom_left", "center"):
            pt = compute_window_position(pos_mode, self.popup_window.width(), self.popup_window.height())
            self.popup_window.move(pt)
        elif self.settings.get("menuPinned") or pos_mode == "remember":
            saved_pos = self.settings.get("savedMenuPos")
            if isinstance(saved_pos, list) and len(saved_pos) == 2:
                self.popup_window.move(int(saved_pos[0]), int(saved_pos[1]))
        else:
            pt = compute_window_position(
                pos_mode,
                self.popup_window.width(),
                self.popup_window.height(),
                saved_pos=self.settings.get("savedMenuPos")
            )
            self.popup_window.move(pt)
        force_bring_to_front(self.popup_window)

    def toggle_selection_mode(self, force_state=None):
        self.is_mode_active = force_state if isinstance(force_state, bool) else (not self.is_mode_active)
        if self.is_mode_active:
            if not self.settings.get("panelPinned"):
                self.result_panel.hide()
            if not self.settings.get("menuPinned"):
                self.popup_window.hide()
            self.status_pill.place_bottom_right()
            self.status_pill.show()
            self.shield_overlay.activate_shield(start_immediately=False)
        else:
            self.status_pill.hide()
            self.shield_overlay.hide()

    def _on_hold_drag_begin(self):
        cursor_pt = QCursor.pos()
        if self.result_panel.isVisible() and self.result_panel.geometry().contains(cursor_pt):
            return
        if self.popup_window.isVisible() and self.popup_window.geometry().contains(cursor_pt):
            return

        if not self.shield_overlay.isVisible():
            if not self.settings.get("panelPinned"):
                self.result_panel.hide()
            if not self.settings.get("menuPinned"):
                self.popup_window.hide()
            self.status_pill.place_bottom_right()
            self.status_pill.show()
            self.shield_overlay.activate_shield(start_immediately=True)

    def _on_capture_done(
        self,
        rect: QRect,
        pixmap: QPixmap,
        normal_b64: str,
        ocr_primary_b64: str,
        ocr_secondary_b64: str,
        timestamp: str
    ):
        self.is_mode_active = False
        self.status_pill.hide()
        self.result_panel.render_result_panel(
            rect, pixmap, normal_b64, ocr_primary_b64, ocr_secondary_b64, timestamp
        )

    def run(self):
        sys.exit(self.app.exec())


if __name__ == "__main__":
    ensure_admin_on_windows()
    app = TakoliCopyApplication()
    app.run()