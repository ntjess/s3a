import logging
from collections import defaultdict
from pathlib import Path
from typing import Union, Dict, List, Sequence

import pyqtgraph as pg
import pandas as pd
import qdarkstyle
from pyqtgraph.Qt import QtCore, QtWidgets, QtGui
from utilitys import ParamEditor, ParamEditorPlugin, RunOpts, PrjParam, fns, widgets

from ..constants import LAYOUTS_DIR, REQD_TBL_FIELDS, ICON_DIR, PRJ_ENUMS, PRJ_CONSTS
from ..generalutils import hierarchicalUpdate
from ..logger import getAppLogger
from ..models.s3abase import S3ABase
from ..plugins.comptable import CompTablePlugin
from ..plugins.mainimage import MainImagePlugin
from ..plugins.misc import RandomToolsPlugin
from ..shared import SharedAppSettings
from ..structures import FilePath, NChanImg

__all__ = ["S3A"]

_MENU_PLUGINS = [RandomToolsPlugin]


class S3A(S3ABase):
    sigLayoutSaved = QtCore.Signal()

    __groupingName__ = "Application"

    def __initEditorParams__(self, shared: SharedAppSettings):
        super().__initEditorParams__(shared)
        self.toolsEditor = ParamEditor.buildClsToolsEditor(type(self), "Application")
        shared.colorScheme.registerFunc(
            self.updateTheme, runOpts=RunOpts.ON_CHANGED, nest=False
        )

    def __init__(
        self,
        parent=None,
        log: Union[str, Sequence[str]] = PRJ_ENUMS.LOG_TERM,
        loadLastState=True,
        **startupSettings,
    ):
        # Wait to import quick loader profiles until after self initialization so
        # customized loading functions also get called
        super().__init__(parent, **startupSettings)
        self.setWindowIcon(QtGui.QIcon(str(ICON_DIR / "s3alogo.svg")))
        logger = getAppLogger()
        if PRJ_ENUMS.LOG_GUI in log:
            logger.registerExceptions()
            logger.registerWarnings()
            logger.addHandler(
                widgets.FadeNotifyHandler(
                    PRJ_ENUMS.LOG_LEVEL_ATTENTION,
                    self,
                    maxLevel=PRJ_ENUMS.LOG_LEVEL_ATTENTION,
                )
            )
            logger.addHandler(
                widgets.StatusBarHandler(logging.INFO, self, maxLevel=logging.INFO)
            )
            # This logger isn't supposed to propagate, since everything is handled in
            # the terminal on accepted events unless 'terminal' is also specified
            if PRJ_ENUMS.LOG_TERM not in log:
                logger.propagate = False
        self.APP_TITLE = "Semi-Supervised Semantic Annotator"
        self.CUR_COMP_LBL = "Current Component ID:"
        self.setWindowTitle(self.APP_TITLE)
        self.setWindowIconText(self.APP_TITLE)

        self.currentComponentLabel = QtWidgets.QLabel(self.CUR_COMP_LBL)

        # -----
        # LAOYUT MANAGER
        # -----
        # Dummy editor for layout options since it doesn't really have editable settings
        # Maybe later this can be elevated to have more options
        self.layoutEditor = ParamEditor(self, None, LAYOUTS_DIR, "dockstate", "Layout")

        def loadLayout(layoutName: Union[str, Path]):
            layoutName = Path(layoutName)
            if not layoutName.is_absolute():
                layoutName = LAYOUTS_DIR / f"{layoutName}.dockstate"
            state = fns.attemptFileLoad(layoutName)
            self.restoreState(state["docks"])

        def saveRecentLayout(_folderName: Path):
            outFile = _folderName / "layout.dockstate"
            self.saveLayout(outFile)
            return str(outFile)

        self.layoutEditor.loadParamValues = loadLayout
        self.layoutEditor.saveParamValues = saveRecentLayout
        self.appStateEditor.addImportExportOptions(
            "layout", loadLayout, saveRecentLayout
        )

        self._buildGui()
        self._buildMenu()
        self._hookupSignals()

        # Load in startup settings
        stateDict = None if loadLastState else {}
        hierarchicalUpdate(self.appStateEditor.startupSettings, startupSettings)
        self.appStateEditor.loadParamValues(stateDict=stateDict)

    def _hookupSignals(self):
        # EDIT
        self.saveAllEditorDefaults()

    def _buildMenu(self):
        # Nothing to do for now
        pass

    def _buildGui(self):
        self.setDockOptions(self.ForceTabbedDocks)
        self.setTabPosition(QtCore.Qt.AllDockWidgetAreas, QtWidgets.QTabWidget.North)
        centralWidget = QtWidgets.QWidget()
        self.setCentralWidget(centralWidget)
        layout = QtWidgets.QVBoxLayout(centralWidget)

        self.toolbarWidgets: Dict[PrjParam, List[QtGui.QAction]] = defaultdict(list)
        layout.addWidget(self.mainImage)

        self.tableFieldToolbar.setObjectName("Table Field Plugins")
        # self.addToolBar(self.tableFieldToolbar)
        self.tableFieldToolbar.hide()
        self.generalToolbar.setObjectName("General")
        self.addToolBar(self.generalToolbar)

        _plugins = [self.classPluginMap[c] for c in [MainImagePlugin, CompTablePlugin]]
        parents = [self.mainImage, self.componentTable]
        for plugin, parent in zip(_plugins, reversed(parents)):
            plugin.toolsEditor.actionsMenuFromProcs(
                plugin.name, nest=True, parent=parent, outerMenu=parent.menu
            )

        tableDock = QtWidgets.QDockWidget("Component Table Window", self)
        tableDock.setFeatures(
            tableDock.DockWidgetMovable | tableDock.DockWidgetFloatable
        )

        tableDock.setObjectName("Component Table Dock")
        tableContents = QtWidgets.QWidget(tableDock)
        tableLayout = QtWidgets.QVBoxLayout(tableContents)
        tableLayout.addWidget(self.componentTable)
        tableDock.setWidget(tableContents)

        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, tableDock)

        # STATUS BAR
        statusBar = self.statusBar()

        self.mousePosLabel = QtWidgets.QLabel()
        self.pixelColorLabel = QtWidgets.QLabel()

        self.imageLabel = QtWidgets.QLabel(f"Image: None")

        statusBar.show()
        statusBar.addPermanentWidget(self.imageLabel)

        statusBar.addPermanentWidget(self.mousePosLabel)
        self.mainImage.mouseCoordsLbl = self.mousePosLabel

        statusBar.addPermanentWidget(self.pixelColorLabel)
        self.mainImage.pxColorLbl = self.pixelColorLabel

    def saveLayout(
        self, layoutName: Union[str, Path] = None, allowOverwriteDefault=False
    ):
        dockStates = self.saveState().data()
        if Path(layoutName).is_absolute():
            savePathPlusStem = layoutName
        else:
            savePathPlusStem = LAYOUTS_DIR / layoutName
        saveFile = savePathPlusStem.with_suffix(f".dockstate")
        fns.saveToFile(
            {"docks": dockStates}, saveFile, allowOverwriteDefault=allowOverwriteDefault
        )
        self.sigLayoutSaved.emit()

    def changeFocusedComponent(self, ids: Union[int, Sequence[int]] = None):
        ret = super().changeFocusedComponent(ids)
        self.currentComponentLabel.setText(
            f"Component ID: {self.mainImage.focusedComponent[REQD_TBL_FIELDS.ID]}"
        )
        return ret

    def resetTableFieldsGui(self):
        outFname = fns.popupFilePicker(
            None, "Select Table Config File", "All Files (*.*);; Config Files (*.yml)"
        )
        if outFname is not None:
            self.sharedAttrs.tableData.loadConfig(outFname)

    def _addPluginObject(self, plugin: ParamEditorPlugin, **kwargs):
        plugin = super()._addPluginObject(plugin, **kwargs)
        if not plugin:
            return
        dock = plugin.dock
        if dock is None:
            return
        self.sharedAttrs.quickLoader.addDock(dock)
        self.addTabbedDock(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)

        if plugin.menu is None:
            # No need to add menu and graphics options
            return plugin

        parentTb = plugin.parentMenu
        if parentTb is not None:
            plugin.addToWindow(self, parentToolbarOrMenu=parentTb)
        return plugin

    def setMainImage(
        self,
        file: FilePath = None,
        imageData: NChanImg = None,
        clearExistingComponents=True,
    ):
        gen = super().setMainImage(file, imageData, clearExistingComponents)
        ret = fns.gracefulNext(gen)
        img = self.sourceImagePath
        if img is not None:
            img = img.name
        self.imageLabel.setText(f"Image: {img}")

        yield ret
        yield fns.gracefulNext(gen)

    def setMainImageGui(self):
        fileFilter = (
            "Image Files (*.png *.tif *.jpg *.jpeg *.bmp *.jfif);;All files(*.*)"
        )
        fname = fns.popupFilePicker(None, "Select Main Image", fileFilter)
        if fname is not None:
            with pg.BusyCursor():
                self.setMainImage(fname)

    def exportAnnotationsGui(self):
        """Saves the component table to a file"""
        fileFilters = self.componentIo.ioFileFilter(**{"*": "All Files"})
        outFname = fns.popupFilePicker(
            None, "Select Save File", fileFilters, existing=False
        )
        if outFname is not None:
            super().exportCurrentAnnotation(outFname)

    def openAnnotationGui(self):
        # TODO: See note about exporting components. Delegate the filepicker activity to
        #  importer
        fileFilter = self.componentIo.ioFileFilter(which=PRJ_ENUMS.IO_IMPORT)
        fname = fns.popupFilePicker(None, "Select Load File", fileFilter)
        if fname is None:
            return
        self.openAnnotations(fname)

    def saveLayoutGui(self):
        outName = fns.dialogGetSaveFileName(self, "Layout Name")
        if outName is None or outName == "":
            return
        self.saveLayout(outName)

    # ---------------
    # BUTTON CALLBACKS
    # ---------------
    def closeEvent(self, ev: QtGui.QCloseEvent):
        # Confirm all components have been saved
        shouldExit = True
        forceClose = False
        if self.hasUnsavedChanges:
            ev.ignore()
            forceClose = False
            msg = QtWidgets.QMessageBox()
            msg.setWindowTitle("Confirm Exit")
            msg.setText(
                "Component table has unsaved changes.\nYou can choose to save and exit "
                "or discard changes "
            )
            msg.setDefaultButton(msg.Save)
            msg.setStandardButtons(msg.Discard | msg.Cancel | msg.Save)
            code = msg.exec_()
            if code == msg.Discard:
                forceClose = True
            elif code == msg.Cancel:
                shouldExit = False
        if shouldExit:
            # Clean up all editor windows, which could potentially be left open
            ev.accept()
            fns.restoreExceptionBehavior()
            if not forceClose:
                self.appStateEditor.saveParamValues()

    def forceClose(self):
        """
        Allows the app to close even if it has unsaved changes. Useful for closing
        within a script
        """
        self.hasUnsavedChanges = False
        self.close()

    def _populateLoadLayoutOptions(self):
        self.layoutEditor.addDirItemsToMenu(self.menuLayout)

    def updateTheme(self, useDarkTheme=False):
        style = ""
        if useDarkTheme:
            style = qdarkstyle.load_stylesheet()
        self.setStyleSheet(style)

    def addAndFocusComponents(
        self, components: pd.DataFrame, addType=PRJ_ENUMS.COMPONENT_ADD_AS_NEW
    ):
        gen = super().addAndFocusComponents(components, addType=addType)
        changeDict = fns.gracefulNext(gen)
        keepIds = changeDict["ids"]
        keepIds = keepIds[keepIds >= 0]
        selection = self.componentController.selectRowsById(keepIds)
        if (
            self.isVisible()
            and self.componentTable.props[PRJ_CONSTS.PROP_SHOW_TBL_ON_COMP_CREATE]
        ):
            # For some reason sometimes the actual table selection doesn't propagate in
            # time, so directly forward the selection here
            self.componentTable.setSelectedCellsAsGui(selection)
        yield changeDict
        yield fns.gracefulNext(gen)


if __name__ == "__main__":
    import sys

    app = QtWidgets.QApplication([])
    win = S3A()
    win.showMaximized()
    sys.exit(app.exec_())
