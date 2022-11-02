from __future__ import annotations

from pathlib import Path
from typing import Union, TYPE_CHECKING

import pandas as pd

from pyqtgraph.Qt import QtWidgets
from qtextras import ParameterEditor, fns

from ..constants import PRJ_CONSTS, MENU_OPTS_DIR
from ..parameditors import algcollection
from ..processing import PipelineParameter

if TYPE_CHECKING:
    from ..views.s3agui import S3A
    from ..models.tablemodel import ComponentManager
    from ..shared import SharedAppSettings

_UNSET_NAME = object()


class ParameterEditorPlugin(ParameterEditor):
    window: S3A = None
    directoryParent: str | None = MENU_OPTS_DIR
    menuTitle: str = None

    dock: QtWidgets.QDockWidget | None = None
    menu: QtWidgets.QMenu | None = None

    def __initSharedSettings__(self, shared: SharedAppSettings = None, **kwargs):
        """
        Overload this method to add parameters to the editor. This method is called
        when the plugin is attached to the window.
        """
        pass

    def __init__(self, *, name: str = None, directory: str = None, **kwargs):
        if name is None:
            name = fns.nameFormatter(self.__class__.__name__.replace("Plugin", ""))
        if directory is None and self.directoryParent is not None:
            directory = Path(self.directoryParent) / name

        super().__init__(name=name, directory=directory)

    def attachToWindow(self, window: S3A):
        self.__initSharedSettings__(shared=window.sharedSettings)
        self.window = window
        self.menuTitle = self._resolveMenuTitle(self.name)
        self.dock, self.menu = self.createWindowDock(window, self.menuTitle)

    def _resolveMenuTitle(self, name: str = None, ensureShortcut=True):
        name = self.menuTitle or name
        if ensureShortcut and "&" not in name:
            name = f"&{name}"
        return name


class ProcessorPlugin(ParameterEditorPlugin):
    processEditor: algcollection.AlgorithmEditor = None
    """
    Most table field plugins will use some sort of processor to infer field data.
    This property holds spawned collections. See :class:`XYVerticesPlugin` for
    an example.
    """

    @property
    def currentProcessor(self) -> PipelineParameter:
        return self.processEditor.currentProcessor

    @currentProcessor.setter
    def currentProcessor(self, newProcessor: Union[str, NestedProcWrapper]):
        self.processEditor.changeActiveProcessor(newProcessor)


class TableFieldPlugin(ProcessorPlugin):
    mainImage = None
    """
    Holds a reference to the focused image and set when the s3a reference is set. 
    This is useful for most table field plugins, since mainImage will hold a reference to 
    the component series that is modified by the plugins.
    """
    componentManager: ComponentManager = None
    """
    Holds a reference to the focused image and set when the s3a reference is set.
    Offers a convenient way to access component data.
    """

    _active = False

    _makeMenuShortcuts = False

    # @property
    # def parentMenu(self):
    #   return self.window.tableFieldToolbar

    def attachToWindow(self, window: S3A):
        super().attachToWindow(window)
        self.mainImage = window.mainImage
        self.componentManager = window.componentManager
        window.sigRegionAccepted.connect(self.acceptChanges)
        self.componentManager.sigUpdatedFocusedComponent.connect(
            self.updateFocusedComponent
        )
        self.active = True
        self.registerFunction(
            self.processorAnalytics, runActionTemplate=PRJ_CONSTS.TOOL_PROC_ANALYTICS
        )

    def processorAnalytics(self):
        proc = self.currentProcessor
        if hasattr(proc, "stageSummaryGui"):
            proc.stageSummaryGui()
        else:
            raise TypeError(
                f"Processor type {type(proc)} does not implement summary analytics."
            )

    def updateFocusedComponent(self, component: pd.Series = None):
        """
        This function is called when a new component is created or the focused image is
        updated from the main view. See :meth:`ComponentManager.updateFocusedComponent` for
        parameters.
        """
        pass

    def acceptChanges(self):
        """
        This must be overloaded by each plugin so the set component data is properly
        stored in the focused component. Essentially, any changes made by this plugin
        are saved after a call to this method.
        """
        raise NotImplementedError

    @property
    def active(self):
        """Whether this plugin is currently in use by the focused image."""
        return self._active

    @active.setter
    def active(self, newActive: bool):
        if newActive == self._active:
            return
        if newActive:
            self._onActivate()
        else:
            self._onDeactivate()
        self._active = newActive

    def _onActivate(self):
        """Overloaded by plugin classes to set up the plugin for use"""

    def _onDeactivate(self):
        """
        Overloaded by plugin classes to tear down when the plugin is no longer in
        use
        """
