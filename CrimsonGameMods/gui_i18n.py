"""
gui_i18n.py — single-lever, English-as-key i18n with Qt setter interception.

Design goals
------------
1. English is the canonical "key". `tr("Save")` returns the translated string for
   the current language (or "Save" if untranslated).
2. Legacy `tr("btn.save")` call sites (keyed JSONs like locale/en.json) keep
   working via a reverse-key map: English text -> legacy key -> translated value.
3. ALL user-visible Qt setters are monkey-patched at `install()` time so that
   every string passed through them is routed through `tr()`. Zero dev discipline
   required going forward.
4. Game-data names (items/quests/missions) use `locale/names_{lang}.json` via
   `translate_item_name` and are wired into `item_db` automatically.

Bypass marker: a string starting with U+200B (zero-width space) is returned
as-is (the marker is stripped), for verbatim strings (paths, hashes, etc.).
"""

from __future__ import annotations

import json
import os
import sys
import logging
from typing import Dict, List, Optional, Set, Any

log = logging.getLogger(__name__)

_MY_DIR = os.path.dirname(os.path.abspath(__file__))
_BYPASS = "\u200b"

_current_lang: str = "en"

_translations: Dict[str, str] = {}
_legacy_translations: Dict[str, str] = {}
_english_to_key: Dict[str, str] = {}
_names_data: Dict[str, Any] = {}

_harvest: Set[str] = set()

_installed: bool = False
_patched_count: int = 0

_needs_language_picker: bool = False


def _locale_dirs() -> List[str]:
    """Return all directories to search for locale files, highest priority first.

    Order:
      1. User-writable download cache (e.g. %LOCALAPPDATA%/CrimsonSaveEditor/locale)
      2. {exe_dir}/locale  (next to the exe for frozen builds)
      3. PyInstaller _MEIPASS/locale  (bundled, read-only)
      4. Source tree locale/
      5. Source tree dist/locale/  (legacy)
    """
    dirs: List[str] = []

    try:
        from lang_pack_downloader import USER_LOCALE_DIR
        dirs.append(str(USER_LOCALE_DIR))
    except Exception:
        pass

    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(os.path.dirname(sys.executable), "locale"))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(os.path.join(meipass, "locale"))
    dirs.append(os.path.join(_MY_DIR, "locale"))
    dirs.append(os.path.join(_MY_DIR, "dist", "locale"))
    seen = set()
    out = []
    for d in dirs:
        key = os.path.normcase(os.path.abspath(d)) if d else ""
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _load_json(lang: str) -> Dict[str, Any]:
    variants = [f"{lang}.json"]
    aliases = {
        "ja": ["ja_JP"], "ko": ["ko_KR"], "zh": ["zh_CN"],
        "zh-tw": ["zh_TW"], "de": ["de_DE"], "fr": ["fr_FR"],
        "es": ["es_ES"], "it": ["it_IT"], "pl": ["pl_PL"],
        "ru": ["ru_RU"], "tr": ["tr_TR"], "pt-br": ["pt_BR"],
    }
    for alias in aliases.get(lang, []):
        variants.append(f"{alias}.json")
    short = lang.split("_")[0].split("-")[0]
    if short != lang:
        variants.append(f"{short}.json")
    for d in _locale_dirs():
        for v in variants:
            p = os.path.join(d, v)
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        return data
                except Exception as e:
                    log.warning("Failed to load %s: %s", p, e)
    return {}


def _load_names(lang: str) -> Dict[str, Any]:
    if lang == "en":
        return {}
    for d in _locale_dirs():
        p = os.path.join(d, f"names_{lang}.json")
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log.warning("Failed to load %s: %s", p, e)
    short = lang.split("_")[0].split("-")[0]
    if short != lang:
        return _load_names(short)
    return {}


_DOTTED_KEY_RE = None

def _looks_like_legacy_key(k: str) -> bool:
    """A legacy dotted key looks like ``tab.inventory``: lowercase, no spaces,
    contains a dot, no uppercase, not a UI sentence."""
    if not k or "_" == k[0]:
        return False
    if " " in k:
        return False
    if "." not in k:
        return False
    return k.islower()


def _load_translations(lang: str) -> None:
    """Reload ``{lang}.json`` and ``en.json`` into the in-memory dictionaries.

    Call this after the user switches language at runtime.

    Rules (simple, no heuristics):
      * ``_translations`` is a flat ``english_or_key -> localized`` dict. We just
        merge every non-underscore string entry from the target JSON into it.
      * ``_legacy_translations`` is the same dict re-keyed by legacy dotted keys
        so ``tr("btn.save")`` still resolves.
      * ``_english_to_key`` maps English text to the legacy dotted key so
        ``tr("Save")`` still works when the translation lives under the legacy
        key only (old-style ``locale/en.json`` + ``locale/ko.json`` pairing).
    """
    global _translations, _legacy_translations, _english_to_key, _names_data

    _translations = {}
    _legacy_translations = {}
    _english_to_key = {}
    _names_data = {}

    en_data = _load_json("en")
    if isinstance(en_data, dict):
        for k, v in en_data.items():
            if k.startswith("_") or not isinstance(v, str) or not v:
                continue
            if _looks_like_legacy_key(k):
                _english_to_key[v] = k

    if lang == "en":
        return

    target = _load_json(lang)
    log.info("gui_i18n: loading lang=%r from dirs=%s -> %d entries",
             lang, _locale_dirs(), len(target))
    if not target:
        log.warning("gui_i18n: no locale pack found for %r", lang)
    for k, v in target.items():
        if k.startswith("_") or not isinstance(v, str) or not v:
            continue
        if _looks_like_legacy_key(k):
            _legacy_translations[k] = v
        else:
            _translations[k] = v

    _names_data = _load_names(lang)
    log.info("gui_i18n: loaded %d english-keyed, %d legacy-keyed, %d english->key, %d names",
             len(_translations), len(_legacy_translations),
             len(_english_to_key), len(_names_data))


def _classify_and_load(lang: str) -> None:
    """Back-compat wrapper — kept so external callers don't break."""
    _load_translations(lang)


def tr(text: Any, **fmt: Any) -> Any:
    """Translate ``text`` from English to the active language.

    - Returns non-str unchanged.
    - Bypass marker (U+200B prefix) returns the remainder verbatim.
    - Format kwargs applied post-translation (best-effort).
    """
    if not isinstance(text, str) or not text:
        return text
    if text.startswith(_BYPASS):
        return text[len(_BYPASS):]

    if _current_lang == "en":
        out = text
    else:
        out = _translations.get(text)
        if out is None:
            key = _english_to_key.get(text)
            if key is not None:
                out = _legacy_translations.get(key)
        if out is None:
            if text in _legacy_translations:
                out = _legacy_translations[text]
        if out is None:
            _harvest.add(text)
            out = text

    if fmt:
        try:
            out = out.format(**fmt)
        except Exception:
            pass
    return out


def current_language() -> str:
    return _current_lang


def set_language(lang: str) -> None:
    """Switch the active language and reload translation tables.

    Safe to call at any point — including after a runtime download via the
    language picker. New widget strings pick up the change automatically via
    the Qt setter interceptors patched by :func:`install`.
    """
    global _current_lang
    if lang and lang != "en":
        try:
            from lang_pack_downloader import is_pack_local as _probe
            if not _probe(lang):
                log.warning(
                    "gui_i18n: language pack %r not installed; "
                    "translations will fall back to English.", lang,
                )
        except Exception:
            pass
    _current_lang = lang or "en"
    _load_translations(_current_lang)
    try:
        import sys as _sys
        mod = _sys.modules.get("item_db")
        if mod is not None:
            pass
    except Exception:
        pass


def needs_language_picker() -> bool:
    """True if no default_lang was found in editor_config.json at startup.

    MainWindow reads this after ``show()`` to decide whether to pop the
    first-run language picker.
    """
    return _needs_language_picker


def set_needs_language_picker(flag: bool) -> None:
    global _needs_language_picker
    _needs_language_picker = bool(flag)


def available_languages() -> List[str]:
    codes: Set[str] = set()
    for d in _locale_dirs():
        if not os.path.isdir(d):
            continue
        try:
            for fname in os.listdir(d):
                if fname.endswith(".json") and not fname.startswith("names_"):
                    codes.add(fname[:-5])
        except OSError:
            pass
    codes.add("en")
    return sorted(codes)


def compute_startup_language(config_path: str) -> str:
    """Read ``default_lang`` from ``editor_config.json`` and decide start lang.

    Semantics (new design):
      * If ``default_lang`` is present in the config -> use it.
      * Otherwise start English and set the ``needs_language_picker`` flag so
        MainWindow can pop the first-run picker after the window is shown.

    The legacy ``language`` key is still honored as a fallback so existing
    installs don't get re-prompted.
    """
    cfg: Dict[str, Any] = {}
    try:
        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
    except Exception:
        cfg = {}

    default = cfg.get("default_lang")
    if isinstance(default, str) and default.strip():
        return default.strip()

    legacy = cfg.get("language")
    if isinstance(legacy, str) and legacy.strip():
        return legacy.strip()

    set_needs_language_picker(True)
    return "en"


def translate_item_name(key: Any, fallback: str = "") -> str:
    """Return localized item name. Falls back to ``fallback`` (English)."""
    if _current_lang == "en" or not _names_data:
        return fallback
    items = _names_data.get("items", {})
    if not isinstance(items, dict):
        return fallback
    v = items.get(str(key)) or items.get(key)
    return v if v else fallback


def translate_quest_name(key: Any, fallback: str = "") -> str:
    if _current_lang == "en" or not _names_data:
        return fallback
    section = _names_data.get("quests", {})
    if not isinstance(section, dict):
        return fallback
    v = section.get(str(key)) or section.get(key)
    return v if v else fallback


def translate_name(kind: str, key: Any, fallback: str = "") -> str:
    if _current_lang == "en" or not _names_data:
        return fallback
    section = _names_data.get(kind, {})
    if not isinstance(section, dict):
        return fallback
    v = section.get(str(key)) or section.get(key)
    return v if v else fallback


def harvest_missing() -> List[str]:
    return sorted(_harvest)


def dump_harvest(path: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(_harvest), f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning("dump_harvest failed: %s", e)


def bypass(text: str) -> str:
    """Mark ``text`` to skip translation. Returns U+200B + text."""
    return _BYPASS + text


def _patch_method(cls, name: str, arg_indices=(0,)) -> bool:
    """Wrap ``cls.name`` so that string args at ``arg_indices`` go through tr()."""
    global _patched_count
    try:
        orig = getattr(cls, name)
    except AttributeError:
        return False
    if getattr(orig, "__gui_i18n_patched__", False):
        return False

    def wrapper(self, *args, **kw):
        if args:
            new_args = list(args)
            for i in arg_indices:
                if i < len(new_args) and isinstance(new_args[i], str):
                    new_args[i] = tr(new_args[i])
            args = tuple(new_args)
        return orig(self, *args, **kw)

    wrapper.__gui_i18n_patched__ = True
    wrapper.__name__ = name
    try:
        setattr(cls, name, wrapper)
        _patched_count += 1
        return True
    except (TypeError, AttributeError):
        return False


def _patch_staticmethod(cls, name: str, str_arg_positions=(0, 1)) -> bool:
    """Patch a @staticmethod/classmethod on ``cls`` like QMessageBox.information."""
    global _patched_count
    try:
        orig = getattr(cls, name)
    except AttributeError:
        return False
    if getattr(orig, "__gui_i18n_patched__", False):
        return False

    def wrapper(*args, **kw):
        new_args = list(args)
        for i in str_arg_positions:
            if i < len(new_args) and isinstance(new_args[i], str):
                new_args[i] = tr(new_args[i])
        return orig(*new_args, **kw)

    wrapper.__gui_i18n_patched__ = True
    wrapper.__name__ = name
    try:
        setattr(cls, name, wrapper)
        _patched_count += 1
        return True
    except (TypeError, AttributeError):
        return False


def _install_qt_patches() -> None:
    """Monkey-patch all user-visible Qt setters."""
    global _patched_count
    from PySide6 import QtWidgets, QtGui, QtCore

    W = QtWidgets
    G = QtGui

    _patch_method(W.QWidget, "setWindowTitle")
    _patch_method(W.QWidget, "setToolTip")
    _patch_method(W.QWidget, "setStatusTip")
    _patch_method(W.QWidget, "setWhatsThis")
    _patch_method(W.QWidget, "setAccessibleName")
    _patch_method(W.QWidget, "setAccessibleDescription")

    _patch_method(W.QLabel, "setText")
    _patch_method(W.QAbstractButton, "setText")
    for cls in (W.QLabel, W.QPushButton, W.QCheckBox, W.QRadioButton,
                W.QToolButton, W.QCommandLinkButton):
        _patch_method(cls, "__init__")

    _patch_method(W.QGroupBox, "setTitle")
    _patch_method(W.QGroupBox, "__init__")

    _patch_method(G.QAction, "setText")
    _patch_method(G.QAction, "setToolTip")
    _patch_method(G.QAction, "setStatusTip")
    _patch_method(G.QAction, "setWhatsThis")
    _patch_method(G.QAction, "setIconText")
    _patch_method(G.QAction, "__init__")

    _patch_method(W.QMenu, "setTitle")
    _patch_method(W.QMenu, "__init__")
    _patch_method(W.QMenu, "addAction", arg_indices=(0,))
    _patch_method(W.QMenu, "addMenu", arg_indices=(0,))
    _patch_method(W.QMenuBar, "addMenu", arg_indices=(0,))

    _patch_method(W.QTabWidget, "addTab", arg_indices=(1,))
    _patch_method(W.QTabWidget, "insertTab", arg_indices=(2,))
    _patch_method(W.QTabWidget, "setTabText", arg_indices=(1,))
    _patch_method(W.QTabWidget, "setTabToolTip", arg_indices=(1,))
    _patch_method(W.QTabWidget, "setTabWhatsThis", arg_indices=(1,))
    _patch_method(W.QTabBar, "addTab", arg_indices=(0,))
    _patch_method(W.QTabBar, "insertTab", arg_indices=(1,))
    _patch_method(W.QTabBar, "setTabText", arg_indices=(1,))
    _patch_method(W.QTabBar, "setTabToolTip", arg_indices=(1,))

    def _patch_header_labels(cls, name):
        global _patched_count
        try:
            orig = getattr(cls, name)
        except AttributeError:
            return
        if getattr(orig, "__gui_i18n_patched__", False):
            return

        def wrapper(self, labels, *a, **kw):
            if isinstance(labels, (list, tuple)):
                labels = [tr(x) if isinstance(x, str) else x for x in labels]
            return orig(self, labels, *a, **kw)
        wrapper.__gui_i18n_patched__ = True
        setattr(cls, name, wrapper)
        _patched_count += 1

    _patch_header_labels(W.QTableWidget, "setHorizontalHeaderLabels")
    _patch_header_labels(W.QTableWidget, "setVerticalHeaderLabels")
    _patch_header_labels(W.QTreeWidget, "setHeaderLabels")

    _patch_method(W.QTableWidgetItem, "__init__")
    _patch_method(W.QTableWidgetItem, "setText")
    _patch_method(W.QTableWidgetItem, "setToolTip")
    _patch_method(W.QTableWidgetItem, "setWhatsThis")

    _patch_method(W.QListWidgetItem, "__init__")
    _patch_method(W.QListWidgetItem, "setText")
    _patch_method(W.QListWidgetItem, "setToolTip")
    _patch_method(W.QListWidgetItem, "setWhatsThis")

    _patch_method(W.QTreeWidgetItem, "setText", arg_indices=(1,))
    _patch_method(W.QTreeWidgetItem, "setToolTip", arg_indices=(1,))
    _patch_method(W.QTreeWidgetItem, "setWhatsThis", arg_indices=(1,))

    try:
        _tw_orig = W.QTreeWidgetItem.__init__

        def _tw_init(self, *args, **kw):
            new_args = list(args)
            for i, a in enumerate(new_args):
                if isinstance(a, list) and all(isinstance(x, str) or x is None for x in a):
                    new_args[i] = [tr(x) if isinstance(x, str) else x for x in a]
            return _tw_orig(self, *new_args, **kw)
        _tw_init.__gui_i18n_patched__ = True
        W.QTreeWidgetItem.__init__ = _tw_init
        _patched_count += 1
    except Exception:
        pass


    _patch_method(W.QLineEdit, "setPlaceholderText")
    _patch_method(W.QPlainTextEdit, "setPlaceholderText")
    _patch_method(W.QTextEdit, "setPlaceholderText")

    _orig_combo_add_item = W.QComboBox.addItem
    if not getattr(_orig_combo_add_item, "__gui_i18n_patched__", False):
        def _combo_add_item(self, *args, **kw):
            args = list(args)
            english = None
            if args and isinstance(args[0], str):
                english = args[0]
                args[0] = tr(args[0])
            elif len(args) >= 2 and isinstance(args[1], str):
                english = args[1]
                args[1] = tr(args[1])
            if english is not None and "userData" not in kw:
                if (args and isinstance(args[0], str) and len(args) == 1) or \
                   (len(args) == 2 and not isinstance(args[0], str)):
                    kw["userData"] = english
            return _orig_combo_add_item(self, *args, **kw)
        _combo_add_item.__gui_i18n_patched__ = True
        W.QComboBox.addItem = _combo_add_item
        _patched_count += 1

    _patch_method(W.QComboBox, "insertItem", arg_indices=(1,))
    _patch_method(W.QComboBox, "setItemText", arg_indices=(1,))
    _patch_method(W.QComboBox, "setPlaceholderText")

    _orig_combo_current_text = W.QComboBox.currentText
    if not getattr(_orig_combo_current_text, "__gui_i18n_patched__", False):
        def _combo_current_text(self):
            try:
                idx = self.currentIndex()
                if idx >= 0:
                    data = self.itemData(idx)
                    if isinstance(data, str) and data:
                        return data
            except Exception:
                pass
            return _orig_combo_current_text(self)
        _combo_current_text.__gui_i18n_patched__ = True
        W.QComboBox.currentText = _combo_current_text
        _patched_count += 1

    _orig_combo_add_items = W.QComboBox.addItems
    if not getattr(_orig_combo_add_items, "__gui_i18n_patched__", False):
        def _combo_add_items(self, items):
            if isinstance(items, (list, tuple)):
                start_index = self.count()
                translated = [tr(x) if isinstance(x, str) else x for x in items]
                _orig_combo_add_items(self, translated)
                for offset, original in enumerate(items):
                    if isinstance(original, str):
                        try:
                            self.setItemData(start_index + offset, original)
                        except Exception:
                            pass
                return None
            return _orig_combo_add_items(self, items)
        _combo_add_items.__gui_i18n_patched__ = True
        W.QComboBox.addItems = _combo_add_items
        _patched_count += 1

    _patch_method(W.QListWidget, "addItem", arg_indices=(0,))
    _patch_method(W.QListWidget, "insertItem", arg_indices=(1,))

    _orig_lw_add_items = W.QListWidget.addItems

    def _lw_add_items(self, items):
        if isinstance(items, (list, tuple)):
            items = [tr(x) if isinstance(x, str) else x for x in items]
        return _orig_lw_add_items(self, items)
    if not getattr(_orig_lw_add_items, "__gui_i18n_patched__", False):
        _lw_add_items.__gui_i18n_patched__ = True
        W.QListWidget.addItems = _lw_add_items
        _patched_count += 1

    _patch_method(W.QDockWidget, "__init__")
    _patch_method(W.QDockWidget, "setWindowTitle")
    _patch_method(W.QStatusBar, "showMessage")
    _patch_method(W.QToolBar, "__init__")
    _patch_method(W.QToolBar, "setWindowTitle")
    _patch_method(W.QToolBar, "addAction", arg_indices=(0,))

    _patch_method(W.QMessageBox, "setText")
    _patch_method(W.QMessageBox, "setInformativeText")
    _patch_method(W.QMessageBox, "setDetailedText")
    _patch_method(W.QMessageBox, "setWindowTitle")
    _patch_method(W.QMessageBox, "addButton", arg_indices=(0,))
    for sname in ("information", "warning", "critical", "question", "about"):
        _patch_staticmethod(W.QMessageBox, sname, str_arg_positions=(1, 2))
    _patch_staticmethod(W.QMessageBox, "aboutQt", str_arg_positions=(1,))

    for sname in ("getText", "getInt", "getDouble", "getItem", "getMultiLineText"):
        _patch_staticmethod(W.QInputDialog, sname, str_arg_positions=(1, 2))

    for sname in ("getOpenFileName", "getSaveFileName", "getOpenFileNames",
                  "getExistingDirectory"):
        _patch_staticmethod(W.QFileDialog, sname, str_arg_positions=(1,))

    _patch_method(W.QProgressDialog, "__init__")
    _patch_method(W.QProgressDialog, "setLabelText")
    _patch_method(W.QProgressDialog, "setCancelButtonText")
    _patch_method(W.QProgressDialog, "setWindowTitle")

    _patch_method(W.QErrorMessage, "showMessage")

    _patch_staticmethod(W.QToolTip, "showText", str_arg_positions=(1,))

    try:
        _patch_staticmethod(W.QWhatsThis, "showText", str_arg_positions=(1,))
    except Exception:
        pass


def _install_item_db_adapter() -> None:
    """Wrap item_db.ItemNameDB.get_name to route through translate_item_name."""
    try:
        import item_db
    except Exception:
        return
    cls = getattr(item_db, "ItemNameDB", None)
    if cls is None or getattr(cls.get_name, "__gui_i18n_patched__", False):
        return
    orig_get_name = cls.get_name

    def get_name(self, key):
        info = self.items.get(key)
        english = info.name if (info and info.name) else f"Unknown ({key})"
        if _current_lang != "en":
            loc = translate_item_name(key, english)
            return loc
        return english
    get_name.__gui_i18n_patched__ = True
    cls.get_name = get_name


def install(app=None, lang: str = "en") -> None:
    """Install i18n: patch Qt setters, load translations, wire adapters.

    Safe to call multiple times — only patches on first call.
    """
    global _installed
    log.info("gui_i18n: install(lang=%r) called; current_lang=%r", lang, _current_lang)
    set_language(lang)
    if _installed:
        log.info("gui_i18n: install already done, only switched language to %r", _current_lang)
        return
    _installed = True
    try:
        _install_qt_patches()
        log.info("gui_i18n: patched %d Qt setter(s)", _patched_count)
    except Exception as e:
        log.error("gui_i18n: Qt patch install failed: %s", e)
    try:
        _install_item_db_adapter()
    except Exception as e:
        log.error("gui_i18n: item_db adapter install failed: %s", e)
    try:
        from PySide6.QtWidgets import QPushButton, QLabel
        b = QPushButton("Set Stack")
        l = QLabel(); l.setText("Inventory")
        log.info("gui_i18n self-test: QPushButton('Set Stack')->%r  QLabel.setText('Inventory')->%r",
                 b.text(), l.text())
        b.deleteLater(); l.deleteLater()
    except Exception as e:
        log.error("gui_i18n self-test failed: %s", e)


def patched_setter_count() -> int:
    return _patched_count
