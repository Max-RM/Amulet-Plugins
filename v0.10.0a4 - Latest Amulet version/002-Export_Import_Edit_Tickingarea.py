# -*- coding: utf-8 -*-
"""
Bedrock LevelDB: edit / export / import ticking area entries (tickingarea_* keys).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import wx
import amulet_nbt
from amulet_nbt import (
    ByteTag,
    CompoundTag,
    FloatTag,
    IntTag,
    LongTag,
    StringTag,
    from_snbt,
)

from amulet_map_editor.programs.edit.api.operations import DefaultOperationUI

if TYPE_CHECKING:
    from amulet.api.level import BaseLevel
    from amulet_map_editor.programs.edit.api.canvas import EditCanvas

# ---------------------------------------------------------------------------
PLUGIN_VERSION = "2.0.3"
PLUGIN_NAME = "Edit / Export / Import Tickingarea (Bedrock)"

KEY_PREFIX = "tickingarea_"
PLACEHOLDER_KEY = "Tickingarea in this world"
NO_SEARCH_RESULTS = "(No search matches — clear search or type a key manually)"

# Minimum combo width; combo expands horizontally with the operations panel.
KEY_COMBO_MIN_WIDTH = 280
SNBT_MIN_HEIGHT = 220
SEARCH_DEBOUNCE_MS = 120
SNBT_PARSE_DEBOUNCE_MS = 280
FIELD_PUSH_DEBOUNCE_MS = 60

_WIN_INVALID_FILE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

FIELD_ORDER: List[str] = [
    "Dimension",
    "EntityId",
    "IsAlwaysActive",
    "IsCircle",
    "MaxDistToPlayers",
    "MaxX",
    "MaxZ",
    "MinX",
    "MinZ",
    "Name",
]


def _remove_newlines_outside_quotes(snbt: str) -> str:
    out: List[str] = []
    i = 0
    n = len(snbt)
    in_string = False
    quote = ""
    escape = False
    while i < n:
        c = snbt[i]
        if escape:
            out.append(c)
            escape = False
            i += 1
            continue
        if in_string:
            if c == "\\":
                escape = True
                out.append(c)
            elif c == quote:
                in_string = False
                out.append(c)
            else:
                out.append(c)
            i += 1
            continue
        if c in ('"', "'"):
            in_string = True
            quote = c
            out.append(c)
            i += 1
            continue
        if c in "\n\r":
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _format_tag_snbt(tag: Any, minify: bool) -> str:
    if minify:
        s = tag.to_snbt(None)
        if "\n" in s or "\r" in s:
            s = _remove_newlines_outside_quotes(s)
        return s
    return tag.to_snbt(1)


def _get_root_compound(nbt: Any) -> CompoundTag:
    if hasattr(nbt, "compound"):
        return nbt.compound
    if isinstance(nbt, CompoundTag):
        return nbt
    return nbt


def _byte_to_bool(tag: Any) -> Optional[bool]:
    if tag is None:
        return None
    try:
        v = int(tag)
        return bool(v)
    except Exception:
        return None


def _safe_export_basename(key: str) -> str:
    if not key.startswith(KEY_PREFIX):
        return "tickingarea"
    rest = key[len(KEY_PREFIX) :]
    name = _WIN_INVALID_FILE.sub("_", rest)
    return name or "tickingarea"


def _is_valid_key(key: str) -> bool:
    if key in (PLACEHOLDER_KEY, NO_SEARCH_RESULTS):
        return False
    return key.startswith(KEY_PREFIX) and len(key) > len(KEY_PREFIX)


def _extract_key_from_combo_text(s: str) -> str:
    s = (s or "").strip()
    if " | " in s:
        return s.split(" | ", 1)[0].strip()
    m = re.match(r"^(tickingarea_[^\s|]+)", s)
    if m:
        return m.group(1)
    return s


class TickingAreaDrop(wx.FileDropTarget):
    def __init__(self, panel: "SetBlock"):
        super().__init__()
        self.panel = panel

    def OnDropFiles(self, x: int, y: int, filenames: List[str]) -> bool:
        for path in filenames:
            if path.lower().endswith((".nbt", ".snbt")):
                self.panel.import_file(path, confirm_discard=True)
                break
        return True


class SetBlock(wx.Panel, DefaultOperationUI):
    def __init__(
        self,
        parent: wx.Window,
        canvas: "EditCanvas",
        world: "BaseLevel",
        options_path: str,
    ):
        wx.Panel.__init__(self, parent)
        DefaultOperationUI.__init__(self, parent, canvas, world, options_path)

        self.all_keys: List[str] = []
        self.filtered_keys: List[str] = []
        self._display_for_key: Dict[str, str] = {}
        self._parsed_compound: Optional[CompoundTag] = None

        self.snbt_dirty = False
        self._session_key = PLACEHOLDER_KEY
        self._search_timer: Optional[wx.CallLater] = None
        self._snbt_parse_timer: Optional[wx.CallLater] = None
        self._field_push_timer: Optional[wx.CallLater] = None
        self._loading_snbt = False
        self._loading_fields = False

        self._field_texts: Dict[str, wx.TextCtrl] = {}
        self._field_checks: Dict[str, wx.CheckBox] = {}

        self._build_ui()
        self.SetDropTarget(TickingAreaDrop(self))

        self._index_areas()
        self.filtered_keys = list(self.all_keys)
        self._fill_combo_choices()
        self._session_key = _extract_key_from_combo_text(self.key_combo.GetValue())

    def _build_ui(self) -> None:
        main = wx.BoxSizer(wx.VERTICAL)

        main.Add(
            wx.StaticText(
                self,
                label=(
                    f"{PLUGIN_NAME} — v{PLUGIN_VERSION}\n"
                    "Bedrock only: ticking areas use LevelDB keys starting with tickingarea_. "
                    "Java edition is not supported."
                ),
            ),
            0,
            wx.EXPAND | wx.ALL,
            6,
        )

        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(self, label="Search:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.search = wx.TextCtrl(self, size=(180, -1))
        self.search.Bind(wx.EVT_TEXT, self._on_search_text)
        row.Add(self.search, 1, wx.RIGHT | wx.EXPAND, 8)
        self.case_checkbox = wx.CheckBox(self, label="Case sensitive")
        self.case_checkbox.SetValue(False)
        self.case_checkbox.Bind(wx.EVT_CHECKBOX, self._on_search_changed)
        row.Add(self.case_checkbox, 0, wx.ALIGN_CENTER_VERTICAL)
        main.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)

        main.Add(
            wx.StaticText(self, label="Ticking area key (list shows Name + Circle/Rectangle):"),
            0,
            wx.LEFT,
            6,
        )

        key_col = wx.BoxSizer(wx.VERTICAL)
        self.key_combo = wx.ComboBox(self, style=wx.CB_DROPDOWN)
        self.key_combo.SetMinSize((KEY_COMBO_MIN_WIDTH, -1))
        self.key_combo.Bind(wx.EVT_COMBOBOX, self._on_combo_selected)
        self.key_combo.Bind(wx.EVT_TEXT, self._on_key_text)
        key_col.Add(self.key_combo, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        key_btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.get_button = wx.Button(self, label="Get DATA")
        self.get_button.Bind(wx.EVT_BUTTON, self._run_get_sdata)
        key_btn_row.Add(self.get_button, 0, wx.RIGHT, 8)

        self.auto_load = wx.CheckBox(self, label="Auto-load when selection changes")
        self.auto_load.SetValue(True)
        key_btn_row.Add(self.auto_load, 0, wx.ALIGN_CENTER_VERTICAL)
        key_col.Add(key_btn_row, 0, wx.LEFT | wx.TOP | wx.BOTTOM, 6)

        main.Add(key_col, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 2)

        fields_box = wx.StaticBox(self, label="Fields (synced with SNBT when valid)")
        fields_sizer = wx.StaticBoxSizer(fields_box, wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, hgap=8, vgap=4)
        grid.AddGrowableCol(1, 1)

        for fname in FIELD_ORDER:
            grid.Add(wx.StaticText(self, label=f'{fname}:'), 0, wx.ALIGN_CENTER_VERTICAL)
            if fname in ("IsAlwaysActive", "IsCircle"):
                cb = wx.CheckBox(self, label="")
                cb.Bind(wx.EVT_CHECKBOX, self._on_any_field)
                self._field_checks[fname] = cb
                grid.Add(cb, 0, wx.EXPAND)
            else:
                tc = wx.TextCtrl(self)
                tc.Bind(wx.EVT_TEXT, self._on_any_field)
                self._field_texts[fname] = tc
                grid.Add(tc, 0, wx.EXPAND)

        fields_sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 6)
        main.Add(fields_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        self.minify_checkbox = wx.CheckBox(self, label="Minify SNBT (single-line)")
        self.minify_checkbox.SetValue(False)
        self.minify_checkbox.Bind(wx.EVT_CHECKBOX, self._on_minify_toggle)
        main.Add(self.minify_checkbox, 0, wx.LEFT | wx.BOTTOM, 2)
        main.Add(wx.StaticText(self, label="SNBT:"), 0, wx.LEFT, 6)
        self.data_text = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.HSCROLL)
        self.data_text.SetMinSize((-1, SNBT_MIN_HEIGHT))
        self.data_text.Bind(wx.EVT_TEXT, self._on_snbt_edit)
        main.Add(self.data_text, 1, wx.EXPAND | wx.ALL, 6)

        btn = wx.BoxSizer(wx.HORIZONTAL)
        self.save_button = wx.Button(self, label="Save Tickingarea Data to world")
        self.save_button.Bind(wx.EVT_BUTTON, self._run_set_sdata)
        self.export_button = wx.Button(self, label="Export NBT")
        self.export_button.Bind(wx.EVT_BUTTON, self._run_export)
        self.import_button = wx.Button(self, label="Import NBT")
        self.import_button.Bind(wx.EVT_BUTTON, self._run_import)
        self.delete_button = wx.Button(self, label="DELETE from World")
        self.delete_button.Bind(wx.EVT_BUTTON, self._run_Del)

        btn.Add(self.save_button, 0, wx.RIGHT, 6)
        btn.Add(self.export_button, 0, wx.RIGHT, 6)
        btn.Add(self.import_button, 0, wx.RIGHT, 6)
        btn.Add(self.delete_button, 0)
        main.Add(btn, 0, wx.ALL, 6)

        self.SetSizer(main)

    @property
    def level_db(self):
        lw = self.world.level_wrapper
        if hasattr(lw, "level_db"):
            return lw.level_db
        return lw._level_manager._db

    # --- metadata for list -------------------------------------------------

    def _meta_for_key_bytes(self, key_b: bytes) -> Tuple[str, bool, str]:
        """Returns (name, is_circle, display_line)."""
        k = key_b.decode("utf-8", errors="replace")
        try:
            raw = self.level_db.get(key_b)
            if raw is None:
                return "", False, f"{k} | ? | ?"
            nbt = amulet_nbt.load(raw, little_endian=True)
            root = _get_root_compound(nbt)
            name = ""
            t = root.get("Name")
            if t is not None:
                try:
                    name = str(getattr(t, "py_data", t))
                except Exception:
                    name = ""
            circ = False
            t = root.get("IsCircle")
            if t is not None:
                try:
                    circ = bool(int(t))
                except Exception:
                    pass
            kind = "Circle" if circ else "Rectangle"
            disp_name = name if name else "(empty name)"
            if len(disp_name) > 48:
                disp_name = disp_name[:47] + "…"
            line = f"{k} | {kind} | {disp_name}"
            return name, circ, line
        except Exception:
            return "", False, f"{k} | ? | ?"

    def _index_areas(self) -> None:
        self.all_keys.clear()
        self._display_for_key.clear()
        for w in self.level_db.keys():
            if b"\xff" in w or b"\x00" in w:
                continue
            if b"tickingarea_" in w:
                self.all_keys.append(w.decode("utf-8", errors="replace"))
        self.all_keys.sort()
        for k in self.all_keys:
            _, _, line = self._meta_for_key_bytes(k.encode("utf-8"))
            self._display_for_key[k] = line

    def _display_line(self, key: str) -> str:
        return self._display_for_key.get(key, key)

    def _apply_search_filter(self) -> None:
        raw = self.search.GetValue()
        self.filtered_keys.clear()
        if self.case_checkbox.GetValue():
            needle = raw
            for k in self.all_keys:
                disp = self._display_for_key.get(k, k)
                if needle in disp or needle in k:
                    self.filtered_keys.append(k)
        else:
            needle = raw.lower()
            for k in self.all_keys:
                disp = self._display_for_key.get(k, k)
                if needle in disp.lower() or needle in k.lower():
                    self.filtered_keys.append(k)
        self._fill_combo_choices(preserve_key=True)

    def _on_search_text(self, _evt: wx.Event) -> None:
        if self._search_timer is not None:
            self._search_timer.Stop()
        self._search_timer = wx.CallLater(SEARCH_DEBOUNCE_MS, self._apply_search_filter)

    def _on_search_changed(self, _evt: wx.Event) -> None:
        self._apply_search_filter()

    def _combo_contains_line_for_key(self, key: str) -> bool:
        line = self._display_line(key)
        for i in range(self.key_combo.GetCount()):
            if self.key_combo.GetString(i) == line:
                return True
        if self._combo_contains(key):
            return True
        return False

    def _combo_contains(self, text: str) -> bool:
        for i in range(self.key_combo.GetCount()):
            if self.key_combo.GetString(i) == text:
                return True
        return False

    def _ensure_key_in_combo(self, key: str) -> None:
        if not key:
            key = PLACEHOLDER_KEY
        if key in (PLACEHOLDER_KEY, NO_SEARCH_RESULTS):
            self._loading_snbt = True
            self.key_combo.SetValue(key)
            self._loading_snbt = False
            return
        line = self._display_line(key)
        if self._combo_contains(line):
            self._loading_snbt = True
            self.key_combo.SetValue(line)
            self._loading_snbt = False
            return
        if self._combo_contains(key):
            self._loading_snbt = True
            self.key_combo.SetValue(key)
            self._loading_snbt = False
            return
        self._loading_snbt = True
        self.key_combo.Append(line)
        self.key_combo.SetValue(line)
        self._loading_snbt = False

    def _fill_combo_choices(self, preserve_key: bool = False) -> None:
        if preserve_key:
            typed = (self.key_combo.GetValue() or "").strip()
            cur_key = _extract_key_from_combo_text(typed) if typed else self._session_key
            if not cur_key:
                cur_key = PLACEHOLDER_KEY
        else:
            cur_key = ""

        self.key_combo.Clear()
        self.key_combo.Append(PLACEHOLDER_KEY)

        if not self.filtered_keys:
            if self.search.GetValue().strip():
                self.key_combo.Append(NO_SEARCH_RESULTS)
        else:
            for k in self.filtered_keys:
                self.key_combo.Append(self._display_line(k))

        if preserve_key and cur_key:
            if cur_key not in (PLACEHOLDER_KEY, NO_SEARCH_RESULTS) and not self._combo_contains_line_for_key(
                cur_key
            ):
                self.key_combo.Append(self._display_line(cur_key))
            self._ensure_key_in_combo(cur_key)
            return

        self.key_combo.SetSelection(0)
        self._ensure_key_in_combo(PLACEHOLDER_KEY)

    # --- SNBT <-> fields ----------------------------------------------------

    def _clear_fields(self) -> None:
        self._loading_fields = True
        try:
            for n, tc in self._field_texts.items():
                tc.SetValue("")
            for n, cb in self._field_checks.items():
                cb.SetValue(False)
        finally:
            self._loading_fields = False

    def _sync_fields_from_compound(self, root: CompoundTag) -> None:
        self._loading_fields = True
        try:
            for fname in FIELD_ORDER:
                if fname in self._field_checks:
                    cb = self._field_checks[fname]
                    if fname not in root:
                        cb.SetValue(False)
                    else:
                        b = _byte_to_bool(root[fname])
                        cb.SetValue(bool(b))
                else:
                    tc = self._field_texts[fname]
                    if fname not in root:
                        tc.SetValue("")
                        continue
                    tag = root[fname]
                    try:
                        if fname == "Name":
                            tc.SetValue(str(getattr(tag, "py_data", tag)))
                        elif fname == "EntityId":
                            tc.SetValue(str(int(tag)))
                        elif fname == "MaxDistToPlayers":
                            tc.SetValue(str(float(tag)))
                        else:
                            tc.SetValue(str(int(tag)))
                    except Exception:
                        tc.SetValue("")
        finally:
            self._loading_fields = False

    def _apply_fields_to_compound(self) -> None:
        if self._parsed_compound is None:
            self._parsed_compound = CompoundTag()
        root = self._parsed_compound

        for fname in FIELD_ORDER:
            if fname in self._field_checks:
                cb = self._field_checks[fname]
                root[fname] = ByteTag(1 if cb.GetValue() else 0)
            else:
                tc = self._field_texts[fname]
                s = tc.GetValue().strip()
                if fname == "EntityId":
                    if not s:
                        root.pop("EntityId", None)
                    else:
                        root["EntityId"] = LongTag(int(s))
                elif fname == "Name":
                    root["Name"] = StringTag(s)
                elif fname == "MaxDistToPlayers":
                    if not s:
                        root.pop("MaxDistToPlayers", None)
                    else:
                        root["MaxDistToPlayers"] = FloatTag(float(s))
                else:
                    if not s:
                        root.pop(fname, None)
                    else:
                        root[fname] = IntTag(int(s))

    def _push_fields_to_snbt(self) -> None:
        if self._loading_fields:
            return
        try:
            self._apply_fields_to_compound()
            if self._parsed_compound is None:
                return
            self._loading_snbt = True
            self.data_text.SetValue(_format_tag_snbt(self._parsed_compound, self.minify_checkbox.GetValue()))
            self._loading_snbt = False
            self.snbt_dirty = True
            if self._session_key.startswith(KEY_PREFIX) and len(self._session_key) > len(KEY_PREFIX):
                self._update_display_line_for_key(self._session_key, self._parsed_compound)
                self._ensure_key_in_combo(self._session_key)
        except Exception as e:
            wx.MessageBox(f"Could not apply fields to NBT:\n{e}", "Error", wx.OK | wx.ICON_ERROR)

    def _schedule_field_push(self) -> None:
        if self._field_push_timer is not None:
            self._field_push_timer.Stop()
        self._field_push_timer = wx.CallLater(FIELD_PUSH_DEBOUNCE_MS, self._push_fields_to_snbt)

    def _on_any_field(self, _evt: wx.Event) -> None:
        if self._loading_fields:
            return
        self._schedule_field_push()

    def _schedule_snbt_parse(self) -> None:
        if self._snbt_parse_timer is not None:
            self._snbt_parse_timer.Stop()
        self._snbt_parse_timer = wx.CallLater(SNBT_PARSE_DEBOUNCE_MS, self._try_parse_snbt_to_fields)

    def _try_parse_snbt_to_fields(self) -> None:
        if self._loading_snbt:
            return
        try:
            tag = from_snbt(self.data_text.GetValue())
            root = _get_root_compound(tag)
            self._parsed_compound = root
            self._sync_fields_from_compound(root)
        except Exception:
            self._parsed_compound = None
            self._clear_fields()

    def _on_snbt_edit(self, evt: wx.Event) -> None:
        if self._loading_snbt:
            evt.Skip()
            return
        self.snbt_dirty = True
        self._schedule_snbt_parse()
        evt.Skip()

    def _on_minify_toggle(self, _evt: wx.Event) -> None:
        try:
            tag = from_snbt(self.data_text.GetValue())
            root = _get_root_compound(tag)
            self._parsed_compound = root
            self._loading_snbt = True
            self.data_text.SetValue(_format_tag_snbt(root, self.minify_checkbox.GetValue()))
            self._loading_snbt = False
            self.snbt_dirty = False
            self._sync_fields_from_compound(root)
        except Exception:
            wx.MessageBox("Could not parse SNBT for Minify.", "Error", wx.OK | wx.ICON_ERROR)

    # --- key UI ------------------------------------------------------------

    def _confirm_discard_if_dirty(self, action: str) -> bool:
        if not self.snbt_dirty:
            return True
        dlg = wx.MessageDialog(
            self,
            (
                "Data was edited and not saved to the world.\n\n"
                f"Action: {action}\n\n"
                "Yes — discard edits and continue.\n"
                "No — stay."
            ),
            "Unsaved changes",
            wx.YES_NO | wx.ICON_WARNING,
        )
        try:
            dlg.SetYesNoLabels("Discard", "Stay")
        except Exception:
            pass
        r = dlg.ShowModal()
        dlg.Destroy()
        return r == wx.ID_YES

    def _on_key_text(self, evt: wx.Event) -> None:
        if self._loading_snbt:
            evt.Skip()
            return
        v = self.key_combo.GetValue()
        if v == NO_SEARCH_RESULTS:
            evt.Skip()
            return
        ek = _extract_key_from_combo_text(v)
        if not ek or ek == PLACEHOLDER_KEY:
            self._session_key = PLACEHOLDER_KEY
        else:
            self._session_key = ek
        evt.Skip()

    def _on_combo_selected(self, evt: wx.Event) -> None:
        raw = self.key_combo.GetValue()
        new_key = _extract_key_from_combo_text(raw)
        if new_key == NO_SEARCH_RESULTS:
            self._ensure_key_in_combo(self._session_key)
            evt.Skip()
            return
        if new_key == PLACEHOLDER_KEY:
            if new_key == self._session_key:
                evt.Skip()
                return
            if not self._confirm_discard_if_dirty("switching to template"):
                self._ensure_key_in_combo(self._session_key)
                evt.Skip()
                return
            self.snbt_dirty = False
            self._session_key = PLACEHOLDER_KEY
            evt.Skip()
            return
        if new_key == self._session_key:
            evt.Skip()
            return
        if not self._confirm_discard_if_dirty("switching ticking area"):
            self._ensure_key_in_combo(self._session_key)
            evt.Skip()
            return
        self.snbt_dirty = False
        self._session_key = new_key
        if self.auto_load.GetValue():
            self._run_get_sdata(None, skip_dirty_check=True)
        evt.Skip()

    def _run_get_sdata(self, _, skip_dirty_check: bool = False) -> None:
        key = _extract_key_from_combo_text(self.key_combo.GetValue())
        if key in (PLACEHOLDER_KEY, NO_SEARCH_RESULTS):
            return
        if not skip_dirty_check and not self._confirm_discard_if_dirty("loading from world"):
            return
        if not key.startswith(KEY_PREFIX):
            wx.MessageBox("Key must start with tickingarea_", "Invalid key", wx.OK | wx.ICON_WARNING)
            return
        raw = self.level_db.get(key.encode("utf-8"))
        if raw is None:
            wx.MessageBox("Key not found in LevelDB.", "No data", wx.OK | wx.ICON_INFORMATION)
            return
        nbt = amulet_nbt.load(raw, little_endian=True)
        root = _get_root_compound(nbt)
        self._parsed_compound = root
        self._loading_snbt = True
        self.data_text.SetValue(_format_tag_snbt(root, self.minify_checkbox.GetValue()))
        self._loading_snbt = False
        self._sync_fields_from_compound(root)
        self.snbt_dirty = False
        self._session_key = key
        self._update_display_line_for_key(key, root)
        self._ensure_key_in_combo(key)

    def _run_set_sdata(self, _) -> None:
        key = _extract_key_from_combo_text(self.key_combo.GetValue())
        if not _is_valid_key(key):
            wx.MessageBox(
                "Invalid key. Expected: tickingarea_<id>\nExample: tickingarea_myfile",
                "Invalid key",
                wx.OK | wx.ICON_WARNING,
            )
            return
        try:
            payload = from_snbt(self.data_text.GetValue()).save_to(compressed=False, little_endian=True)
        except Exception as e:
            wx.MessageBox(f"SNBT parse error:\n{e}", "Error", wx.OK | wx.ICON_ERROR)
            return
        self.level_db.put(key.encode("utf-8"), payload)
        self._index_areas()
        self._apply_search_filter()
        self.snbt_dirty = False
        wx.MessageBox("Saved to the world.", "Done", wx.OK | wx.ICON_INFORMATION)

    def _run_export(self, _) -> None:
        try:
            payload = from_snbt(self.data_text.GetValue()).save_to(compressed=False, little_endian=True)
        except Exception as e:
            wx.MessageBox(f"SNBT parse error:\n{e}", "Error", wx.OK | wx.ICON_ERROR)
            return
        key = _extract_key_from_combo_text(self.key_combo.GetValue())
        base = _safe_export_basename(key)
        wildcard = "NBT (*.nbt)|*.nbt|SNBT text (*.snbt)|*.snbt|All files (*.*)|*.*"
        with wx.FileDialog(
            self,
            "Save ticking area",
            defaultFile=f"{base}.nbt",
            wildcard=wildcard,
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            path = dlg.GetPath()
        with open(path, "wb") as f:
            f.write(payload)

    def _run_import(self, _) -> None:
        if not self._confirm_discard_if_dirty("importing a file"):
            return
        wildcard = "NBT (*.nbt)|*.nbt|SNBT (*.snbt)|*.snbt|All files (*.*)|*.*"
        with wx.FileDialog(self, "Open NBT", wildcard=wildcard, style=wx.FD_OPEN) as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            self.import_file(dlg.GetPath(), confirm_discard=False)

    def import_file(self, path: str, confirm_discard: bool = False) -> None:
        if confirm_discard and not self._confirm_discard_if_dirty("importing a file"):
            return
        with open(path, "rb") as f:
            data = f.read()
        nbt = amulet_nbt.load(data, little_endian=True)
        root = _get_root_compound(nbt)
        self._parsed_compound = root
        self._loading_snbt = True
        self.data_text.SetValue(_format_tag_snbt(root, self.minify_checkbox.GetValue()))
        self._loading_snbt = False
        self._sync_fields_from_compound(root)
        stem = Path(path).stem
        stem = _WIN_INVALID_FILE.sub("_", stem)
        new_key = f"{KEY_PREFIX}{stem}"
        self._session_key = new_key
        self._update_display_line_for_key(new_key, root)
        self._ensure_key_in_combo(new_key)
        self.snbt_dirty = True

    def _update_display_line_for_key(self, key: str, root: CompoundTag) -> None:
        name = ""
        t = root.get("Name")
        if t is not None:
            try:
                name = str(getattr(t, "py_data", t))
            except Exception:
                name = ""
        circ = False
        t = root.get("IsCircle")
        if t is not None:
            try:
                circ = bool(int(t))
            except Exception:
                pass
        kind = "Circle" if circ else "Rectangle"
        disp_name = name if name else "(empty name)"
        if len(disp_name) > 48:
            disp_name = disp_name[:47] + "…"
        self._display_for_key[key] = f"{key} | {kind} | {disp_name}"

    def _run_Del(self, _) -> None:
        key = _extract_key_from_combo_text(self.key_combo.GetValue())
        if key in (PLACEHOLDER_KEY, NO_SEARCH_RESULTS) or not key.startswith(KEY_PREFIX):
            wx.MessageBox("Enter a valid ticking area key.", "Error", wx.OK | wx.ICON_WARNING)
            return
        if not self._confirm_discard_if_dirty("deleting"):
            return
        if (
            wx.MessageBox(
                f"Delete from the world?\n\n{key}",
                "Confirm",
                wx.OK | wx.CANCEL | wx.ICON_WARNING,
            )
            != wx.OK
        ):
            return
        self.level_db.delete(key.encode("utf-8"))
        self._session_key = PLACEHOLDER_KEY
        self._loading_snbt = True
        self.key_combo.SetValue(PLACEHOLDER_KEY)
        self._loading_snbt = False
        self._parsed_compound = None
        self.data_text.SetValue("")
        self._clear_fields()
        self._index_areas()
        self._apply_search_filter()
        self.snbt_dirty = False
        wx.MessageBox("Deleted.", "Done", wx.OK | wx.ICON_INFORMATION)


export = dict(
    name=f"v002 Edit/Export/Import Tickingarea v{PLUGIN_VERSION}",
    operation=SetBlock,
)
#Based on PremierHell's code and modified by MaxRM