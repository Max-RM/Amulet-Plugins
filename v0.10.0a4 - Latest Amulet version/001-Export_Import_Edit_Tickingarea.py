from typing import TYPE_CHECKING, Tuple 
 
import amulet.level 
from amulet.api.wrapper import Interface, EntityIDType, EntityCoordType 
 
from amulet_nbt import * 
nbt = from_snbt("{}") 
nbtf = NamedTag(nbt) 
 
import numpy 
import urllib.request 
import wx 
import wx.dataview 
import ast 
import os 
import string 
import os.path 
from os import path 
from ctypes import windll 
from distutils.version import LooseVersion, StrictVersion 
from amulet.api.data_types import Dimension 
from amulet.api.selection import SelectionGroup 
from amulet_nbt import * 
from amulet.api.block_entity import BlockEntity 
from amulet_map_editor.api.wx.ui.simple import SimpleDialog 
from amulet_map_editor.api.wx.ui.block_select import BlockDefine 
from amulet_map_editor.api.wx.ui.block_select import BlockSelect 
from amulet_map_editor.programs.edit.api.operations import DefaultOperationUI 
from amulet_map_editor.api.wx.ui.base_select import BaseSelect 
from amulet_map_editor.api import image 
from amulet.utils import block_coords_to_chunk_coords 
from amulet.api.block import Block 
import PyMCTranslate 
from amulet_map_editor.api.wx.ui.base_select import BaseSelect 
from amulet.libs.leveldb import LevelDB 
import amulet_nbt 
from amulet.level.formats import mcstructure 
import datetime 
from pathlib import Path 
#from amulet.level.formats.leveldb_world import  format 
 
if TYPE_CHECKING: 
 
 
    from amulet.api.level import BaseLevel 
    from amulet_map_editor.programs.edit.api.canvas import EditCanvas

from amulet_nbt import *
from amulet_map_editor.programs.edit.api.operations import DefaultOperationUI

from amulet.utils import block_coords_to_chunk_coords

operation_modes = {
        #"Java": "java",
        "Bedrock ONLY for now": "bedrock",
    }
class SetBlock(wx.Panel, DefaultOperationUI):


    def __init__(
        self,
        parent: wx.Window,
        canvas: "EditCanvas",
        world: "BaseLevel",
        options_path: str,

    ):

        platform = world.level_wrapper.platform
        world_version = world.level_wrapper.version

        plat = (platform, world_version)
        hbox = wx.wxEVT_VLBOX


        wx.Panel.__init__(self, parent)
        DefaultOperationUI.__init__(self, parent, canvas, world, options_path)
        self.Freeze()

        self._sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        side_sizer = wx.BoxSizer(wx.HORIZONTAL)
        bottom_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self._sizer.Add(side_sizer, 1, wx.TOP | wx.LEFT, 0)
        self._sizer.Add(top_sizer, 0, wx.TOP| wx.LEFT, 290)
        self._sizer.Add(bottom_sizer, 1, wx.BOTTOM | wx.LEFT,0)



        # choicebox for operation mode selection.

        self._mode = wx.Choice(self, choices=list(operation_modes.keys()))
        self._mode.SetSelection(0)
        self._structlist = wx.Choice(self, choices=self._run_get_slist())
        self._structlist.SetSelection(0)
        self._structlist.Bind(wx.EVT_CHOICE,self.onFocus)
        side_sizer.Add(self._mode, 0,  wx.TOP | wx.LEFT , 5)
        side_sizer.Add(self._structlist, 0,  wx.TOP | wx.LEFT, 9)


        self._run_getSData_button = wx.Button(self, label="Get Tickingarea DATA")
        self._run_getSData_button.Bind(wx.EVT_BUTTON, self._run_get_sdata)
        side_sizer.Add(self._run_getSData_button, 25, wx.BOTTOM | wx.LEFT, 20)
        self._run_text = wx.TextCtrl(
            self, style=wx.TE_LEFT  |  wx.TE_BESTWRAP, size=(290,20)
        )
        bottom_sizer.Add(self._run_text,0 , wx.LEFT, 0)
        self._run_text.SetValue("Tickingarea in this world")
        self._run_setSData_button = wx.Button(self, label="Save Tickingarea  \n Data to world ")
        self._run_setSData_button.Bind(wx.EVT_BUTTON, self._run_set_sdata)
        bottom_sizer.Add(self._run_setSData_button, 0,   wx.TOP | wx.LEFT, 0)
        self._run_setSData_button.Fit()


        self._run_setEData_button = wx.Button(self, label="Export Tickingarea NBT")
        self._run_setEData_button.Bind(wx.EVT_BUTTON, self._run_export)
        bottom_sizer.Add(self._run_setEData_button, 0, wx.LEFT, 0)

        self._run_setImData_button = wx.Button(self, label="Import Tickingarea NBT")
        self._run_setImData_button.Bind(wx.EVT_BUTTON, self._run_import)
        bottom_sizer.Add(self._run_setImData_button, 0, wx.LEFT, 0)


        self._run_Del_button = wx.Button(self, label="DELETE Tickingarea \n from World")
        self._run_Del_button.Bind(wx.EVT_BUTTON, self._run_Del)
        bottom_sizer.Add(self._run_Del_button, 0, wx.LEFT, 0)



        self._mode_description = wx.TextCtrl(
            self, style=wx.TE_MULTILINE | wx.TE_BESTWRAP
        )
        self._sizer.Add(self._mode_description, 25, wx.EXPAND | wx.TOP | wx.RIGHT, 0)

        self._mode_description.Fit()

        self.Layout()
        self.Thaw()


    @property
    def level_db(self):
        level_wrapper = self.world.level_wrapper
        if hasattr(level_wrapper, "level_db"):
            return level_wrapper.level_db
        else:
            return level_wrapper._level_manager._db


    def onFocus(self,evt):
        setdata = self._structlist.GetString(self._structlist.GetSelection())
        self._run_text.SetValue(setdata)



    def _run_get_data(self, _):

        player = self.leveldb.get(b'~local_player')
        data = amulet_nbt.load(player, little_endian=True).to_snbt(1)
        self._mode_description.SetValue(data)

    def _run_set_data(self, _):  #b'structuretemplate_mystructure:test'

        data = self._mode_description.GetValue()
        self.world.level_wrapper._level_manager._db.put(b'~local_player',
            from_snbt(data).save_to(compressed=False,little_endian=True))

    def _run_get_sdata(self, _):

        setdata = self._run_text.GetValue()
        enS = setdata.encode("utf-8")
        player = self.world.level_wrapper._level_manager._db.get(enS)
        self._mode_description.SetValue(amulet_nbt.load(player, little_endian=True).to_snbt(1))


    def _run_set_sdata(self, _):
        if 'tickingarea_' not in self._run_text.GetValue():
            fixKey = "tickingarea_" + self._run_text.GetValue()
            theKey = fixKey.encode("utf-8")
        else:
            theKey = self._run_text.GetValue().encode("utf-8")

        self.level_db.put(theKey, from_snbt(self._mode_description.GetValue())
            .save_to(compressed=False, little_endian=True))
        self._structlist.Clear()
        self._structlist.Append(self._run_get_slist())
        self._structlist.SetSelection(0)

    def _run_export(self, _):  # b'structuretemplate_mystructure:test'


        nbt = from_snbt(self._mode_description.GetValue()).save_to(compressed=False, little_endian=True)

        with wx.FileDialog(self, "Open NBT file", wildcard="NBT files (*.NBT)|*.NBT",
                           style=wx.FD_SAVE) as fileDialog:

            if fileDialog.ShowModal() == wx.ID_CANCEL:
                return
            else:
                pathname = fileDialog.GetPath()
                print(pathname)

        f = open(pathname, "wb")
        f.write(nbt)


    def _run_import(self, _):
        data2 = []
        with wx.FileDialog(self, "Open NBT file", wildcard="NBT files (*.NBT)|*.NBT",
                           style=wx.FD_OPEN) as fileDialog:

            if fileDialog.ShowModal() == wx.ID_CANCEL:
                return
            else:
                pathname = fileDialog.GetPath()
                print(pathname)

        with open(pathname, "rb") as f:
            data = f.read()


        nbt = amulet_nbt.load(data, little_endian=True)

        self._mode_description.SetValue(nbt.to_snbt(1))



    def _run_Del(self, _):

        theKey = self._run_text.GetValue().encode("utf-8")

        wxx = wx.MessageBox("You are going to deleted \n " + str(theKey),
                      "This can't be undone Are you Sure?", wx.OK | wx.CANCEL | wx.ICON_INFORMATION)
        if wxx == int(16):
            return
        self.world.level_wrapper._level_manager._db.delete(theKey)
        wxx = wx.MessageBox(""+ str(theKey) +" DELETED",
                            "THis "+ str(theKey) +"has been deleted \n ", wx.OK | wx.ICON_INFORMATION)
        self._structlist.Clear()
        self._structlist.Append(self._run_get_slist())
        self._structlist.SetSelection(0)

    def _run_get_slist(self):
        world = self.world.level_wrapper._level_manager._db.keys()
        currentStructure = []
        currentStructure.append("Tickingarea In this World")
        for w in world:
            if b'\xff' not in w:
                if b'\x00' not in w:
                    if b'tickingarea_' in w:
                        currentStructure.append(w)
        return currentStructure
    pass


#simple export options.
export = dict(name="v001 Edit/Export/Import Tickingarea Updated", operation=SetBlock)
#Big thanks to PremiereHell for the help in fixing plugins.