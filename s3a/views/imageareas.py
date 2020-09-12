from typing import Union, Optional, Collection

import cv2 as cv
import numpy as np
import pandas as pd
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui
from skimage.io import imread

from s3a import FR_SINGLETON
from s3a.constants import REQD_TBL_FIELDS, FR_CONSTS as FRC
from s3a.generalutils import getClippedBbox
from s3a.structures import FRParam, FRVertices, FRComplexVertices, FilePath
from s3a.structures import NChanImg
from .buttons import FRDrawOpts, FRButtonCollection
from .clickables import FRRightPanViewBox
from .regions import FRRegionCopierPlot
from ..parameditors import FRParamEditor, FRTableFieldPlugin

__all__ = ['FRMainImage', 'FRFocusedImage', 'FREditableImgBase']

from s3a.controls.drawctrl import FRRoiCollection
from ..graphicsutils import contextMenuFromEditorActions

Signal = QtCore.Signal
QCursor = QtGui.QCursor

@FR_SINGLETON.registerGroup(FRC.CLS_IMG_AREA)
class FREditableImgBase(pg.PlotWidget):
  sigMousePosChanged = Signal(object, object)
  """
  FRVertices() coords, [[[img pixel]]] np.ndarray. If the mouse pos is outside
  image bounds, the second param will be *None*.
  """

  @classmethod
  def __initEditorParams__(cls):
    cls.toolsEditor = FRParamEditor.buildClsToolsEditor(cls)

    cls.compCropMargin, cls.treatMarginAsPct = FR_SINGLETON.generalProps.registerProps(
      cls, [FRC.PROP_CROP_MARGIN_VAL, FRC.PROP_TREAT_MARGIN_AS_PCT])
    cls.showGuiBtns = FR_SINGLETON.generalProps.registerProp(
      cls, FRC.PROP_SHOW_GUI_TOOL_BTNS, asProperty=False
    )

    (cls.clearRoiAct, ) = cls.toolsEditor.registerProps(
      cls, [FRC.TOOL_CLEAR_ROI], asProperty=False, ownerObj=cls
    )

  def __init__(self, parent=None, drawShapes: Collection[FRParam]=(),
               drawActions: Collection[FRParam]=(),**kargs):
    super().__init__(parent, viewBox=FRRightPanViewBox(), **kargs)
    self.menu = contextMenuFromEditorActions(self.toolsEditor, self.toolsEditor.name, self)
    vb: pg.ViewBox = self.getViewBox()
    # Disable default menus
    self.plotItem.ctrlMenu = None
    self.sceneObj.contextMenu = None
    # Set desired menu actions
    vb.menu = self.menu

    self.clearRoiAct.sigActivated.connect(lambda: self.clearCurRoi())
    self.setAspectLocked(True)
    self.getViewBox().invertY()
    self.setMouseEnabled(True)
    # -----
    # IMAGE
    # -----
    self.imgItem = pg.ImageItem()
    self.imgItem.setZValue(-100)
    self.addItem(self.imgItem)

    # -----
    # DRAWING OPTIONS
    # -----
    self.regionCopier = FRRegionCopierPlot(self)

    self.drawAction: FRParam = FRC.DRAW_ACT_PAN
    self.shapeCollection = FRRoiCollection(drawShapes, self)
    self.shapeCollection.sigShapeFinished.connect(self.handleShapeFinished)

    # Make sure panning is allowed before creating draw widget
    if FRC.DRAW_ACT_PAN not in drawActions:
      drawActions += (FRC.DRAW_ACT_PAN,)

    def shapeAssignment(newShapeParam: FRParam):
      self.shapeCollection.curShapeParam = newShapeParam
    self.drawShapeGrp = FRButtonCollection(self, 'Shapes', drawShapes, shapeAssignment)

    def actionAssignment(newActionParam: FRParam):
      self.drawAction = newActionParam
      if self.regionCopier.active:
        self.regionCopier.erase()
    self.drawActGrp = FRButtonCollection(self, 'Actions', drawActions, actionAssignment)

    self.drawOptsWidget = FRDrawOpts(self.drawShapeGrp, self.drawActGrp, self)

    # Don't create shortcuts since this will be done by the tool editor
    self.toolsGrp = FRButtonCollection.fromToolsEditors(self.toolsEditor, self)
    self.showGuiBtns.sigValueChanged.connect(lambda _p, val: self.toolsGrp.setVisible(val))

    # Initialize draw shape/action buttons
    self.drawActGrp.callFuncByParam(self.drawAction)
    self.drawShapeGrp.callFuncByParam(self.shapeCollection.curShapeParam)

  def switchBtnMode(self, newMode: FRParam):
    # EAFP
    try:
      self.drawActGrp.callFuncByParam(newMode)
    except KeyError:
      self.drawShapeGrp.callFuncByParam(newMode)

  @property
  def image(self) -> Optional[NChanImg]:
    return self.imgItem.image

  def handleShapeFinished(self, roiVerts: FRVertices) -> Optional[np.ndarray]:
    """
    Overloaded in child classes to process new regions
    """

  def mousePressEvent(self, ev: QtGui.QMouseEvent):
    super().mousePressEvent(ev)
    if (ev.buttons() == QtCore.Qt.LeftButton
        and not self.regionCopier.active
        and self.drawAction != FRC.DRAW_ACT_PAN):
      self.shapeCollection.buildRoi(ev, self.imgItem)

  def mouseDoubleClickEvent(self, ev: QtGui.QMouseEvent):
    super().mouseDoubleClickEvent(ev)
    if ev.buttons() == QtCore.Qt.LeftButton \
        and self.drawAction != FRC.DRAW_ACT_PAN:
      self.shapeCollection.buildRoi(ev, self.imgItem)

  def mouseMoveEvent(self, ev: QtGui.QMouseEvent):
    """
    Mouse move behavior is contingent on which shape is currently selected,
    unless we are panning
    """
    super().mouseMoveEvent(ev)
    if self.drawAction != FRC.DRAW_ACT_PAN:
      self.shapeCollection.buildRoi(ev, self.imgItem)
    posRelToImage = self.imgItem.mapFromScene(ev.pos())
    pxY = int(posRelToImage.y())
    pxX = int(posRelToImage.x())
    pxColor = None
    if (self.imgItem.image is not None
        and 0 < pxX < self.imgItem.image.shape[1]
        and 0 < pxY < self.imgItem.image.shape[0]):
      pxColor = self.imgItem.image[pxY, pxX]
      if pxColor.ndim == 0:
        pxColor = np.array([pxColor])
      # pos = ev.pos()
    pos = FRVertices([pxX, pxY])
    self.sigMousePosChanged.emit(pos, pxColor)

  def mouseReleaseEvent(self, ev: QtGui.QMouseEvent):
    """
    Perform a processing method depending on what the current draw action is

    :return: Whether the mouse release completes the current ROI
    """
    super().mouseReleaseEvent(ev)
    if (self.drawAction != FRC.DRAW_ACT_PAN
        and ev.button() == QtCore.Qt.LeftButton and not self.regionCopier.active):
      self.shapeCollection.buildRoi(ev, self.imgItem)

  def clearCurRoi(self):
    self.shapeCollection.clearAllRois()

@FR_SINGLETON.registerGroup(FRC.CLS_MAIN_IMG_AREA)
class FRMainImage(FREditableImgBase):
  sigCompsCreated = Signal(object) # pd.DataFrame
  # Hooked up during __init__
  sigSelectionBoundsMade = Signal(object) # FRVertices

  @classmethod
  def __initEditorParams__(cls):
    super().__initEditorParams__()
    (cls.mergeCompsAct, cls.splitCompsAct, cls.moveCompsAct, cls.copyCompsAct,
     ) = cls.toolsEditor.registerProps(
      cls, [FRC.TOOL_MERGE_COMPS, FRC.TOOL_SPLIT_COMPS,
            FRC.TOOL_MOVE_REGIONS, FRC.TOOL_COPY_REGIONS], asProperty=False)
    (cls.minCompSize, cls.onlyGrowViewbox) = FR_SINGLETON.generalProps.registerProps(
      cls, [FRC.PROP_MIN_COMP_SZ, FRC.PROP_ONLY_GROW_MAIN_VB])
    FR_SINGLETON.addDocks(cls.toolsEditor)

  def __init__(self, parent=None, imgSrc=None, **kargs):
    allowedShapes = (FRC.DRAW_SHAPE_RECT, FRC.DRAW_SHAPE_POLY)
    allowedActions = (FRC.DRAW_ACT_SELECT,FRC.DRAW_ACT_ADD)
    super().__init__(parent, drawShapes=allowedShapes,
                     drawActions=allowedActions, **kargs)
    # plt: pg.PlotItem = self.plotItem
    # # Make sure grid lines are on top of image
    # for axDict in plt.axes.values():
    #   ax = axDict['item']
    #   ax.setZValue(500)
    # -----
    # Image Item
    # -----
    self.setImage(imgSrc)
    self.compFromLastProcResult: Optional[pd.DataFrame] = None
    self.lastProcVerts: Optional[FRVertices] = None
    copier = self.regionCopier
    def startCopy():
      copier.inCopyMode = True
      copier.sigCopyStarted.emit()
    self.copyCompsAct.sigActivated.connect(startCopy)

    def startMove():
      copier.inCopyMode = False
      copier.sigCopyStarted.emit()
    self.moveCompsAct.sigActivated.connect(startMove)

    self.switchBtnMode(FRC.DRAW_ACT_ADD)

  def handleShapeFinished(self, roiVerts: FRVertices) -> Optional[np.ndarray]:
    if self.regionCopier.active: return
    if self.drawAction in [FRC.DRAW_ACT_SELECT] and roiVerts.connected:
      # Selection
      self.sigSelectionBoundsMade.emit(roiVerts)
    elif self.drawAction == FRC.DRAW_ACT_ADD:
      # Component modification subject to processor
      # For now assume a single point indicates foreground where multiple indicate
      # background selection
      # False positive from type checker
      verts = np.clip(roiVerts.astype(int), 0, self.image.shape[:2][::-1])
      self.compFromLastProcResult = FR_SINGLETON.tableData.makeCompDf()

      if cv.contourArea(verts) < self.minCompSize:
        return

      # noinspection PyTypeChecker
      verts = FRComplexVertices([verts])
      newComps = FR_SINGLETON.tableData.makeCompDf()
      newComps[REQD_TBL_FIELDS.VERTICES] = [verts]
      self.compFromLastProcResult = newComps
      self.lastProcVerts = verts
      self.sigCompsCreated.emit(newComps)

  def mouseReleaseEvent(self, ev: QtGui.QMouseEvent):
    super().mouseReleaseEvent(ev)
    if self.image is None: return
    pos = self.imgItem.mapFromScene(ev.pos())
    xx, yy, = pos.x(), pos.y()
    if self.drawAction == FRC.DRAW_ACT_PAN and not self.regionCopier.active:
      # Simulate a click-wide boundary selection so points can be selected in pan mode
      squareCorners = FRVertices([[xx, yy], [xx, yy]], dtype=float)
      self.sigSelectionBoundsMade.emit(squareCorners)

  def mouseDoubleClickEvent(self, ev: QtGui.QMouseEvent):
    super().mouseDoubleClickEvent(ev)
    if self.regionCopier.active:
      self.regionCopier.sigCopyStopped.emit()

  def switchBtnMode(self, newMode: FRParam):
    super().switchBtnMode(newMode)

  def setImage(self, imgSrc: Union[FilePath, np.ndarray]=None):
    """
    Allows the user to change the main image either from a filepath or array data
    """
    if isinstance(imgSrc, FilePath.__args__):
      # TODO: Handle alpha channel images. For now, discard that data
      imgSrc = imread(imgSrc)
      if imgSrc.ndim == 3:
        # Alpha channels cause unexpected results for most image processors. Avoid this
        # by chopping it off until otherwise needed
        imgSrc = imgSrc[:,:,0:3]

    if imgSrc is None:
      self.imgItem.clear()
    else:
      self.imgItem.setImage(imgSrc)

  def clearCurRoi(self):
    super().clearCurRoi()
    self.regionCopier.erase()

@FR_SINGLETON.registerGroup(FRC.CLS_FOCUSED_IMG_AREA)
class FRFocusedImage(FREditableImgBase):
  sigPluginChanged = Signal()

  @classmethod
  def __initEditorParams__(cls):
    super().__initEditorParams__()
    (cls.acceptRegionAct, ) = cls.toolsEditor.registerProps(
      cls, [FRC.TOOL_ACCEPT_FOC_REGION], asProperty=False, ownerObj=cls
    )

  def __init__(self, parent=None, **kargs):
    allowableShapes = (
      FRC.DRAW_SHAPE_RECT, FRC.DRAW_SHAPE_POLY, FRC.DRAW_SHAPE_PAINT
    )
    allowableActions = (
      FRC.DRAW_ACT_ADD, FRC.DRAW_ACT_REM
    )
    super().__init__(parent, allowableShapes, allowableActions, **kargs)

    self.compSer: pd.Series = FR_SINGLETON.tableData.makeCompSer()
    self.bbox = np.zeros((2, 2), dtype='int32')

    self.currentPlugin: Optional[FRTableFieldPlugin] = None

    self.switchBtnMode(FRC.DRAW_ACT_ADD)
    self.switchBtnMode(FRC.DRAW_SHAPE_PAINT)

  def handleShapeFinished(self, roiVerts: FRVertices) -> Optional[np.ndarray]:
    if self.drawAction == FRC.DRAW_ACT_PAN:
      return
    for plugin in FR_SINGLETON.tableFieldPlugins:
      if plugin.active:
        plugin.handleShapeFinished(roiVerts)

  @FR_SINGLETON.actionStack.undoable('Modify Focused Component')
  def updateAll(self, mainImg: NChanImg=None, newComp:Optional[pd.Series]=None,
                _isAlreadyTrimmed=False):
    """
    Updates focused image and component from provided information. Useful for creating
    a 'zoomed-in' view that allows much faster processing than applying image processing
    algorithms to the entire image each iteration.
    :param mainImg: Image from the main view
    :param newComp: New component to edit using various plugins (See :class:`FRTableFieldPlugin`)
    :param _isAlreadyTrimmed: Used internally during undo. Generally shouldn't be set by the
      user
    """
    oldImg = self.image
    if oldImg is not None:
      oldImg = oldImg.copy()
    oldComp = self.compSer

    if mainImg is None:
      self.imgItem.clear()
      self.shapeCollection.clearAllRois()
      self.shapeCollection.clearAllRois()
      self.compSer = FR_SINGLETON.tableData.makeCompSer()
    else:
      newVerts: FRComplexVertices = newComp[REQD_TBL_FIELDS.VERTICES]
      # Since values INSIDE the dataframe are reset instead of modified, there is no
      # need to go through the trouble of deep copying
      self.compSer = newComp.copy(deep=False)
      # Remove all other fields so they don't overwrite main component fields on update
      keepCols = [REQD_TBL_FIELDS.INST_ID, REQD_TBL_FIELDS.VERTICES]
      self.compSer = self.compSer[keepCols]

      # Propagate all resultant changes
      if not _isAlreadyTrimmed:
        self._updateBbox(mainImg.shape, newVerts)
        bboxToUse = self.bbox
      else:
        bboxToUse = FRVertices([[0,0], mainImg.shape[:2]])
        self.bbox = bboxToUse
      newCompImg = mainImg[bboxToUse[0,1]:bboxToUse[1,1],
                   bboxToUse[0,0]:bboxToUse[1,0],
                   :]
      if newCompImg.size == 0:
        # Empty slice
        self.imgItem.clear()
      else:
        self.imgItem.setImage(newCompImg)
        QtCore.QTimer.singleShot(0, self.autoRange)
    for plugin in FR_SINGLETON.tableFieldPlugins:
      plugin.updateAll(mainImg, newComp)
    yield
    self.updateAll(oldImg, oldComp, True)

  def _updateBbox(self, mainImgShape, newVerts: FRComplexVertices):
    concatVerts = newVerts.stack()
    # Ignore NAN entries during computation
    bbox = np.vstack([concatVerts.min(0),
                      concatVerts.max(0)])
    # Account for margins
    padVal = self.compCropMargin
    if self.treatMarginAsPct:
      padVal = max((bbox[1,:] - bbox[0,:])*self.compCropMargin/2/100)
    self.bbox = getClippedBbox(mainImgShape, bbox, int(padVal))

  def changeCurrentPlugin(self, newPlugin: FRTableFieldPlugin, forceActivate=True):
    if newPlugin is self.currentPlugin:
      return
    if self.currentPlugin is not None:
      self.currentPlugin.active = False
    newEditors = [self.toolsEditor]
    if newPlugin is not None:
      newEditors.append(newPlugin.toolsEditor)
    self.menu = contextMenuFromEditorActions(newEditors, menuParent=self)
    self.getViewBox().menu = self.menu
    self.currentPlugin = newPlugin
    if forceActivate and newPlugin is not None and not newPlugin.active:
      newPlugin.active = True
    self.sigPluginChanged.emit()