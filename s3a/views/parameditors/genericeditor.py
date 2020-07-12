from __future__ import annotations

import weakref
from typing import List, Dict, Union, Type, Tuple

from pyqtgraph.Qt import QtWidgets, QtCore
from pyqtgraph.parametertree import Parameter

from s3a.models.editorbase import FRParamEditorBase
from s3a.graphicsutils import dialogGetSaveFileName
from s3a.structures import FRParam, FilePath

Signal = QtCore.Signal

def clearUnwantedParamVals(paramState: dict):
  for k, child in paramState.get('children', {}).items():
    clearUnwantedParamVals(child)
  if paramState.get('value', True) is None:
    paramState.pop('value')

class FRParamEditorDockGrouping(QtWidgets.QDockWidget):

  def __init__(self, editors: List[FRParamEditor], dockName, parent=None):
    super().__init__(parent)
    self.tabs = QtWidgets.QTabWidget(self)
    self.hide()
    for editor in editors:
      # "Main Image Settings" -> "Settings"
      self.tabs.addTab(editor.dockContentsWidget, editor.name.split(dockName)[1][1:])
      editor.dock = self
    mainLayout = QtWidgets.QVBoxLayout()
    mainLayout.addWidget(self.tabs)
    centralWidget = QtWidgets.QWidget()
    centralWidget.setLayout(mainLayout)
    self.setWidget(centralWidget)
    if dockName is None:
      dockName = editors[0].name
    self.setObjectName(dockName)
    self.setWindowTitle(dockName)

    self.name = dockName
    self.editors = editors

_childTuple_asValue = Tuple[FRParam,...]
childTuple_asParam = Tuple[Tuple[FRParam,...], bool]
_keyType = Union[FRParam, Union[_childTuple_asValue, childTuple_asParam]]
class FRParamEditor(FRParamEditorBase):
  """
  GUI controls for user-interactive parameters within S3A. Each window consists of
  a parameter tree and basic saving capabilities.
  """
  def __init__(self, parent=None, paramList: List[Dict]=None, saveDir: FilePath='.',
               fileType='param', name=None, topTreeChild: Parameter=None,
               registerCls: Type=None, registerParam: FRParam=None, **registerGroupOpts):
    super().__init__(parent, paramList, saveDir, fileType, name, topTreeChild,
                     registerCls, registerParam, **registerGroupOpts)
    self.dock = self
    self.hide()
    self.setWindowTitle(self.name)
    self.setObjectName(self.name)

    # This will be set to 'True' when an action for this editor is added to
    # the main window menu
    self.hasMenuOption = False

    # -----------
    # Additional widget buttons
    # -----------
    self.saveAsBtn = QtWidgets.QPushButton('Save As...')
    self.applyBtn = QtWidgets.QPushButton('Apply')
    self.closeBtn = QtWidgets.QPushButton('Close')

    # -----------
    # Widget layout
    # -----------
    self.dockContentsWidget = QtWidgets.QWidget(parent)
    self.setWidget(self.dockContentsWidget)
    btnLayout = QtWidgets.QHBoxLayout()
    btnLayout.addWidget(self.saveAsBtn)
    btnLayout.addWidget(self.applyBtn)
    btnLayout.addWidget(self.closeBtn)

    self.centralLayout = QtWidgets.QVBoxLayout(self.dockContentsWidget)
    self.centralLayout.addWidget(self.tree)
    self.centralLayout.addLayout(btnLayout)
    # self.setLayout(centralLayout)
    self.tree.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
    # -----------
    # UI Element Signals
    # -----------
    self.saveAsBtn.clicked.connect(self.saveParamState_gui)
    self.closeBtn.clicked.connect(self.close)
    self.applyBtn.clicked.connect(self.applyChanges)

    if registerCls is not None:
      self.registerGroup(registerParam)(registerCls)

  def _paramTreeChanged(self, param, child, idx):
    self._stateBeforeEdit = self.params.saveState()

  def _expandCols(self):
    # totWidth = 0
    for colIdx in range(2):
      self.tree.resizeColumnToContents(colIdx)
    #   totWidth += self.tree.columnWidth(colIdx) + self.tree.margin
    # appInst.processEvents()
    # self.dockContentsWidget.setMinimumWidth(totWidth)
    self.tree.setColumnWidth(0, self.width()//2)
    self.resize(self.tree.width(), self.height())

  def show(self):
    self.setWindowState(QtCore.Qt.WindowActive)
    # Necessary on MacOS
    self.raise_()
    # Necessary on Windows
    self.activateWindow()
    self.applyBtn.setFocus()
    super().show()

  def reject(self):
    """
    If window is closed apart from pressing 'accept', restore pre-edit state
    """
    self.params.restoreState(self._stateBeforeEdit, removeChildren=False)
    super().reject()

  def applyChanges(self):
    # Don't emit any signals if nothing changed
    newState = self.params.saveState(filter='user')
    outDict = self.params.getValues()
    if self._stateBeforeEdit != newState:
      self._stateBeforeEdit = newState
      self.sigParamStateUpdated.emit(outDict)
    return outDict

  def saveParamState_gui(self):
    paramState = self.params.saveState(filter='user')
    saveName = dialogGetSaveFileName(self, 'Save As', self.lastAppliedName)
    self.saveParamState(saveName, paramState)