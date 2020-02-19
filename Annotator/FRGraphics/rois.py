from typing import Optional

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets, QtGui
from pyqtgraph import ROI, PolyLineROI, RectROI, LineSegmentROI
import numpy as np

from Annotator.exceptions import FRInvalidROIEvType
from Annotator.params import FRVertices

def _getImgMask(roi: pg.ROI, imgItem: pg.ImageItem):
  imgMask = np.zeros(imgItem.image.shape[0:2], dtype='bool')
  roiSlices, _ = roi.getArraySlice(imgMask, imgItem)
  # TODO: Clip regions that extend beyond imgItem dimensions
  roiSz = [curslice.stop - curslice.start for curslice in roiSlices]
  # renderShapeMask takes width, height args. roiSlices has row/col sizes,
  # so switch this order when passing to renderShapeMask
  roiSz = roiSz[::-1]
  roiMask = roi.renderShapeMask(*roiSz).astype('uint8')
  # Also, the return value for renderShapeMask is given in col-major form.
  # Transpose this, since all other data is in row-major.
  roiMask = roiMask.T
  imgMask[roiSlices[0], roiSlices[1]] = roiMask
  return imgMask

def _clearPoints(roi: pg.ROI):
  while roi.handles:
    roi.removeHandle(0)

# --------
# COLLECTION OF OVERLOADED ROIS THAT KNOW HOW TO UPDATE THEMSELEVES AS NEEDED WITHIN
# THE ANNOTATOR REGIONS
# --------
class FRExtendedROI(pg.ROI):
  """
  Purely for specifying the interface provided by the following classes. This is mainly
  so PyCharm knows what methds and signatures are provided by the ROIs below.
  """
  connected: bool

  def updateShape(self, ev: QtGui.QMouseEvent, xyEvCoords: np.ndarray) -> (bool, Optional[FRVertices]):
    """
    Customized update for the FRROI.

    :param ev: Mouse event (press, release, move, etc.)
    :param xyEvCoords: Location of the event relative to the imgItem

    :return: Tuple (constructingRoi, verts):
              * `constructingRoi`: Whether the ROI is currently under construction
              *  `verts`: Vertices generated by this ROI. If the ROI is not done,
                 `None` is returned instead.
    """

class FRRectROI(pg.RectROI):
  connected = True

  def __init__(self):
    super().__init__([-1,-1], [0,0])

  def updateShape(self, ev: QtGui.QMouseEvent, xyEvCoords: np.ndarray) -> (
      bool, Optional[FRVertices]):
    """
    See function signature for :func:`FRExtendedROI.updateShape`
    """
    success = True
    verts = None
    constructingRoi = False
    # If not left click, do nothing
    if (int(ev.buttons()) & QtCore.Qt.LeftButton) == 0:
      return constructingRoi, verts

    evType = ev.type()
    if evType == QtCore.QEvent.MouseButtonPress:
      # Need to start a new shape
      self.setPos(xyEvCoords)
      self.setSize(0)
      self.addScaleHandle([1, 1], [0, 0])
      constructingRoi = True
    elif evType == QtCore.QEvent.MouseMove:
      # ROI handle will change the shape as needed, no action required
      constructingRoi = True
    elif evType == QtCore.QEvent.MouseButtonRelease:
      # Done drawing the ROI, complete shape, get vertices
      verts_list = [[tup[1].x(), tup[1].y()] for tup in self.getSceneHandlePositions()]
      verts = FRVertices.createFromArr(verts_list)
      constructingRoi = False
    else:
      success = False
      # Unable to process this event

    if success:
      ev.accept()

    return constructingRoi, verts

class FRPolygonROI(pg.PolyLineROI):
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
        self.setPos(xyEvCoords.flatten())
        self.setPoints([])
      self.addVertex(xyEvCoords)
      self.constructingRoi = True
    elif evType == QtCore.QEvent.MouseMove:
      # ROI handle will change the shape as needed, no action required
      pass
    elif evType == QtCore.QEvent.MouseButtonRelease:
      # Check if the placed point is close enough to the first vertex. If so, the shape is done
      verts_list = np.array([[tup[1].x(), tup[1].y()] for tup in self.getSceneHandlePositions()])
      if (len(verts_list) > 2) \
      and np.all(np.abs(verts_list[0] - verts_list[-1]) < 5):
        verts = FRVertices.createFromArr(verts_list)
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

class FRPaintFillROI(FRPolygonROI):

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