import sys
from typing import Optional, Callable, Dict

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets, QtGui

from s3a.projectvars import FR_CONSTS
from s3a.structures import FRParam
from s3a.structures import FRVertices


def _clearPoints(roi: pg.ROI):
  while roi.handles:
    roi.removeHandle(0)

# --------
# COLLECTION OF OVERLOADED ROIS THAT KNOW HOW TO UPDATE THEMSELEVES AS NEEDED WITHIN
# THE ANNOTATOR REGIONS
# --------
class FRROIExtension:
  connected = True

  def updateShape(self, ev: QtGui.QMouseEvent, xyEvCoords: FRVertices) -> (bool, Optional[FRVertices]):
    """
    Customized update for the FRROI.

    :param ev: Mouse event (press, release, move, etc.)
    :param xyEvCoords: Location of the event relative to the imgItem

    :return: Tuple (constructingRoi, verts):
              * `constructingRoi`: Whether the ROI is currently under construction
              *  `verts`: Vertices generated by this ROI. If the ROI is not done,
                 `None` is returned instead.
    """

  @property
  def vertices(self): return FRVertices()

class FRExtendedROI(pg.ROI, FRROIExtension):
  """
  Purely for specifying the interface provided by the following classes. This is mainly
  so PyCharm knows what methds and signatures are provided by the ROIs below.
  """


class FRRectROI(pg.RectROI, FRROIExtension):
  connected = True

  def __init__(self):
    super().__init__([-1,-1], [0,0], invertible=True)

  def updateShape(self, ev: QtGui.QMouseEvent, xyEvCoords: FRVertices) -> (
      bool, Optional[FRVertices]):
    """
    See function signature for :func:`FRExtendedROI.updateShape`
    """
    success = True
    verts = None
    constructingRoi = False
    # If not left click, do nothing
    if (int(ev.buttons()) & QtCore.Qt.LeftButton) == 0 \
       and ev.button() != QtCore.Qt.LeftButton:
      return constructingRoi, verts

    evType = ev.type()
    if evType == QtCore.QEvent.MouseButtonPress:
      # Need to start a new shape
      self.setPos(xyEvCoords.asPoint())
      self.setSize(0)
      self.addScaleHandle([1, 1], [0, 0])
      constructingRoi = True
    elif evType == QtCore.QEvent.MouseMove:
      # ROI handle will change the shape as needed, no action required
      constructingRoi = True
    elif evType == QtCore.QEvent.MouseButtonRelease:
      # Done drawing the ROI, complete shape, get vertices
      verts = self.vertices
      constructingRoi = False
    else:
      success = False
      # Unable to process this event

    if success:
      ev.accept()

    return constructingRoi, verts

  @property
  def vertices(self) -> FRVertices:
    origin = np.array([self.pos()])
    sz = np.array([self.size()])
    # Sz will be negative at inverted shape dimensions

    # Outer contours generated by opencv are counterclockwise. Replicate this structure
    # for consistency
    otherCorners = [sz * [0, 1], sz * [1, 1], sz * [1, 0]]

    verts_np = np.vstack([origin, *(origin + otherCorners)])
    verts = FRVertices(verts_np, connected=self.connected, dtype=float)
    return verts

class FRPolygonROI(pg.PolyLineROI, FRROIExtension):
  connected = True
  constructingRoi = False

  def __init__(self, *args, **kwargs):
    # Since this won't execute until after module import, it doesn't cause
    # a dependency
    super().__init__([], closed=False, movable=False, removable=False, *args, **kwargs)
    # Force new menu options
    self.finishPolyAct = QtWidgets.QAction()

  def updateShape(self, ev: QtGui.QMouseEvent, xyEvCoords: FRVertices) -> (
      bool, Optional[FRVertices]):
    """
    See function signature for :func:`FRExtendedROI.updateShape`
    """
    success = True
    verts = None
    evType = ev.type()
    if evType == QtCore.QEvent.MouseButtonPress:
      # Start a new shape if this is the first press, or create a new handle of an existing shape
      if not self.constructingRoi:
        # ROI is new
        self.setPos(xyEvCoords.asPoint())
        self.setPoints([])
      self.addVertex(xyEvCoords)
      self.constructingRoi = True
    elif evType == QtCore.QEvent.MouseMove:
      # ROI handle will change the shape as needed, no action required
      pass
    elif evType in [QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.MouseButtonDblClick]:
      # Check if the placed point is close enough to the first vertex. If so, the shape is done
      vertsToCheck = self.vertices
      if ((len(vertsToCheck) > 2)
          and (evType == QtCore.QEvent.MouseButtonDblClick
            or np.all(np.abs(vertsToCheck[0] - vertsToCheck[-1]) < 5)
          )
      ):
        verts = vertsToCheck
        self.constructingRoi = False
      else:
        # Not done constructing shape, indicate this by clearing the returned list
        pass
    else:
      success = False
      # Unable to process this event

    if success:
      ev.accept()

    return self.constructingRoi, verts

  def addVertex(self, xyEvCoords: FRVertices):
    """
    Creates a new vertex for the polygon
    :param xyEvCoords: Location for the new vertex
    :return: Whether this vertex finishes the polygon
    """
    # Account for moved ROI
    xyEvCoords.x = (xyEvCoords.x - self.x())
    xyEvCoords.y = (xyEvCoords.y - self.y())
    # If enough points exist and new point is 'close' to origin,
    # consider the ROI complete
    newVert = QtCore.QPointF(xyEvCoords.x, xyEvCoords.y)
    self.addFreeHandle(newVert)
    if len(self.handles) > 1:
      self.addSegment(self.handles[-2]['item'], self.handles[-1]['item'])

  @property
  def vertices(self):
    origin = np.array(self.pos())
    if len(self.handles) > 0:
      verts_np = np.array([(h['pos'].x(), h['pos'].y()) for h in self.handles]) + origin
      return FRVertices(verts_np, connected=self.connected, dtype=float)
    else:
      return FRVertices(connected=self.connected, dtype=float)

class FRPaintFillROI(FRPolygonROI):

  connected = False

  def updateShape(self, ev: QtGui.QMouseEvent, xyEvCoords: FRVertices) -> (
      bool, Optional[FRVertices]):
    """
    See function signature for :func:`FRExtendedROI.updateShape`
    """
    success = True
    verts = None
    evType = ev.type()
    # If not left click, do nothing
    if (int(ev.buttons()) & QtCore.Qt.LeftButton) == 0:
      self.constructingRoi = False
      self.setPoints([])
      return self.constructingRoi, verts

    if (evType == ev.MouseButtonPress) | (evType == ev.MouseMove):
      # Started / doing floodfill
      if not self.constructingRoi:
        # Just started. Add a visible point for eye candy
        self.addVertex(xyEvCoords)
      verts = xyEvCoords
      self.constructingRoi = True
    # No occurrence of mouse release, since that would violate the 'if' check for
    # button press
    else:
      success = False

    if success:
      ev.accept()
    return self.constructingRoi, verts

SHAPE_ROI_MAPPING: Dict[FRParam, Callable[[], FRExtendedROI]] = {
  FR_CONSTS.DRAW_SHAPE_PAINT: FRPaintFillROI,
  FR_CONSTS.DRAW_SHAPE_RECT: FRRectROI,
  FR_CONSTS.DRAW_SHAPE_POLY: FRPolygonROI,
}

# Adapted from https://groups.google.com/forum/#!msg/pyqtgraph/tWtjJOQF5x4/CnQF6IgmDAAJ
# def boundingRect(_roi: pg.ROI):
#   pw = 0.5 * _roi.currentPen.width()
#   return QtCore.QRectF(-0.5 * pw, -0.5 * pw, pw + _roi.state['size'][0], pw + _roi.state['size'][1]).normalized()
#
# for roi in SHAPE_ROI_MAPPING.values():
#   roi.boundingRect = boundingRect