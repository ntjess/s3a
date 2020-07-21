from __future__ import annotations

from functools import partial
from typing import Tuple, Collection, Callable, Union, Iterable, Any, Dict

from pyqtgraph.Qt import QtWidgets, QtGui, QtCore

from s3a import FR_SINGLETON
from s3a.projectvars import FR_CONSTS
from s3a.structures import FRParam

__all__ = ['FRDrawOpts']

class FRDrawOpts(QtWidgets.QWidget):
  def __init__(self, shapeGrp: FRButtonCollection, actGrp: FRButtonCollection,
               parent: QtWidgets.QWidget=None):
    """
    Creates a draw options widget hosting both shape and action selection buttons.
    :param parent: UI widget whose destruction will also destroy these widgets
    :param shapeGrp: Shape options that will appear in the widget
    :param actGrp: Action optiosn that will appear in the widget
    """
    super().__init__(parent)
    # Create 2 layout versions so on resize the group boxes can 'wrap'
    self.topLayout = QtWidgets.QHBoxLayout()
    self.setLayout(self.topLayout)

    # SHAPES

    self.topLayout.addWidget(shapeGrp)
    # ACTIONS
    self.topLayout.addWidget(actGrp)
    self.topLayout.setDirection(self.topLayout.LeftToRight)
    self.horizWidth = self.layout().minimumSize().width()

  def resizeEvent(self, ev: QtGui.QResizeEvent) -> None:
    if self.width() < self.horizWidth + 30:
      self.topLayout.setDirection(self.topLayout.TopToBottom)
    else:
      self.topLayout.setDirection(self.topLayout.LeftToRight)
    super().resizeEvent(ev)

  def selectOpt(self, shapeOrAction: FRParam):
    """
    Programmatically selects a shape or action from the existing button group.
    Whether a shape or action is passed in is inferred from which button group
    :param:`shapeOrAction` belongs to

    :param shapeOrAction: The button to select
    :return: None
    """
    # TODO: This should probably be more robust
    if shapeOrAction in self.shapeBtnParamMap.inverse:
      self.shapeBtnParamMap.inverse[shapeOrAction].setChecked(True)
    elif shapeOrAction in self.actionBtnParamMap.inverse:
      self.actionBtnParamMap.inverse[shapeOrAction].setChecked(True)

class _DEFAULT_OWNER: pass
"""None is a valid owner, so create a sentinel that's not valid"""
btnCallable = Callable[[FRParam], Any]
class FRButtonCollection(QtWidgets.QGroupBox):
  def __init__(self, parent=None, title: str=None, btnParams: Collection[FRParam]=(),
               btnTriggerFns: Union[btnCallable, Collection[btnCallable]]=(),
               exclusive=True, checkable=True, ownerObj=_DEFAULT_OWNER):
    super().__init__(parent)
    self.uiLayout = QtWidgets.QHBoxLayout(self)
    self.btnGroup = QtWidgets.QButtonGroup(self)
    self.paramToFuncMapping: Dict[FRParam, btnCallable] = dict()
    self.paramToBtnMapping: Dict[FRParam, QtWidgets.QPushButton] = dict()
    if title is not None:
      self.setTitle(title)
    self.btnGroup.setExclusive(exclusive)

    if not isinstance(btnTriggerFns, Iterable):
      btnTriggerFns = [btnTriggerFns]*len(btnParams)
    for param, fn in zip(btnParams, btnTriggerFns):
      self.create_addBtn(param, fn, checkable, ownerObj)

  def create_addBtn(self, btnParam: FRParam, triggerFn: btnCallable, checkable=True,
                    ownerObj: Union[type, Any]=_DEFAULT_OWNER):
    if ownerObj is _DEFAULT_OWNER:
      ownerObj = self.parent()
    newBtn = FR_SINGLETON.shortcuts.createRegisteredButton(btnParam, ownerObj)
    if checkable:
      newBtn.setCheckable(True)
      oldTriggerFn = triggerFn
      # If the button is chekcable, only call this function when the button is checked
      def newTriggerFn(param: FRParam):
        if newBtn.isChecked():
          oldTriggerFn(param)
      triggerFn = newTriggerFn
    newBtn.clicked.connect(lambda: self.callFuncByParam(btnParam))

    self.btnGroup.addButton(newBtn)
    self.uiLayout.addWidget(newBtn)
    self.paramToFuncMapping[btnParam] = triggerFn
    self.paramToBtnMapping[btnParam] = newBtn

  def callFuncByParam(self, param: FRParam):
    if param is None:
      return
    # Ensure function is called in the event it requires a button to be checked
    btn = self.paramToBtnMapping[param]
    if btn.isCheckable():
      btn.setChecked(True)
    self.paramToFuncMapping[param](param)