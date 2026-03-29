# -*- coding: utf-8 -*-
"""
Bedrock LevelDB: export / import / edit saved structures (structuretemplate_* keys).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

import wx
import amulet_nbt
from amulet_nbt import from_snbt

from amulet_map_editor.programs.edit.api.operations import DefaultOperationUI

if TYPE_CHECKING:
    from amulet.api.level import BaseLevel
    from amulet_map_editor.programs.edit.api.canvas import EditCanvas

# ---------------------------------------------------------------------------
PLUGIN_VERSION = "2.1.3"
PLUGIN_NAME = "Edit / Export / Import Mcstructure (Bedrock)"

# Same template as legacy 001 plugin — not a valid LevelDB key (prompt only).
PLACEHOLDER_KEY = "Mcstructure in this world"
NO_SEARCH_RESULTS = "(No search matches — clear search or type a key manually)"

STRUCT_COMBO_WIDTH = 280
SNBT_MIN_HEIGHT = 320
SEARCH_DEBOUNCE_MS = 120

_WIN_INVALID_FILE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _remove_newlines_outside_quotes(snbt: str) -> str:
    """
    Strip CR/LF outside quoted SNBT string literals so minified output is one line.
    Newlines inside "..." or '...' are preserved (escape-aware).
    """
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


def _format_tag_snbt(tag, minify: bool) -> str:
    """Pretty (indented) or true one-line minified SNBT."""
    if minify:
        s = tag.to_snbt(None)
        if "\n" in s or "\r" in s:
            s = _remove_newlines_outside_quotes(s)
        return s
    return tag.to_snbt(1)


def _safe_export_basename_from_key(key: str) -> str:
    if not key.startswith("structuretemplate_"):
        return "structure"
    rest = key[len("structuretemplate_") :]
    if ":" in rest:
        name = rest.split(":", 1)[1]
    else:
        name = rest
    name = name.replace(":", "__")
    name = _WIN_INVALID_FILE.sub("_", name)
    return name or "structure"


def _structure_display_name(key: str) -> str:
    if not key.startswith("structuretemplate_"):
        return key
    rest = key[len("structuretemplate_") :]
    if ":" in rest:
        return rest.split(":", 1)[1]
    return rest


def _import_name_from_filename_stem(stem: str) -> str:
    return stem.replace("__", ":")


def _is_valid_structure_key(key: str) -> bool:
    if key == PLACEHOLDER_KEY or key == NO_SEARCH_RESULTS:
        return False
    if not key.startswith("structuretemplate_"):
        return False
    if ":" not in key:
        return False
    rest = key[len("structuretemplate_") :]
    if ":" not in rest:
        return False
    name = rest.split(":", 1)[1]
    return len(name) > 0


def _is_placeholder_or_no_results(key: str) -> bool:
    return key == PLACEHOLDER_KEY or key == NO_SEARCH_RESULTS


class StructureDrop(wx.FileDropTarget):
    def __init__(self, panel: "SetBlock"):
        super().__init__()
        self.panel = panel

    def OnDropFiles(self, x: int, y: int, filenames: List[str]) -> bool:
        for path in filenames:
            low = path.lower()
            if low.endswith(".mcstructure") or low.endswith(".nbt"):
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

        self.all_structures: List[str] = []
        self.filtered_structures: List[str] = []
        self.snbt_dirty = False
        self._session_key = PLACEHOLDER_KEY
        self._search_timer: Optional[wx.CallLater] = None
        self._stats_timer: Optional[wx.CallLater] = None
        self._loading_snbt = False

        self._build_ui()
        self.SetDropTarget(StructureDrop(self))

        self._index_structures()
        self.filtered_structures = list(self.all_structures)
        self._fill_combo_choices()
        self._session_key = self.key_combo.GetValue()

    def _build_ui(self) -> None:
        main = wx.BoxSizer(wx.VERTICAL)

        info = wx.StaticText(
            self,
            label=(
                f"{PLUGIN_NAME} — v{PLUGIN_VERSION}\n"
                "Bedrock only: structures live in LevelDB (keys starting with structuretemplate_). "
                "Java edition is not supported."
            ),
        )
        main.Add(info, 0, wx.EXPAND | wx.ALL, 6)

        search_row = wx.BoxSizer(wx.HORIZONTAL)
        search_row.Add(wx.StaticText(self, label="Search by name:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.search = wx.TextCtrl(self, size=(180, -1))
        self.search.Bind(wx.EVT_TEXT, self._on_search_text)
        search_row.Add(self.search, 1, wx.RIGHT | wx.EXPAND, 8)
        self.case_checkbox = wx.CheckBox(self, label="Case sensitive")
        self.case_checkbox.SetValue(False)
        self.case_checkbox.Bind(wx.EVT_CHECKBOX, self._on_search_changed)
        search_row.Add(self.case_checkbox, 0, wx.ALIGN_CENTER_VERTICAL)
        main.Add(search_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)

        main.Add(
            wx.StaticText(self, label="Structure key (pick from list or type a full key):"),
            0,
            wx.LEFT,
            6,
        )

        key_row = wx.BoxSizer(wx.HORIZONTAL)
        self.key_combo = wx.ComboBox(
            self,
            style=wx.CB_DROPDOWN,
            size=(STRUCT_COMBO_WIDTH, -1),
        )
        self.key_combo.SetMaxSize((STRUCT_COMBO_WIDTH, -1))
        self.key_combo.Bind(wx.EVT_COMBOBOX, self._on_combo_selected)
        self.key_combo.Bind(wx.EVT_TEXT, self._on_key_text)
        key_row.Add(self.key_combo, 0, wx.RIGHT, 8)

        self.get_button = wx.Button(self, label="Get DATA")
        self.get_button.Bind(wx.EVT_BUTTON, self._run_get_sdata)
        key_row.Add(self.get_button, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

        self.auto_load = wx.CheckBox(self, label="Auto-load SNBT when the selection changes")
        self.auto_load.SetValue(True)
        key_row.Add(self.auto_load, 0, wx.ALIGN_CENTER_VERTICAL)

        main.Add(key_row, 0, wx.LEFT | wx.BOTTOM, 6)

        self.minify_checkbox = wx.CheckBox(self, label="Minify SNBT (single-line)")
        self.minify_checkbox.SetValue(False)
        self.minify_checkbox.Bind(wx.EVT_CHECKBOX, self._on_minify_toggle)
        main.Add(self.minify_checkbox, 0, wx.LEFT | wx.BOTTOM, 4)

        self.stats_label = wx.StaticText(self, label="Size: —   Block volume: —")
        main.Add(self.stats_label, 0, wx.LEFT | wx.BOTTOM, 4)

        main.Add(wx.StaticText(self, label="Structure SNBT:"), 0, wx.LEFT, 6)

        self.data_text = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.HSCROLL,
        )
        self.data_text.SetMinSize((STRUCT_COMBO_WIDTH, SNBT_MIN_HEIGHT))
        self.data_text.Bind(wx.EVT_TEXT, self._on_snbt_edit)
        main.Add(self.data_text, 1, wx.EXPAND | wx.ALL, 6)

        btn = wx.BoxSizer(wx.HORIZONTAL)
        self.save_button = wx.Button(self, label="Save Mcstructure Data to world")
        self.save_button.Bind(wx.EVT_BUTTON, self._run_set_sdata)
        self.export_button = wx.Button(self, label="Export Mcstructure NBT")
        self.export_button.Bind(wx.EVT_BUTTON, self._run_export)
        self.import_button = wx.Button(self, label="Import Mcstructure NBT")
        self.import_button.Bind(wx.EVT_BUTTON, self._run_import)
        self.delete_button = wx.Button(self, label="DELETE Mcstructure from World")
        self.delete_button.Bind(wx.EVT_BUTTON, self._run_Del)

        btn.Add(self.save_button, 0, wx.RIGHT, 6)
        btn.Add(self.export_button, 0, wx.RIGHT, 6)
        btn.Add(self.import_button, 0, wx.RIGHT, 6)
        btn.Add(self.delete_button, 0)
        main.Add(btn, 0, wx.ALL, 6)

        self.SetSizer(main)

    @property
    def level_db(self):
        level_wrapper = self.world.level_wrapper
        if hasattr(level_wrapper, "level_db"):
            return level_wrapper.level_db
        return level_wrapper._level_manager._db

    def _index_structures(self) -> None:
        self.all_structures.clear()
        for k in self.level_db.keys():
            if b"\xff" in k or b"\x00" in k:
                continue
            if b"structuretemplate_" in k:
                self.all_structures.append(k.decode("utf-8", errors="replace"))
        self.all_structures.sort()

    def _apply_search_filter(self) -> None:
        raw = self.search.GetValue()
        if self.case_checkbox.GetValue():
            needle = raw
            self.filtered_structures = [
                s for s in self.all_structures if needle in _structure_display_name(s)
            ]
        else:
            needle = raw.lower()
            self.filtered_structures = [
                s for s in self.all_structures if needle in _structure_display_name(s).lower()
            ]
        self._fill_combo_choices(preserve_key=True)

    def _on_search_text(self, _evt: wx.Event) -> None:
        if self._search_timer is not None:
            self._search_timer.Stop()
        self._search_timer = wx.CallLater(SEARCH_DEBOUNCE_MS, self._apply_search_filter)

    def _on_search_changed(self, _evt: wx.Event) -> None:
        self._apply_search_filter()

    def _combo_contains(self, text: str) -> bool:
        for i in range(self.key_combo.GetCount()):
            if self.key_combo.GetString(i) == text:
                return True
        return False

    def _ensure_key_in_combo(self, key: str) -> None:
        """wx.ComboBox can show blank if SetValue is not in the dropdown list — append if needed."""
        if not key:
            key = PLACEHOLDER_KEY
        if key in (PLACEHOLDER_KEY, NO_SEARCH_RESULTS):
            self._loading_snbt = True
            self.key_combo.SetValue(key)
            self._loading_snbt = False
            return
        if self._combo_contains(key):
            self._loading_snbt = True
            self.key_combo.SetValue(key)
            self._loading_snbt = False
            return
        self._loading_snbt = True
        self.key_combo.Append(key)
        self.key_combo.SetValue(key)
        self._loading_snbt = False

    def _fill_combo_choices(self, preserve_key: bool = False) -> None:
        if preserve_key:
            typed = (self.key_combo.GetValue() or "").strip()
            cur = typed if typed else self._session_key
            if not cur:
                cur = PLACEHOLDER_KEY
        else:
            cur = ""
        self.key_combo.Clear()
        self.key_combo.Append(PLACEHOLDER_KEY)

        if not self.filtered_structures:
            if self.search.GetValue().strip():
                self.key_combo.Append(NO_SEARCH_RESULTS)
        else:
            for s in self.filtered_structures:
                self.key_combo.Append(s)

        if preserve_key and cur:
            if cur not in (PLACEHOLDER_KEY, NO_SEARCH_RESULTS) and not self._combo_contains(cur):
                self.key_combo.Append(cur)
            self._ensure_key_in_combo(cur)
            return

        self.key_combo.SetSelection(0)
        self._ensure_key_in_combo(PLACEHOLDER_KEY)

    def _confirm_discard_if_dirty(self, action: str) -> bool:
        if not self.snbt_dirty:
            return True
        dlg = wx.MessageDialog(
            self,
            (
                "SNBT was edited and not saved to the world.\n\n"
                f"Action: {action}\n\n"
                "Yes — discard edits and continue.\n"
                "No — stay on the current structure."
            ),
            "Unsaved changes",
            wx.YES_NO | wx.ICON_WARNING,
        )
        try:
            dlg.SetYesNoLabels("Discard edits", "Stay")
        except Exception:
            pass
        r = dlg.ShowModal()
        dlg.Destroy()
        return r == wx.ID_YES

    def _on_snbt_edit(self, evt: wx.Event) -> None:
        if self._loading_snbt:
            evt.Skip()
            return
        self.snbt_dirty = True
        self._schedule_stats_refresh()
        evt.Skip()

    def _on_key_text(self, evt: wx.Event) -> None:
        if self._loading_snbt:
            evt.Skip()
            return
        v = self.key_combo.GetValue()
        if v == NO_SEARCH_RESULTS:
            evt.Skip()
            return
        if not (v or "").strip():
            self._session_key = PLACEHOLDER_KEY
        else:
            self._session_key = v
        evt.Skip()

    def _on_combo_selected(self, evt: wx.Event) -> None:
        new_key = self.key_combo.GetValue()
        if new_key == NO_SEARCH_RESULTS:
            self._ensure_key_in_combo(self._session_key)
            evt.Skip()
            return
        if new_key == PLACEHOLDER_KEY:
            if new_key == self._session_key:
                evt.Skip()
                return
            if not self._confirm_discard_if_dirty("switching to the template label"):
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
        if not self._confirm_discard_if_dirty("switching the selected structure"):
            self._ensure_key_in_combo(self._session_key)
            evt.Skip()
            return
        self.snbt_dirty = False
        self._session_key = new_key
        if self.auto_load.GetValue():
            self._run_get_sdata(None, skip_dirty_check=True)
        evt.Skip()

    def _on_minify_toggle(self, _evt: wx.Event) -> None:
        try:
            tag = from_snbt(self.data_text.GetValue())
            self._loading_snbt = True
            self.data_text.SetValue(_format_tag_snbt(tag, self.minify_checkbox.GetValue()))
            self._loading_snbt = False
            self.snbt_dirty = False
            self._schedule_stats_refresh()
        except Exception:
            wx.MessageBox(
                "Could not parse SNBT for Minify toggle.",
                "Error",
                wx.OK | wx.ICON_ERROR,
            )

    def _schedule_stats_refresh(self) -> None:
        if self._stats_timer is not None:
            self._stats_timer.Stop()
        self._stats_timer = wx.CallLater(80, self._refresh_stats)

    def _refresh_stats(self) -> None:
        try:
            tag = from_snbt(self.data_text.GetValue())
            sz = self._read_size_triple(tag)
            if sz is None:
                self.stats_label.SetLabel("Size: —   Block volume: —")
                return
            x, y, z = sz
            vol = x * y * z
            self.stats_label.SetLabel(f"Size: {x} × {y} × {z}   Block volume: {vol}")
        except Exception:
            self.stats_label.SetLabel("Size: (SNBT parse error)")

    def _read_size_triple(self, tag) -> Optional[Tuple[int, int, int]]:
        root = tag
        if hasattr(tag, "compound"):
            root = tag.compound
        if not hasattr(root, "get"):
            return None
        size = root.get("size")
        if size is None:
            return None
        try:
            return (int(size[0]), int(size[1]), int(size[2]))
        except Exception:
            return None

    def _snbt_minify(self) -> bool:
        return self.minify_checkbox.GetValue()

    def _run_get_sdata(self, _, skip_dirty_check: bool = False) -> None:
        key = self.key_combo.GetValue()
        if _is_placeholder_or_no_results(key):
            return
        if not skip_dirty_check and not self._confirm_discard_if_dirty("loading data from the world"):
            return
        en = key.encode("utf-8")
        raw = self.level_db.get(en)
        if raw is None:
            wx.MessageBox("Key not found in LevelDB.", "No data", wx.OK | wx.ICON_INFORMATION)
            return
        nbt = amulet_nbt.load(raw, little_endian=True)
        self._loading_snbt = True
        self.data_text.SetValue(_format_tag_snbt(nbt, self._snbt_minify()))
        self._loading_snbt = False
        self.snbt_dirty = False
        self._session_key = key
        self._ensure_key_in_combo(key)
        self._refresh_stats()

    def _run_set_sdata(self, _) -> None:
        key = self.key_combo.GetValue()
        if not _is_valid_structure_key(key):
            wx.MessageBox(
                "Invalid structure key.\n\n"
                "Expected format:\n"
                "  structuretemplate_<descriptor>:<name>\n\n"
                "Example: structuretemplate_mystructure:house",
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
        self._index_structures()
        self._apply_search_filter()
        self.snbt_dirty = False
        wx.MessageBox("Saved to the world.", "Done", wx.OK | wx.ICON_INFORMATION)

    def _run_export(self, _) -> None:
        try:
            payload = from_snbt(self.data_text.GetValue()).save_to(compressed=False, little_endian=True)
        except Exception as e:
            wx.MessageBox(f"SNBT parse error:\n{e}", "Error", wx.OK | wx.ICON_ERROR)
            return
        key = self.key_combo.GetValue()
        base = _safe_export_basename_from_key(key)
        wildcard = (
            "Mcstructure (*.mcstructure)|*.mcstructure|"
            "NBT (*.nbt)|*.nbt|"
            "All files (*.*)|*.*"
        )
        with wx.FileDialog(
            self,
            "Save structure",
            defaultFile=f"{base}.mcstructure",
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
        wildcard = (
            "Structures (*.mcstructure;*.nbt)|*.mcstructure;*.nbt|"
            "Mcstructure (*.mcstructure)|*.mcstructure|"
            "NBT (*.nbt)|*.nbt|"
            "All files (*.*)|*.*"
        )
        with wx.FileDialog(self, "Open structure", wildcard=wildcard, style=wx.FD_OPEN) as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            self.import_file(dlg.GetPath(), confirm_discard=False)

    def import_file(self, path: str, confirm_discard: bool = False) -> None:
        if confirm_discard and not self._confirm_discard_if_dirty("importing a file"):
            return
        with open(path, "rb") as f:
            data = f.read()
        nbt = amulet_nbt.load(data, little_endian=True)
        self._loading_snbt = True
        self.data_text.SetValue(_format_tag_snbt(nbt, self._snbt_minify()))
        self._loading_snbt = False
        stem = Path(path).stem
        name = _import_name_from_filename_stem(stem)
        new_key = f"structuretemplate_mystructure:{name}"
        self._session_key = new_key
        self._ensure_key_in_combo(new_key)
        self.snbt_dirty = True
        self._refresh_stats()

    def _run_Del(self, _) -> None:
        key = self.key_combo.GetValue()
        if _is_placeholder_or_no_results(key) or not key.startswith("structuretemplate_"):
            wx.MessageBox(
                "Enter a valid structure key to delete.",
                "Error",
                wx.OK | wx.ICON_WARNING,
            )
            return
        if not self._confirm_discard_if_dirty("deleting a structure from the world"):
            return
        the_key = key.encode("utf-8")
        if (
            wx.MessageBox(
                f"Delete from the world?\n\n{key}",
                "Confirm",
                wx.OK | wx.CANCEL | wx.ICON_WARNING,
            )
            != wx.OK
        ):
            return
        self.level_db.delete(the_key)
        self._session_key = PLACEHOLDER_KEY
        self._loading_snbt = True
        self.key_combo.SetValue(PLACEHOLDER_KEY)
        self._loading_snbt = False
        self._index_structures()
        self._apply_search_filter()
        self.data_text.SetValue("")
        self.snbt_dirty = False
        self._refresh_stats()
        wx.MessageBox("Structure deleted.", "Done", wx.OK | wx.ICON_INFORMATION)


export = dict(
    name=f"v002 Edit/Export/Import Mcstructure v{PLUGIN_VERSION}",
    operation=SetBlock,
)
