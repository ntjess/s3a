from __future__ import annotations

import copy
import typing as t
import warnings
from collections import deque, namedtuple
from functools import lru_cache

import numpy as np
import pandas as pd
import pyqtgraph as pg
from pyqtgraph.Qt import QtWidgets
from utilitys import (
    DeferredActionStackMixin as DASM,
    fns,
    ParamContainer,
    PrjParam,
    ProcessIO,
    RunOpts,
)

from .base import TableFieldPlugin
from ..constants import PRJ_CONSTS as CNST, REQD_TBL_FIELDS as RTF
from ..generalutils import getCroppedImg, showMaskDiff, tryCvResize, incrStageNames
from ..graphicsutils import RegionHistoryViewer
from ..processing.algorithms import imageproc
from ..processing.processing import (
    AbortableThreadContainer,
    ThreadedFuncWrapper,
)
from ..shared import SharedAppSettings
from ..structures import BlackWhiteImg, XYVertices, ComplexXYVertices
from ..views.regions import MultiRegionPlot, makeMultiRegionDf


class _REG_ACCEPTED:
    pass


buffEntry = namedtuple("buffentry", "id_ vertices")


class VerticesPlugin(DASM, TableFieldPlugin):
    name = "Vertices"

    def __initEditorParams__(self, shared: SharedAppSettings):
        super().__initEditorParams__()
        self.procEditor = shared.imgProcClctn.createProcessorEditor(
            type(self), self.name + " Processor"
        )

        self.dock.addEditors([self.procEditor])

        self.props = ParamContainer()
        shared.generalProps.registerProp(CNST.PROP_REG_APPROX_EPS, container=self.props)

    def __init__(self):
        super().__init__()
        self.queueActions = True
        self.region = MultiRegionPlot(disableMouseClick=True)
        self.region.hide()
        self.firstRun = True
        self.playbackWindow = RegionHistoryViewer()
        self.regionBuffer = deque(maxlen=CNST.PROP_UNDO_BUF_SZ.value)
        self.stageInfoImage = pg.ImageItem()
        self.stageInfoImage.hide()
        self._displayedStage = ""
        self.statusBtn: QtWidgets.QPushButton | None = None
        self.taskMgr = AbortableThreadContainer()
        self.taskMgr.sigThreadsUpdated.connect(self.updateTaskLbl)

        self.oldProcCache = None
        """Holds the last result from a region run so undoables reset the proc cache"""

        _, self._overlayParam = self.procEditor.registerFunc(
            self.overlayStageInfo,
            parentParam=self.procEditor._metaParamGrp,
            returnParam=True,
            runOpts=RunOpts.ON_CHANGED,
        )

    def attachWinRef(self, win):
        win.mainImg.addItem(self.region)
        win.mainImg.addItem(self.stageInfoImage)

        def resetRegBuff(_, newSize):
            newBuff = deque(maxlen=newSize)
            newBuff.extend(self.regionBuffer)
            self.regionBuffer = newBuff

        mainBufSize = win.props.params["maxLength"]
        mainBufSize.sigValueChanged.connect(resetRegBuff)

        funcLst = [
            self.resetFocusedRegion,
            self.fillRegionMask,
            self.clearFocusedRegion,
            self.clearProcessorHistory,
            self.invertRegion,
        ]
        paramLst = [
            CNST.TOOL_RESET_FOC_REGION,
            CNST.TOOL_FILL_FOC_REGION,
            CNST.TOOL_CLEAR_FOC_REGION,
            CNST.TOOL_CLEAR_HISTORY,
            CNST.TOOL_INVERT_FOC_REGION,
        ]
        for func, param in zip(funcLst, paramLst):
            self.registerFunc(func, btnOpts=param)

        def onChange():
            self.firstRun = True
            self.clearFocusedRegion()
            self.stageInfoImage.hide()

        win.mainImg.imgItem.sigImageChanged.connect(onChange)

        win.mainImg.registerDrawAction(
            [CNST.DRAW_ACT_ADD, CNST.DRAW_ACT_REM], self._runFromDrawAct
        )
        win.mainImg.addTools(self.toolsEditor)
        self.vb: pg.ViewBox = win.mainImg.getViewBox()

        self.statusBtn = QtWidgets.QPushButton("No pending actions")
        self.statusBtn.setToolTip("Click to abort all active/pending actions")
        self.statusBtn.clicked.connect(self.endQueuedActionsGui)
        win.statBar.addPermanentWidget(self.statusBtn)

        super().attachWinRef(win)

    def fillRegionMask(self):
        """Completely fill the focused region mask"""
        if self.mainImg.compSer is None:
            return
        filledImg = np.ones(self.mainImg.image.shape[:2], dtype="uint16")
        self.updateRegionFromMask(filledImg)

    @classmethod
    def clearProcessorHistory(cls):
        """
        Each time an update is made in the processor, it is saved so algorithmscan take
        past edits into account when performing their operations. Clearing that history
        will erase algorithm knowledge of past edits.
        """
        imageproc.procCache["mask"] = np.zeros_like(imageproc.procCache["mask"])

    def updateFocusedComp(self, newComp: pd.Series = None):
        if self.mainImg.compSer[RTF.INST_ID] == -1:
            self.updateRegionFromDf(None)
            return
        oldId = self.mainImg.compSer[RTF.INST_ID]
        self.updateRegionFromDf(self.mainImg.compSerAsFrame)
        if newComp is None or oldId != newComp[RTF.INST_ID]:
            self.firstRun = True

    def _runFromDrawAct(self, verts: XYVertices, param: PrjParam):
        # noinspection PyTypeChecker
        verts: XYVertices = verts.astype(int)
        activeEdits = len(self.region.regionData["Vertices"]) > 0
        if not activeEdits and self.win.compDisplay.selectionIntersectsRegion(verts):
            # Warning already handled by main image
            return

        if param == CNST.DRAW_ACT_ADD:
            vertsKey = "fgVerts"
        else:
            vertsKey = "bgVerts"
        kwargs = {vertsKey: verts}
        if self.queueActions:
            thread = self.taskMgr.addThread(self.run, **kwargs, name="Vertices Update")
            thread.sigResultReady.connect(self._onThreadFinished)
            thread.sigFailed.connect(self._onThreadFinished)
        else:
            # Run immediately
            result = self.run(**kwargs)
            self.updateGuiFromProcessor(result)

    def updateTaskLbl(self):
        if not self.statusBtn:
            return
        active = sum([th.isRunning() for th in self.taskMgr.threads])
        pending = len(self.taskMgr.threads) - active
        if active or pending:
            self.statusBtn.setText(f"{active} active, {pending} pending action(s)")
        else:
            self.statusBtn.setText("No pending actions")

    def endQueuedActions(self, endRunning=False):
        # Reversing kills unstarted tasks first
        self.taskMgr.endThreads(reversed(self.taskMgr.threads), endRunning=endRunning)

    def endQueuedActionsGui(self):
        statuses = [t.isRunning() for t in self.taskMgr.threads]
        if len(statuses) and all(statuses):
            # Only running threads are left, ensure the user really wants to violently kill them
            confirm = QtWidgets.QMessageBox.question(
                self.win,
                "Kill Running Actions?",
                "Killing in-progress actions may cause memory leaks or unintended side effects. Are you sure you want to"
                " continue?",
            )
            if confirm == QtWidgets.QMessageBox.StandardButton.Yes:
                self.endQueuedActions(endRunning=True)
        else:
            # By default, only end not-yet-started actions
            self.endQueuedActions()

    def _onThreadFinished(self, thread: ThreadedFuncWrapper, ex=None):
        if not ex:
            self.updateGuiFromProcessor(thread.result)
        else:
            warnings.warn(str(ex), UserWarning)

    def updateGuiFromProcessor(self, procResult: dict | np.ndarray):
        img = self.mainImg.image
        if img is None:
            compGrayscale = None
        else:
            compGrayscale = self.region.toGrayImg(img.shape[:2])

        newGrayscale = procResult
        if isinstance(newGrayscale, dict):
            newGrayscale = newGrayscale["image"]
        elif newGrayscale is None:
            # No change
            return
        newGrayscale = newGrayscale.astype("uint8")

        matchNames = incrStageNames(self.curProcessor.stagesFlattened)
        type(self).displayableInfos.fget.cache_clear()
        if self._displayedStage in matchNames:
            self.overlayStageInfo(self._displayedStage, self.stageInfoImage.opacity())
        else:
            self.stageInfoImage.hide()
        # Can't set limits to actual infos since equality comparison fails in pyqtgraph setLimits
        limits = [""] + list(self.displayableInfos)
        self._overlayParam.child("info").setLimits(limits)

        self.firstRun = False
        if not np.array_equal(newGrayscale, compGrayscale):
            self.updateRegionFromMask(newGrayscale)

    def run(
        self, fgVerts: XYVertices = None, bgVerts: XYVertices = None, updateGui=False
    ):
        vertsDict = {}
        if fgVerts is not None:
            vertsDict["fgVerts"] = fgVerts
        if bgVerts is not None:
            vertsDict["bgVerts"] = bgVerts
        img = self.mainImg.image
        if img is None:
            compMask = None
        else:
            compGrayscale = self.region.toGrayImg(img.shape[:2])
            compMask = compGrayscale > 0
        # TODO: When multiple classes can be represented within focused image, this is where
        #  change will have to occur
        viewbox = self.mainImg.viewboxCoords()
        self.oldProcCache = imageproc.procCache.copy()
        result = self.curProcessor.run(
            image=img,
            prevCompMask=compMask,
            **vertsDict,
            firstRun=self.firstRun,
            viewbox=XYVertices(viewbox),
            prevCompVerts=ComplexXYVertices(
                [r.stack() for r in self.region.regionData[RTF.VERTICES]]
            ),
            # Warnings render dialogs on the GUI thread but not otherwise
            errorsToWarnings=not self.queueActions,
        )
        if updateGui:
            self.updateGuiFromProcessor(result)
        return result

    def updateRegionFromDf(
        self, newData: pd.DataFrame = None, offset: XYVertices = None
    ):
        """
        Updates the current focused region using the new provided vertices
        :param newData: Dataframe to use.If *None*, the image will be totally reset and the component
          will be removed. Otherwise, the provided value will be used. For column information,
          see `makeMultiRegionDf`
        :param offset: Offset of newVerts relative to main image coordinates
        """
        fImg = self.mainImg
        # Since some calls to this function make an undo entry and others don't, be sure to save state for the undoable ones
        # revertId is only used when changedComp is true and an undo is valid
        newId = fImg.compSer[RTF.INST_ID]
        if newData is None:
            newData = makeMultiRegionDf(0)

        self._maybeChangeFocusedComp(newData.index)

        if fImg.image is None:
            self.region.clear()
            self.regionBuffer.append(buffEntry(newId, ComplexXYVertices()))
            return

        oldData = self.region.regionData

        if offset is None:
            offset = XYVertices([[0, 0]])
        # 0-center new vertices relative to FocusedImage image
        centeredData = newData
        if np.any(offset != 0):
            # Make a deep copy of verts (since they change) to preserve redos
            centeredData = centeredData.copy()
            centeredData[RTF.VERTICES] = self.applyOffset(
                [copy.deepcopy(v) for v in newData[RTF.VERTICES]], offset
            )
        if oldData.equals(centeredData):
            return

        lblCol = self.win.compDisplay.labelCol
        self.region.resetRegionList(newRegionDf=centeredData, labelField=lblCol)
        self.region.focusById(centeredData.index.values)

        buffVerts = ComplexXYVertices()
        for inner in centeredData[RTF.VERTICES]:
            buffVerts.extend(inner)
        self.regionBuffer.append(buffEntry(newId, buffVerts))

    def runOnComponent(self, component: pd.Series):
        def makeReturnValue():
            return ProcessIO(components=fns.serAsFrame(component))

        component = component.copy()
        img = self.mainImg.image
        if img is None or component[RTF.VERTICES].isEmpty():
            # Preserve empty values since they signify deletion to an outer scope
            return makeReturnValue()
        verts = component[RTF.VERTICES]
        compGrayscale = verts.toMask(img.shape[:2])
        compMask = compGrayscale > 0
        # TODO: When multiple classes can be represented within focused image, this is where
        #  change will have to occur
        stacked = verts.stack()
        offset = stacked.min(axis=0)
        if len(stacked) > 1:
            span = np.ptp(stacked, axis=0).flatten()
        else:
            span = np.array([0, 0], dtype=int)
        viewbox = span * np.array([[0, 0], [0, 1], [1, 1], [1, 0]]) + offset
        oldProcCache = imageproc.procCache.copy()
        # Broad range of things that can go wrong
        # noinspection PyBroadException
        try:
            result = self.curProcessor.run(
                image=img,
                prevCompMask=compMask,
                firstRun=True,
                viewbox=XYVertices(viewbox),
                prevCompVerts=verts,
                # Warnings render dialogs on the GUI thread but not otherwise
            )
        except Exception:
            # If anything goes wrong, safe default is to preserve initial vertices
            return makeReturnValue()
        finally:
            imageproc.procCache = oldProcCache
        newGrayscale = result
        if isinstance(newGrayscale, dict):
            newGrayscale = newGrayscale["image"]
        component[RTF.VERTICES] = ComplexXYVertices.fromBinaryMask(newGrayscale)

        return makeReturnValue()

    @staticmethod
    def applyOffset(complexVerts: t.Sequence[ComplexXYVertices], offset: XYVertices):
        centeredVerts = []
        for complexVerts in complexVerts:
            newVertList = ComplexXYVertices()
            for vertList in complexVerts:
                newVertList.append(vertList + offset)
            centeredVerts.append(newVertList)
        return centeredVerts

    def _maybeChangeFocusedComp(self, newIds: t.Sequence[int]):
        regionId = newIds[0] if len(newIds) else -1
        focusedId = self.mainImg.compSer[RTF.INST_ID]
        updated = regionId != focusedId
        if updated:
            if regionId in self.win.compMgr.compDf.index:
                self.win.changeFocusedComp([regionId])
            else:
                self.win.changeFocusedComp()
        return updated

    def updateRegionFromMask(self, mask: BlackWhiteImg, offset=None, compId=None):
        if offset is None:
            offset = XYVertices([0, 0])
        data = self.region.regionData.copy()
        if compId is None:
            compId = data.index[0] if len(data) else -1
        newVerts = ComplexXYVertices.fromBinaryMask(mask).simplify(
            self.props[CNST.PROP_REG_APPROX_EPS]
        )
        df = makeMultiRegionDf(vertices=[newVerts], idList=[compId])
        self.updateRegionUndoable(df, offset=offset, oldProcCache=self.oldProcCache)

    def invertRegion(self):
        """
        Swaps background and foreground in the area enclosed by the region mask
        """
        verts = ComplexXYVertices(
            [
                verts
                for cplxVerts in self.region.regionData[RTF.VERTICES]
                for verts in cplxVerts
            ]
        )
        if not len(verts):
            # Doesn't make sense to invert an empty region
            return
        offset = np.min(verts.stack(), axis=0)
        invertedMask = verts.removeOffset(offset).toMask() == 0
        self.updateRegionFromMask(invertedMask, offset)

    @DASM.undoable("Modify Focused Component")
    def updateRegionUndoable(
        self, newData: pd.DataFrame = None, offset: XYVertices = None, oldProcCache=None
    ):
        # Preserve cache state in argument list so it can be restored on undo. Otherwise, a separate undo buffer must
        # be maintained just to keep the cache up to date
        oldData = self.region.regionData.copy()
        self.updateRegionFromDf(newData, offset=offset)
        yield
        if oldProcCache is not None:
            imageproc.procCache = oldProcCache
        self.updateRegionFromDf(oldData)

    def acceptChanges(self, overrideVerts: ComplexXYVertices = None):
        # Add in offset from main image to VertexRegion vertices
        newVerts = overrideVerts or self.collapseRegionVerts()
        ser = self.mainImg.compSer
        ser[RTF.VERTICES] = newVerts
        self.updateFocusedComp()

    def collapseRegionVerts(self, simplify=True):
        """
        Region can consist of multiple separate complex vertices. However, the focused series
        can only contain one list of complex vertices. This function collapses all data in self.region
        into one list of complex vertices.

        :param simplify: Overlapping regions can be simplified by converting to and back from
          an image. This can be computationally intensive at times, in which case `simplify` can
          be set to *False*
        """
        try:
            hierarchy = np.row_stack(
                [v.hierarchy for v in self.region.regionData[RTF.VERTICES]]
            )
        except ValueError:
            # Numpy error when all verts are empty or no verts present
            hierarchy = np.empty((0, 4), dtype=int)
        outVerts = ComplexXYVertices(
            [
                verts
                for cplxVerts in self.region.regionData[RTF.VERTICES]
                for verts in cplxVerts
            ],
            hierarchy=hierarchy,
        )
        if simplify:
            return outVerts.simplify(epsilon=self.props[CNST.PROP_REG_APPROX_EPS])
        return outVerts

    def clearFocusedRegion(self):
        # Reset drawn comp vertices to nothing
        # Only perform action if image currently exists
        if self.mainImg.compSer is None:
            return
        self.updateRegionFromMask(np.zeros((1, 1), bool))

    def resetFocusedRegion(self):
        """Reset the focused image by restoring the region mask to the last saved state"""
        if self.mainImg.compSer is None:
            return
        self.updateRegionUndoable(self.mainImg.compSerAsFrame)

    def _onActivate(self):
        self.region.show()
        self.win.compDisplay.regionPlot.showFocused = False

    def _onDeactivate(self):
        self.region.hide()
        self.win.compDisplay.regionPlot.showFocused = True

    def getRegionHistory(self):
        outImgs = []
        if not self.regionBuffer:
            return None, []
        firstId = self.regionBuffer[-1].id_
        bufferRegions = [
            buf.vertices for buf in self.regionBuffer if buf.id_ == firstId
        ]

        if not bufferRegions:
            return None, []

        # First find offset and img size so we don't
        # have to keep copying a full image sized output every time
        allVerts = np.vstack([v.stack() for v in bufferRegions])
        initialImg, slices = getCroppedImg(self.mainImg.image, allVerts)
        imShape = initialImg.shape[:2]
        offset = slices[0]
        img = np.zeros(imShape, bool)
        outImgs.append(img)
        for singleRegionVerts in bufferRegions:
            # Copy to avoid screwing up undo buffer!
            copied = singleRegionVerts.removeOffset(offset)
            img = copied.toMask(imShape, warnIfTooSmall=False) > 0
            outImgs.append(img)
        return initialImg, outImgs

    def overlayStageInfo(self, info: t.Union[str, dict] = "", alpha=1.0):
        """
        :param info:
          pType: list
          helpText: "If a name is given, this stage's info result will be shown on top of the image. Note that
            if multiple stages exist with the same name and a string is passed, include the 1-based numeric index in the
            name, i.e. if two 'Open' stages exist, to select the second stage pass 'Open#2'.
          "
          limits: ['']
        :param alpha:
          helpText: Opacity of this overlay
          limits: [0, 1]
          step: 0.1
        """
        if not isinstance(info, dict):
            self._displayedStage = info
            info = self.displayableInfos.get(info, None)
        else:
            self._displayedStage = info["name"]
        if not info:
            # Reset to none
            self.stageInfoImage.hide()
            return
        useImg = info["image"]
        span = info["span"]
        pos = info["pos"]
        if useImg.shape[:2] != span:
            useImg = tryCvResize(useImg, span[::-1], asRatio=False)
        self.stageInfoImage.setPos(*pos)
        self.stageInfoImage.setImage(useImg)
        self.stageInfoImage.setOpts(opacity=alpha)
        self.stageInfoImage.show()

    @property
    @lru_cache()
    def displayableInfos(self):
        outInfos = {}
        stages = self.curProcessor.processor.stagesFlattened
        matchNames = incrStageNames(stages)
        boundSlices = None

        for name, stage in zip(matchNames, stages):
            if stage.result is None:
                # Not run yet
                continue
            info = self.curProcessor.processor.singleStageInfo(stage)
            if info:
                # TODO: Figure out which 'info' to use
                info = info[0]
            if info is None or "image" not in info:
                continue
            useImg = info["image"]
            # This image was likely from a cropped area, make sure to put it in the right place
            imPos = (0, 0)
            span = useImg.shape[:2]
            if boundSlices is not None:
                # Resizing may have also occurred
                span = []
                imPos = []
                for curSlice in boundSlices:  # type: slice
                    # Pos is specified in x-y, not row-col
                    curSlice: slice
                    imPos.append(curSlice.start)
                    span.append(curSlice.stop - curSlice.start)
                span = tuple(span)
                imPos = tuple(imPos)
            outInfos[name] = {
                "name": name,
                "image": useImg,
                "span": span,
                "pos": imPos[::-1],
            }
            # Bound slices up to the evaluated stage are used for resizing information
            if isinstance(stage.result, ProcessIO) and "boundSlices" in stage.result:
                boundSlices = stage.result["boundSlices"]
        return outInfos

    def playbackRegionHistory(self):
        initialImg, history = self.getRegionHistory()
        if initialImg is None:
            warnings.warn("No edits found, nothing to do", UserWarning)
            return
        # Add current state as final result
        history += [history[-1]]
        diffs = [showMaskDiff(o, n) for (o, n) in zip(history, history[1:])]
        self.playbackWindow.setDiffs(diffs)
        self.playbackWindow.displayPlt.setImage(initialImg)
        self.playbackWindow.show()
        self.playbackWindow.raise_()
