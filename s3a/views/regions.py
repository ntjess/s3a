from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore
from utilitys import DeferredActionStackMixin as DASM
from utilitys import EditorPropsMixin, ParamContainer, PrjParam, RunOpts, fns

from .clickables import BoundScatterPlot
from ..constants import PRJ_CONSTS, PRJ_ENUMS, REQD_TBL_FIELDS as RTF
from ..generalutils import stackedVertsPlusConnections, symbolFromVerts
from ..structures import (
    BlackWhiteImg,
    ComplexXYVertices,
    GrayImg,
    OneDArr,
    XYVertices,
)

__all__ = ["MultiRegionPlot", "VertexDefinedImg", "RegionMoverPlot"]

from .rois import ROIManipulator

from ..compio import defaultIo
from ..shared import SharedAppSettings

Signal = QtCore.Signal


def makeMultiRegionDf(
    numRows=1,
    idList: Sequence[int] = None,
    selected: Sequence[bool] = None,
    focused: Sequence[bool] = None,
    vertices: Sequence[ComplexXYVertices] = None,
    labelField: PrjParam = None,
):
    """
    Helper for creating new dataframe holding information determining color data.
    `selected` and `focused` must be boolean arrays indicating whether or not each component
    is selected or focused, respectively.
    If `labelField` is given, it is used as a value to color the components
    """
    outDict = {}
    if selected is None:
        selected = np.zeros(numRows, bool)
    outDict[PRJ_ENUMS.FIELD_SELECTED] = selected
    if focused is None:
        focused = np.zeros(numRows, bool)
    outDict[PRJ_ENUMS.FIELD_FOCUSED] = focused
    if labelField is not None:
        labels_tmp = np.tile(labelField.value, numRows)
        labels = labelField.toNumeric(labels_tmp, rescale=True)
    else:
        labels = np.zeros(numRows)
    outDict[PRJ_ENUMS.FIELD_LABEL] = labels
    if vertices is None:
        vertices = [RTF.VERTICES.value for _ in range(numRows)]
    outDict[RTF.VERTICES] = vertices
    outDf = pd.DataFrame(outDict)
    if idList is not None:
        outDf.index = idList
    return outDf


class MultiRegionPlot(EditorPropsMixin, BoundScatterPlot):
    __groupingName__ = PRJ_CONSTS.CLS_MULT_REG_PLT.name

    def __initEditorParams__(self, shared: SharedAppSettings = None):
        self.props = ParamContainer()
        # Allow working without centralized color settings if needed
        if shared is None:
            return
        # Use setattr so pycharm autocomplete doesn't forget arg hints
        proc = shared.colorScheme.registerFunc(
            self.updateColors,
            runOpts=RunOpts.ON_CHANGED,
            nest=False,
            ignoreKeys=["hideFocused"],
            container=self.props,
            labelColormap=dict(limits=fns.listAllPgColormaps() + ["None"]),
        )
        setattr(self, "updateColors", proc)

    def __init__(self, parent=None, disableMouseClick=False):
        super().__init__(size=1, pxMode=False)
        # Wrapping in atomic process means when users make changes to properties, these are maintained when calling the
        # function internally with no parameters
        self.setParent(parent)
        self.setZValue(50)
        self.regionData = makeMultiRegionDf(0)
        self._symbolCache = None
        self.cmap = np.array([])
        self.updateColors()
        self.showSelected = True
        self.showFocused = True

        # 'pointsAt' is an expensive operation if many points are in the scatterplot. Since
        # this will be called anyway when a selection box is made in the main image, disable
        # mouse click listener to avoid doing all that work for nothing.
        # self.centroidPlts.mouseClickEvent = lambda ev: None
        if disableMouseClick:
            self.mouseClickEvent = lambda ev: None
            # Also disable sigClicked. This way, users who try connecting to this signal won't get
            # code that runs but never triggers
            # self.centroidPlts.sigClicked = None
            self.sigClicked = None

    def resetRegionList(
        self,
        newRegionDf: Optional[pd.DataFrame] = None,
        labelField: PrjParam = RTF.INST_ID,
    ):
        idList = None
        if newRegionDf is not None and labelField in newRegionDf.columns:
            newRegionDf = newRegionDf.copy()
            newRegionDf[PRJ_ENUMS.FIELD_LABEL] = labelField.toNumeric(
                newRegionDf[labelField], rescale=True
            )
        numRows = len(newRegionDf)
        if newRegionDf is not None:
            idList = newRegionDf.index
        self.regionData = makeMultiRegionDf(
            numRows, idList=idList, labelField=labelField
        )
        if newRegionDf is not None:
            self.regionData.update(newRegionDf)
        self.updatePlot()

    def selectById(self, selectedIds: OneDArr):
        """
        Marks 'selectedIds' as currently selected by changing their scheme to user-specified
        selection values.
        """
        self.updateSelectedAndFocused(selectedIds=selectedIds)

    def focusById(self, focusedIds: OneDArr, **kwargs):
        """
        Colors 'focusedIds' to indicate they are present in a focused views.
        """
        self.updateSelectedAndFocused(focusedIds=focusedIds)

    def updateSelectedAndFocused(
        self,
        selectedIds: np.ndarray = None,
        focusedIds: np.ndarray = None,
        updatePlot=True,
    ):
        """
        :param selectedIds: All currently selected Ids
        :param focusedIds: All currently focused Ids
        :param updatePlot: Whether to also update the graphics plot (may be time consuming)
        """
        if len(self.regionData) == 0:
            return
        for col, idList in zip(self.regionData.columns, [selectedIds, focusedIds]):
            if idList is None:
                continue
            self.regionData[col] = False
            idList = np.intersect1d(self.regionData.index, idList)
            self.regionData.loc[idList, col] = True
        if updatePlot:
            self.updatePlot(useCache=True)

    def getShownIdxs(self):
        """Returns a boolean mask of rows in regionData which should be shown"""
        usePoints = np.ones(len(self.regionData), dtype=bool)
        if not self.showSelected:
            usePoints[self.regionData[PRJ_ENUMS.FIELD_SELECTED]] = False
        if not self.showFocused:
            usePoints[self.regionData[PRJ_ENUMS.FIELD_FOCUSED]] = False
        return usePoints

    def updatePlot(self, useCache=False):
        # -----------
        # Update data
        # -----------
        usePoints = self.getShownIdxs()

        if self.regionData.empty or not np.any(usePoints):
            self.setData(x=[], y=[], data=[])
            return

        if (
            not useCache
            or self._symbolCache is None
            or len(self._symbolCache) != len(self.regionData)
        ):
            self._symbolCache = self.createSybolLUT()

        plotRegions = np.vstack(self._symbolCache.loc[usePoints, "location"])
        self.setData(
            *plotRegions.T,
            symbol=self._symbolCache.loc[usePoints, "symbol"].to_numpy(),
            data=self.regionData.index[usePoints],
        )
        self.updateColors()

    def createSybolLUT(self, regionData: pd.DataFrame = None):
        if regionData is None:
            regionData = self.regionData
        boundLocs = []
        boundSymbs = []
        for region in regionData[RTF.VERTICES]:
            boundSymbol, boundLoc = symbolFromVerts(region)

            boundLocs.append(boundLoc)
            boundSymbs.append(boundSymbol)
        cache = {"location": boundLocs, "symbol": boundSymbs}
        return pd.DataFrame(cache)

    def toGrayImg(self, imageShape: Sequence[int] = None):
        labelDf = pd.DataFrame()
        labelDf[RTF.VERTICES] = self.regionData[RTF.VERTICES]
        # Override id column to avoid an extra parameter
        labelDf[RTF.INST_ID] = self.regionData[PRJ_ENUMS.FIELD_LABEL]
        return defaultIo.exportLblPng(labelDf, imageShape=imageShape)

    def pointsAt(self, pos):
        if not isinstance(pos, QtCore.QPointF):
            pos = QtCore.QPointF(*pos)
        return self._selectionOnHiddenIds(pos, "pointsAt")

    def boundsWithin(self, selection: XYVertices):
        return self._selectionOnHiddenIds(selection, "boundsWithin")

    def _selectionOnHiddenIds(
        self, selection: XYVertices | QtCore.QPoint, checkFunc: str
    ):
        """
        Must be overridden since MultiRegionPlot might be hiding focused IDs, in which
        case they won't show up when testing point data.
        Logic is the same for ``pointsAt`` or ``boundsWithin``
        """
        pts = getattr(super(), checkFunc)(selection)
        hiddenPts = ~self.getShownIdxs()
        if not hiddenPts.any():
            # Nothing was hidden, so parent impl. checked every point
            return pts

        needsCheck = self.regionData.loc[hiddenPts]
        # TODO: Probably a better way to do this instead of creating a new plot to check
        checkPlt = self._createPlotForHiddenRegions(needsCheck)
        hiddenAndSelected = getattr(checkPlt, checkFunc)(selection)
        if len(hiddenAndSelected):
            return np.concatenate([pts, hiddenAndSelected])
        return pts

    def _createPlotForHiddenRegions(self, hiddenDf):
        symbLut = self.createSybolLUT(hiddenDf)
        with type(self).setEditorPropertyOpts(shared=None):
            checkerPlot = BoundScatterPlot()

        plotRegions = np.vstack(symbLut["location"])
        checkerPlot.setData(
            *plotRegions.T,
            symbol=symbLut["symbol"].to_numpy(),
            data=hiddenDf.index,
        )
        return checkerPlot

    def updateColors(
        self,
        penWidth=0,
        penColor="w",
        selectedFill="#00f",
        focusedFill="#f00",
        labelColormap="viridis",
        fillAlpha=0.5,
    ):
        """
        Assigns colors from the specified colormap to each unique class
        :param penWidth: Width of the pen in pixels
        :param penColor:
          helpText: Color of the border of each non-selected boundary
          pType: color
        :param selectedFill:
          helpText: Fill color for components selected in the component table
          pType: color
        :param focusedFill:
          helpText: Fill color for the component currently in the focused image
          pType: color
        :param labelColormap:
          helpText: "Colormap to use for fill colors by component label. If `None` is selected,
            the fill will be transparent."
          pType: popuplineeditor
        :param fillAlpha:
          helpText: Transparency of fill color (0 is totally transparent, 1 is totally opaque)
          limits: [0,1]
          step: 0.1
        """
        # Account for maybe hidden spots
        regionData = self.regionData.loc[self.data["data"]]
        if len(regionData) == 0 or len(self.data) == 0:
            return

        lbls = regionData[PRJ_ENUMS.FIELD_LABEL].to_numpy()
        fills = np.empty((len(lbls), 4), dtype="float32")
        fills[:, -1] = fillAlpha

        # On the chance some specified maps have alpha, ignore it with indexing
        fills[:, :-1] = fns.getAnyPgColormap(labelColormap, forceExist=True).map(
            lbls, mode="float"
        )[:, :3]

        for clr, typ in zip(
            [selectedFill, focusedFill],
            [PRJ_ENUMS.FIELD_SELECTED, PRJ_ENUMS.FIELD_FOCUSED],
        ):
            # Ignore alpha of specified fill/focus colors since there's a separate
            # alpha controller parameter
            fills[regionData[typ].to_numpy(), :-1] = pg.Color(clr).getRgbF()[:-1]

        self.setBrush(fills * 255, update=False)
        self.setPen(pg.mkPen(color=penColor, width=penWidth))

    def drop(self, ids):
        return self.regionData.drop(index=ids)

    def dataBounds(self, ax, frac=1.0, orthoRange=None):
        allVerts = ComplexXYVertices()
        for v in self.regionData[RTF.VERTICES]:
            allVerts.extend(v)
        allVerts = allVerts.stack()
        if len(allVerts) == 0:
            return [None, None]
        bounds = np.r_[
            allVerts.min(0, keepdims=True) - 0.5, allVerts.max(0, keepdims=True) + 0.5
        ]
        return list(bounds[:, ax])


class VertexDefinedImg(DASM, EditorPropsMixin, pg.ImageItem):
    sigRegionReverted = Signal(object)  # new GrayImg

    __groupingName__ = "Focused Image Graphics"

    def __initEditorParams__(self, shared: SharedAppSettings):
        self.props = ParamContainer()
        shared.colorScheme.registerProps(
            [PRJ_CONSTS.SCHEME_REG_FILL_COLOR, PRJ_CONSTS.SCHEME_REG_VERT_COLOR],
            container=self.props,
        )
        shared.colorScheme.sigChangesApplied.connect(
            lambda: self.setImage(lut=self.getLUTFromScheme())
        )

    def __init__(self):
        super().__init__()
        self.verts = ComplexXYVertices()

    def embedMaskInImg(self, toEmbedShape: Tuple[int, int]):
        outImg = np.zeros(toEmbedShape, dtype=bool)
        selfShp = self.image.shape
        outImg[0 : selfShp[0], 0 : selfShp[1]] = self.image
        return outImg

    @DASM.undoable("Modify Focused Region")
    def updateFromVertices(self, newVerts: ComplexXYVertices, srcImg: GrayImg = None):
        oldImg = self.image
        oldVerts = self.verts

        self.verts = newVerts.copy()
        if len(newVerts) == 0:
            regionData = np.zeros((1, 1), dtype=bool)
        else:
            if srcImg is None:
                stackedVerts = newVerts.stack()
                regionData = newVerts.toMask()
                # Make vertices full brightness
                regionData[stackedVerts.rows, stackedVerts.cols] = 2
            else:
                regionData = srcImg.copy()

        self.setImage(regionData, levels=[0, 2], lut=self.getLUTFromScheme())
        yield
        self.updateFromVertices(oldVerts, oldImg)

    def updateFromMask(self, newMask: BlackWhiteImg):
        # It is expensive to color the vertices, so only find contours if specified by the user
        oldImg = self.image

        newMask = newMask.astype("uint8")
        if np.array_equal(oldImg > 0, newMask):
            # Nothing to do
            return
        verts = ComplexXYVertices.fromBinaryMask(newMask)
        stackedVerts = verts.stack()
        newMask[stackedVerts.rows, stackedVerts.cols] = 2
        self.updateFromVertices(verts, srcImg=newMask)
        return

    def getLUTFromScheme(self):
        lut = [(0, 0, 0, 0)]
        for clr in PRJ_CONSTS.SCHEME_REG_FILL_COLOR, PRJ_CONSTS.SCHEME_REG_VERT_COLOR:
            lut.append(clr.getRgb())
        return np.array(lut, dtype="uint8")


class RegionMoverPlot(QtCore.QObject):
    sigMoveStarted = QtCore.Signal()
    sigMoveStopped = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False
        self.inCopyMode = True
        self.baseData = pd.DataFrame()
        self.dataMin = XYVertices()

        self.manipRoi = ROIManipulator()
        self.manipRoi.hide()

        self._connectivity = np.ndarray([], bool)

    def transformedData(self, verts: ComplexXYVertices):
        out = ComplexXYVertices(hierarchy=verts.hierarchy)
        for sublist in verts:
            out.append(self.manipRoi.getTransformedPoints(data=sublist))
        return out

    def resetBaseData(self, baseData: pd.DataFrame):
        allData = ComplexXYVertices()
        allConnctivity = []
        for verts in baseData[
            RTF.VERTICES
        ]:  # each list element represents one component
            plotData, connectivity = stackedVertsPlusConnections(verts)
            allData.append(plotData)
            allConnctivity.append(connectivity)
            if len(connectivity) > 0:
                connectivity[-1] = False
        plotData = allData.stack()
        if len(allConnctivity):
            connectivity = np.concatenate(allConnctivity)

        self.manipRoi.setAngle(0)

        self.baseData = baseData

        if len(plotData):
            pos = self.dataMin = plotData.min(0)
            baseData = plotData
            self.manipRoi.setPos(pos)
            self.manipRoi.setBaseData(baseData, connectivity)
        else:
            self.dataMin = np.array([0, 0])
            self.manipRoi.hide()

    def erase(self):
        self.resetBaseData(pd.DataFrame(columns=[RTF.VERTICES]))
        self.active = False
        self.manipRoi.hide()
