import wx
from typing import TYPE_CHECKING
from amulet.api.block import Block
from amulet_map_editor.programs.edit.api.operations import DefaultOperationUI
from amulet.utils import block_coords_to_chunk_coords

if TYPE_CHECKING:
    from amulet.api.level import BaseLevel
    from amulet_map_editor.programs.edit.api.canvas import EditCanvas


FALLING_BLOCKS = {
    "sand", "red_sand", "gravel",
    "anvil", "chipped_anvil", "damaged_anvil",
    "suspicious_sand", "suspicious_gravel",
    "white_concrete_powder", "orange_concrete_powder",
    "magenta_concrete_powder", "light_blue_concrete_powder",
    "yellow_concrete_powder", "lime_concrete_powder",
    "pink_concrete_powder", "gray_concrete_powder",
    "light_gray_concrete_powder", "cyan_concrete_powder",
    "purple_concrete_powder", "blue_concrete_powder",
    "brown_concrete_powder", "green_concrete_powder",
    "red_concrete_powder", "black_concrete_powder",
}

TREE_KEYWORDS = ("log", "stem", "wood", "leaves")

TRANSPARENT_KEYWORDS = (
    "grass", "flower", "rail", "carpet", "glass",
    "pane", "torch", "leaves", "lever", "button", "ladder",
    "vine", "scaffolding", "redstone", "wire",
    "fence", "slab", "stairs", "door", "trapdoor",
    "banner", "sign", "pressure_plate", "snow",
    "barrier", "structure_void", "light_block",
    "web", "chest"
)


class HollowShell(wx.Panel, DefaultOperationUI):

    VERSION = "2.3"

    def __init__(self, parent, canvas, world, options_path):
        wx.Panel.__init__(self, parent)
        DefaultOperationUI.__init__(self, parent, canvas, world, options_path)

        self._sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sizer)

        warning = wx.StaticText(
            self,
            label="⚠ Do NOT use on player builds.\n"
                  "May break architecture or redstone."
        )
        warning.SetForegroundColour(wx.Colour(200, 50, 50))

        self._transparent_cb = wx.CheckBox(self, label="Treat transparent blocks as air")
        self._transparent_cb.SetValue(True)

        self._liquid_cb = wx.CheckBox(self, label="Treat water and lava as air")
        self._liquid_cb.SetValue(True)

        self._protect_trees_cb = wx.CheckBox(self, label="Protect trees")
        self._protect_trees_cb.SetValue(True)

        self._falling_cb = wx.CheckBox(self, label="Support falling blocks from below")
        self._falling_cb.SetValue(False)

        self._run_button = wx.Button(self, label="Hollow Selection")
        self._run_button.Bind(wx.EVT_BUTTON, self._run_operation)

        version_label = wx.StaticText(
            self,
            label=f"Hollow Shell v{self.VERSION}"
        )
        version_label.SetForegroundColour(wx.Colour(120, 120, 120))

        self._sizer.Add(warning, 0, wx.ALL, 5)
        self._sizer.Add(self._transparent_cb, 0, wx.ALL, 5)
        self._sizer.Add(self._liquid_cb, 0, wx.ALL, 5)
        self._sizer.Add(self._protect_trees_cb, 0, wx.ALL, 5)
        self._sizer.Add(self._falling_cb, 0, wx.ALL, 5)
        self._sizer.Add(self._run_button, 0, wx.ALL, 5)
        self._sizer.Add(version_label, 0, wx.ALL, 5)

        self.Layout()

    def _is_air_like(self, block):

        if block.base_name == "air":
            return True

        if self._liquid_cb.GetValue():
            if block.base_name in ("water", "lava"):
                return True

        if self._transparent_cb.GetValue():
            for key in TRANSPARENT_KEYWORDS:
                if key in block.base_name:
                    return True

        return False

    def _is_tree(self, block):
        for key in TREE_KEYWORDS:
            if key in block.base_name:
                return True
        return False

    def _run_operation(self, _):

        dimension = self.canvas.dimension
        platform = self.world.level_wrapper.platform
        version = self.world.level_wrapper.version

        selection = self.canvas.selection.selection_group
        if not selection:
            wx.MessageBox("No selection!", "Error", wx.OK)
            return

        protect_trees = self._protect_trees_cb.GetValue()
        support_falling = self._falling_cb.GetValue()

        air = Block("minecraft", "air")

        def operation():

            to_remove = []

            for box in selection.selection_boxes:
                for x in range(box.min_x, box.max_x):
                    for y in range(box.min_y, box.max_y):
                        for z in range(box.min_z, box.max_z):

                            cx, cz = block_coords_to_chunk_coords(x, z)
                            if not self.world.has_chunk(cx, cz, dimension):
                                continue

                            block, _ = self.world.get_version_block(
                                x, y, z, dimension, (platform, version)
                            )

                            if self._is_air_like(block):
                                continue

                            if protect_trees and self._is_tree(block):
                                continue

                            neighbors = [
                                (x+1,y,z),(x-1,y,z),
                                (x,y+1,z),(x,y-1,z),
                                (x,y,z+1),(x,y,z-1),
                            ]

                            is_surface = False

                            for nx, ny, nz in neighbors:

                                cx2, cz2 = block_coords_to_chunk_coords(nx, nz)
                                if not self.world.has_chunk(cx2, cz2, dimension):
                                    is_surface = True
                                    break

                                n_block, _ = self.world.get_version_block(
                                    nx, ny, nz, dimension, (platform, version)
                                )

                                if self._is_air_like(n_block):
                                    is_surface = True
                                    break

                            if not is_surface:
                                to_remove.append((x, y, z))

            for x, y, z in to_remove:

                if support_falling:
                    cx, cz = block_coords_to_chunk_coords(x, z)
                    if self.world.has_chunk(cx, cz, dimension):
                        above_block, _ = self.world.get_version_block(
                            x, y+1, z, dimension, (platform, version)
                        )
                        if above_block.base_name in FALLING_BLOCKS:
                            continue

                self.world.set_version_block(
                    x, y, z,
                    dimension,
                    (platform, version),
                    air,
                    None,
                )

                cx, cz = block_coords_to_chunk_coords(x, z)
                chunk = self.world.get_chunk(cx, cz, dimension)
                chunk.changed = True

        self.canvas.run_operation(operation)
        wx.MessageBox("Done.", "Hollow Shell", wx.OK)


export = dict(name="Hollow Shell 2.3", operation=HollowShell)
#Created by MaxRM on 02.25.2026 using ChatGPT.