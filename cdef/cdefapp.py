# -*- coding: utf-8 -*-

import sys
from collections import defaultdict
from functools import partial
from os.path import join
from pathlib import Path
from typing import Callable, Dict, Any, Union, Optional

import pandas as pd
import pyqtgraph as pg
import qdarkstyle
from pandas import DataFrame as df
from pyqtgraph import BusyCursor
from pyqtgraph.Qt import QtCore, QtWidgets, QtGui

from . import FR_SINGLETON
from .frgraphics.annotator_ui import FRAnnotatorUI
from .frgraphics.graphicsutils import (dialogGetSaveFileName, addDirItemsToMenu,
                                       attemptLoadSettings, popupFilePicker,
                                       disableAppDuringFunc, makeExceptionsShowDialogs)
from .frgraphics.graphicsutils import saveToFile
from .frgraphics.parameditors import FRParamEditor
from .generalutils import resolveAuthorName
from .projectvars.constants import FR_CONSTS
from .projectvars.constants import LAYOUTS_DIR, REQD_TBL_FIELDS
from .projectvars.enums import FR_ENUMS, _FREnums
from .structures import FRAppIOError, NChanImg
from .tablemodel import FRComponentIO
from .tablemodel import FRComponentMgr
from .tableviewproxy import FRCompDisplayFilter, FRCompSortFilter

Slot = QtCore.pyqtSlot
Signal = QtCore.pyqtSignal

# Configure pg to correctly read image dimensions
pg.setConfigOptions(imageAxisOrder='row-major')

@FR_SINGLETON.registerGroup(FR_CONSTS.CLS_ANNOTATOR)
class FRCdefApp(FRAnnotatorUI):
  """
  Top-level widget for producing component bounding boxes from an input image.
  """
  # Alerts GUI that a layout (either new or overwriting old) was saved
  sigLayoutSaved = Signal()

  @classmethod
  def __initEditorParams__(cls):
    cls.estBoundsOnStart = FR_SINGLETON.generalProps.registerProp(cls,
        FR_CONSTS.PROP_EST_BOUNDS_ON_START)
    cls.useDarkTheme = FR_SINGLETON.scheme.registerProp(cls, FR_CONSTS.SCHEME_USE_DARK_THEME)

  def __init__(self, **quickLoaderArgs):
    super().__init__()
    makeExceptionsShowDialogs(self)

    self.addEditorDocks()
    # ---------------
    # DATA ATTRIBUTES
    # ---------------
    self.hasUnsavedChanges = False

    self.statBar = QtWidgets.QStatusBar(self)
    self.setStatusBar(self.statBar)
    authorName = resolveAuthorName(quickLoaderArgs.get('Author', None))
    if authorName is None:
      sys.exit('No author name provided and no default author exists. Exiting.\n'
               'To start without error, provide an author name explicitly, e.g.\n'
               '"python -m cdef --author=<Author Name>"')
    FR_SINGLETON.tableData.annAuthor = authorName
    self.statBar.showMessage(authorName)

    # Flesh out pg components
    # ---------------
    # MAIN IMAGE
    # ---------------
    self.mainImg.sigComponentCreated.connect(self.add_focusComp)

    # ---------------
    # COMPONENT MANAGER
    # ---------------
    self.compExporter = FRComponentIO()
    self.compMgr = FRComponentMgr()
    self.compMgr.sigCompsChanged.connect(self._recordCompChange)

    # Allow filtering/sorting
    self.sortFilterProxy = FRCompSortFilter(self.compMgr, self)

    self.compTbl.setModel(self.sortFilterProxy)

    # ---------------
    # COMPONENT DISPLAY FILTER
    # ---------------
    self.compDisplay = FRCompDisplayFilter(self.compMgr, self.mainImg, self.compTbl,
                                           self)

    self.mainImg.imgItem.sigImageChanged.connect(self.clearBoundaries)
    self.compDisplay.sigCompsSelected.connect(self.updateCurComp)

    # ---------------
    # UI ELEMENT SIGNALS
    # ---------------
    # Buttons
    self.openImgAct.triggered.connect(lambda: self.openImgActionTriggered())
    self.clearRegionBtn.clicked.connect(self.clearRegionBtnClicked)
    self.resetRegionBtn.clicked.connect(self.resetRegionBtnClicked)
    self.acceptRegionBtn.clicked.connect(self.acceptRegionBtnClicked)

    FR_SINGLETON.scheme.sigParamStateUpdated.connect(self.updateTheme)

    # Menu options
    # FILE
    self.saveLayoutAct.triggered.connect(self.saveLayoutActionTriggered)
    self.sigLayoutSaved.connect(self.populateLoadLayoutOptions)

    self.exportCompListAct.triggered.connect(self.exportCompListActionTriggered)
    self.exportLabelImgAct.triggered.connect(self.exportLabelImgActionTriggered)
    self.loadCompsAct_merge.triggered.connect(lambda: self.loadCompsActionTriggered(FR_ENUMS.COMP_ADD_AS_MERGE))
    self.loadCompsAct_new.triggered.connect(lambda: self.loadCompsActionTriggered(FR_ENUMS.COMP_ADD_AS_NEW))

    # SETTINGS
    for editor in FR_SINGLETON.registerableEditors:
      self.createMenuOptForEditor(self.paramTools, editor)
    profileLoadFunc = self.importQuickLoaderProfile
    self.createMenuOptForEditor(self.menuFile, FR_SINGLETON.quickLoader, profileLoadFunc)
    if quickLoaderArgs is not None:
      profileLoadFunc(quickLoaderArgs)

    # ANALYTICS
    self.newCompAnalyticsAct.triggered.connect(self.showNewCompAnalytics)
    self.modCompAnalyticsAct.triggered.connect(self.showModCompAnalytics)

    # Load layout options
    self.saveLayout('Default', allowOverwriteDefault=True)
    FR_SINGLETON.quickLoader.show()
    self.resetMainImg()



  # -----------------------------
  # FRCdefApp CLASS FUNCTIONS
  # -----------------------------
  # -----
  # Gui stuff
  # -----
  def closeEvent(self, ev: QtGui.QCloseEvent):
    # Confirm all components have been saved
    shouldExit = False
    if self.hasUnsavedChanges:
      ev.ignore()
      if (QtWidgets.QMessageBox.question(self, 'Confirm Exit',
                                         'Component table has unsaved changes.\nAre you sure you want to exit?',
                                         QtWidgets.QMessageBox.Ok, QtWidgets.QMessageBox.Cancel)
          == QtWidgets.QMessageBox.Ok):
        shouldExit = True
    else:
      shouldExit = True
    if shouldExit:
      # Clean up all editor windows, which could potentially be left open
      ev.accept()
      FR_SINGLETON.close()

  def addEditorDocks(self):
    # Define out here to retain scope
    editor = None
    for editor in FR_SINGLETON.allEditors:
      editor.setParent(self)
      self.addDockWidget(QtCore.Qt.RightDockWidgetArea, editor)
    for nextEditor in FR_SINGLETON.allEditors[:-1]:
      self.tabifyDockWidget(editor, nextEditor)

  def createMenuOptForEditor(self, parentMenu: QtWidgets.QMenu, editor: FRParamEditor,
                             loadFunc=None):
    if loadFunc is None:
      loadFunc = partial(self.paramEditorLoadActTriggered, editor)
    name = editor.name
    newMenu = QtWidgets.QMenu(name, self)
    editAct = QtWidgets.QAction ('Edit ' + name, self)
    newMenu.addAction(editAct)
    newMenu.addSeparator()
    def showFunc(_editor=editor):
      editor.show()
      # "Show" twice forces 1) window to exist and 2) it is currently raised and focused
      # These guarantees are not met if "show" is only called once
      editor.show()
    editAct.triggered.connect(showFunc)
    populateFunc = partial(self.populateParamEditorMenuOpts, editor, newMenu, loadFunc)
    editor.sigParamStateCreated.connect(populateFunc)
    # Initialize default menus
    populateFunc()
    parentMenu.addMenu(newMenu)

  @staticmethod
  def populateParamEditorMenuOpts(objForMenu: FRParamEditor, winMenu: QtWidgets.QMenu,
                                  triggerFn: Callable):
    addDirItemsToMenu(winMenu,
                      join(objForMenu.saveDir, f'*.{objForMenu.fileType}'),
                      triggerFn)

  # -----
  # App functionality
  # -----
  def updateTheme(self, _newScheme: Dict[str, Any]):
    style = ''
    if self.useDarkTheme:
      style = qdarkstyle.load_stylesheet()
    self.setStyleSheet(style)

  def clearFocusedRegion(self):
    # Reset drawn comp vertices to nothing
    # Only perform action if image currently exists
    if self.focusedImg.imgItem.image is None:
      return
    self.focusedImg.updateRegionFromVerts(None)

  def resetFocusedRegion(self):
    # Reset drawn comp vertices to nothing
    # Only perform action if image currently exists
    if self.focusedImg.imgItem.image is None:
      return
    self.focusedImg.updateRegionFromVerts(self.focusedImg.compSer[REQD_TBL_FIELDS.VERTICES])

  def acceptFocusedRegion(self):
    self.focusedImg.saveNewVerts()
    modifiedComp = self.focusedImg.compSer
    self.compMgr.addComps(modifiedComp.to_frame().T, addtype=FR_ENUMS.COMP_ADD_AS_MERGE)
    self.compDisplay.regionPlots.focusById([modifiedComp[REQD_TBL_FIELDS.INST_ID]])

  def estimateBoundaries(self):
    with BusyCursor():
      self.mainImg.procCollection.run(prevCompMask=None, fgVerts=None, bgVerts=None)
      verts = self.mainImg.procCollection.resultAsVerts(localEstimate=False)
      components = FR_SINGLETON.tableData.makeCompDf(len(verts))
      components[REQD_TBL_FIELDS.VERTICES] = verts
      self.compMgr.addComps(components)

  def clearBoundaries(self):
    self.compMgr.rmComps()

  def resetMainImg(self, fileName: str=None, imgData: NChanImg=None,
                   clearExistingComps=True):
    """
    * If fileName is None, the main and focused images are blacked out.
    * If only fileName is provided, it is assumed to be an image. The image data
    will be populated by reading in that file.
    * If both fileName and imgData are provided, then imgData is used to populate the
    image, and fileName is assumed to be the file associated with that data.

    :param fileName: Filename either to load or that corresponds to imgData
    :param imgData: N-Channel numpy image
    :param clearExistingComps: If True, erases all existing components on image load.
      Else, they are retained.
    """
    if fileName is not None:
      fileName = str(Path(fileName).resolve())
    with BusyCursor():
      if clearExistingComps:
        self.compMgr.rmComps()
      if imgData is not None:
        self.mainImg.setImage(imgData)
      else:
        self.mainImg.setImage(fileName)
      FR_SINGLETON.tableData.annFile = fileName
      self.focusedImg.resetImage()
      self.mainImg.plotItem.vb.autoRange()
      if self.estBoundsOnStart:
        self.estimateBoundaries()

  def loadLayout(self, layoutName: str):
    layoutFilename = join(LAYOUTS_DIR, f'{layoutName}.dockstate')
    self.restoreState(attemptLoadSettings(layoutFilename))

  def saveLayout(self, layoutName: str=None, allowOverwriteDefault=False):
    dockStates = self.saveState().data()
    saveToFile(dockStates, LAYOUTS_DIR, layoutName, 'dockstate',
               allowOverwriteDefault=allowOverwriteDefault)
    self.sigLayoutSaved.emit()

  def importQuickLoaderProfile(self, profileSrc: Union[dict, str]):
    # Make sure defaults exist
    profileDict = defaultdict(type(None))
    if isinstance(profileSrc, str):
      profName = profileSrc
      profileSrc = {}
    else:
      profName = profileSrc.get('Profile', None)
    if profName is not None:
      profileParams = self.paramEditorLoadActTriggered(
        FR_SINGLETON.quickLoader, profName)['children']
      # Attrs from a param tree are hidden behind 'value', so bring each to the front
      profileDict.update({k: v['value'] for k, v in profileParams.items()})
    profileDict.update(profileSrc)

    imgFname = profileDict['Image']
    if imgFname is not None:
      self.resetMainImg(imgFname)

    annFname = profileDict['Annotations']
    if annFname is not None:
      self.loadCompList(annFname)

    layoutName = profileDict['Layout']
    if layoutName is not None:
      self.loadLayoutActionTriggered(layoutName)

    for editor in FR_SINGLETON.registerableEditors:
      curSettings = profileDict[editor.name]
      if curSettings is not None:
        self.paramEditorLoadActTriggered(editor, curSettings)

  def exportCompList(self, outFname: str):
    self.compExporter.prepareDf(self.compMgr.compDf, self.compDisplay.displayedIds)
    self.compExporter.exportCsv(outFname)
    self.hasUnsavedChanges = False

  def exportLabeledImg(self, outFname: str=None):
    self.compExporter.prepareDf(self.compMgr.compDf, self.compDisplay.displayedIds)
    return self.compExporter.exportLabeledImg(self.mainImg.image.shape, outFname)

  def loadCompList(self, inFname: str, loadType=FR_ENUMS.COMP_ADD_AS_NEW):
    pathFname = Path(inFname)
    if self.mainImg.image is None:
      raise FRAppIOError('Cannot load components when no main image is set.')
    fType = pathFname.suffix[1:]
    if fType == 'csv':
      newComps = FRComponentIO.buildFromCsv(inFname, self.mainImg.image.shape)
    elif fType == 'cdefpkl':
      # Operation may take a long time, but we don't want to start the wait cursor until
      # after dialog selection
      newComps = FRComponentIO.buildFromPkl(inFname, self.mainImg.image.shape)
    else:
      raise FRAppIOError(f'Extension {fType} is not recognized. Must be one of: csv, cdefpkl')
    self.compMgr.addComps(newComps, loadType)

  def showNewCompAnalytics(self):
    self.mainImg.procCollection.curProcessor.processor.plotStages()

  def showModCompAnalytics(self):
    self.focusedImg.procCollection.curProcessor.processor.plotStages()

  # ---------------
  # MISC CALLBACKS
  # ---------------
  @Slot(object)
  def _recordCompChange(self):
    self.hasUnsavedChanges = True

  @Slot(object)
  def add_focusComp(self, newComps: df):
    self.compMgr.addComps(newComps)
    # Make sure index matches ID before updating current component
    newComps = newComps.set_index(REQD_TBL_FIELDS.INST_ID, drop=False)
    # Set this component as active in the focused view
    self.updateCurComp(newComps)

  # ---------------
  # MENU CALLBACKS
  # ---------------
  @Slot()
  def openImgActionTriggered(self):
    fileFilter = "Image Files (*.png; *.tif; *.jpg; *.jpeg; *.bmp; *.jfif);; All files(*.*)"
    fname = popupFilePicker(self, 'Select Main Image', fileFilter)
    if fname is not None:
      self.resetMainImg(fname)

  @Slot(str)
  def loadLayoutActionTriggered(self, layoutName):
    self.loadLayout(layoutName)

  @Slot()
  def populateLoadLayoutOptions(self):
    layoutGlob = join(LAYOUTS_DIR, '*.dockstate')
    addDirItemsToMenu(self.menuLayout, layoutGlob, self.loadLayoutActionTriggered)

  @Slot()
  def saveLayoutActionTriggered(self):
    outName = dialogGetSaveFileName(self, 'Layout Name')
    if outName is None or outName == '':
      return
    self.saveLayout(outName)

  @staticmethod
  def paramEditorLoadActTriggered(objForMenu: FRParamEditor, nameToLoad: str) -> Optional[dict]:
    return objForMenu.loadSettings(nameToLoad)

  @Slot()
  def exportCompListActionTriggered(self):
    fileDlg = QtWidgets.QFileDialog()
    # TODO: Delegate this to the exporter. Make a function that makes the right file filter,
    #   and calls the right exporter function after the filename is retrieved.
    fileFilter = "CSV Files (*.csv)"
    fname, _ = fileDlg.getSaveFileName(self, 'Select Save File', '', fileFilter)
    if len(fname) > 0:
      self.exportCompList(fname)

  @Slot()
  def exportLabelImgActionTriggered(self):
    """
    # Note -- These three functions will be a single dialog with options
    # for each requested parameter. It will look like the FRTableFilterEditor dialog.
    types: List[FRCompParams] = getTypesFromUser()
    outFile = getOutFileFromUser()
    exportLegend = getExpLegendFromUser()
    """
    fileDlg = QtWidgets.QFileDialog()
    # TODO: Delegate this to the exporter. Make a function that makes the right file filter,
    #   and calls the right exporter function after the filename is retrieved.
    fileFilter = "Label Mask Image (*.png; *.tif; *.jpg; *.jpeg; *.bmp; *.jfif);; All files(*.*)"
    fname, _ = fileDlg.getSaveFileName(self, 'Select Save File', '', fileFilter)
    if len(fname) > 0:
      self.exportLabeledImg(fname)


  def loadCompsActionTriggered(self, loadType: _FREnums):
    # TODO: See note about exporting comps. Delegate the filepicker activity to importer
    fileFilter = "CSV Files (*.csv)"
    fname = popupFilePicker(self, 'Select Load File', fileFilter)
    if fname is None:
      return
    self.loadCompList(fname, loadType)

  def newCompAnalyticsActTriggered(self):
    self.showNewCompAnalytics()

  # ---------------
  # BUTTON CALLBACKS
  # ---------------
  # Push buttons
  @Slot()
  def clearRegionBtnClicked(self):
    self.clearFocusedRegion()

  @Slot()
  def resetRegionBtnClicked(self):
    self.resetFocusedRegion()

  @Slot()
  @FR_SINGLETON.shortcuts.registerMethod(FR_CONSTS.SHC_ACCEPT_REGION)
  def acceptRegionBtnClicked(self):
    self.acceptFocusedRegion()

  @disableAppDuringFunc
  @FR_SINGLETON.shortcuts.registerMethod(FR_CONSTS.SHC_ESTIMATE_BOUNDARIES)
  def estimateBoundariesBtnClicked(self):
    self.estimateBoundaries()

  @Slot()
  @FR_SINGLETON.shortcuts.registerMethod(FR_CONSTS.SHC_CLEAR_BOUNDARIES)
  def clearBoundariesClicked(self):
    self.clearBoundaries()
  # ---------------
  # CUSTOM UI ELEMENT CALLBACKS
  # ---------------
  @Slot(object)
  def updateCurComp(self, newComps: df):
    if len(newComps) == 0:
      return
    # TODO: More robust scenario if multiple comps are in the dataframe
    #   For now, just use the last in the selection. This is so that if multiple
    #   components are selected in a row, the most recently selected is always
    #   the current displayed.
    newComp: pd.Series = newComps.iloc[-1,:]
    newCompId = newComp[REQD_TBL_FIELDS.INST_ID]
    self.compDisplay.regionPlots.focusById([newCompId])
    mainImg = self.mainImg.image
    self.focusedImg.updateAll(mainImg, newComp)
    self.curCompIdLbl.setText(f'Component ID: {newCompId}')
