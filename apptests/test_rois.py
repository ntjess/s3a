import pytest
from typing import Tuple

from pyqtgraph.Qt import QtCore, QtGui
import numpy as np

from s3a.shared import SharedAppSettings
from s3a.views.imageareas import MainImage
from s3a.views.rois import SHAPE_ROI_MAPPING
from s3a.constants import PRJ_CONSTS
from utilitys import EditorPropsMixin

shapes = tuple(SHAPE_ROI_MAPPING.keys())
with EditorPropsMixin.setOpts(shared=SharedAppSettings()):
  editableImg = MainImage(drawShapes=shapes,
                          drawActions=(PRJ_CONSTS.DRAW_ACT_SELECT,),)
clctn = editableImg.shapeCollection

def leftClick(pt: Tuple[int, int]):
  btns = QtCore.Qt.MouseButton
  event = QtGui.QMouseEvent(
    QtCore.QEvent.Type.MouseButtonPress,
    QtCore.QPoint(*pt),
    btns.LeftButton,
    btns.LeftButton,
    QtCore.Qt.KeyboardModifier.NoModifier,
  )
  clctn.buildRoi(event)

@pytest.fixture
def mouseDragFactory(qtbot):
  def mouseDrag(widget: MainImage, startPos, endPos):
    btns = QtCore.Qt.MouseButton
    startPos = widget.imgItem.mapToScene(startPos)
    endPos = widget.imgItem.mapToScene(endPos)
    press = QtGui.QMouseEvent(QtGui.QMouseEvent.Type.MouseButtonPress,
                              startPos, btns.LeftButton, btns.LeftButton,
                              QtCore.Qt.KeyboardModifier.NoModifier)
    widget.mousePressEvent(press)

    move = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseMove,
                             endPos, btns.LeftButton, btns.LeftButton,
                             QtCore.Qt.KeyboardModifier.NoModifier,)
    widget.mouseMoveEvent(move)

    release = QtGui.QMouseEvent(QtGui.QMouseEvent.Type.MouseButtonRelease,
                                endPos, btns.LeftButton, btns.LeftButton,
                                QtCore.Qt.KeyboardModifier.NoModifier)
    widget.mouseReleaseEvent(release)
  return mouseDrag

def test_simple_click():
  # For now just make sure no errors occur when dragging one vertex, since
  # this is a legal and expected op for every one
  editableImg.shapeCollection.forceUnlock()
  pt = (0,0)
  editableImg.drawAction = PRJ_CONSTS.DRAW_ACT_SELECT
  for curShape in shapes:
    clctn.curShapeParam = curShape
    leftClick(pt)
    assert np.all(pt in clctn.curShape.vertices)

def test_drag_pt(mouseDragFactory):
  editableImg.drawAction = PRJ_CONSTS.DRAW_ACT_SELECT
  for curShape in shapes:
    clctn.curShapeParam = curShape
    mouseDragFactory(editableImg, QtCore.QPoint(10,10), QtCore.QPoint(100,100))
    # Shapes need real mouse events to properly form, so all this can really do is
    # ensure nothing breaks