from functools import partial
from typing import Sequence
from warnings import warn

import numpy as np
import pandas as pd
from pandas import DataFrame as df
from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

from s3a import FR_SINGLETON
from s3a.models.tablemodel import FRComponentMgr
from s3a.projectvars import FR_CONSTS, FR_ENUMS, REQD_TBL_FIELDS
from s3a.structures import FRS3AException, FRS3AWarning, OneDArr

__all__ = ['FRCompTableView']

from .parameditors import pgregistered

Signal = QtCore.Signal

TBL_FIELDS = FR_SINGLETON.tableData.allFields

class FRPopupTableDialog(QtWidgets.QDialog):
  def __init__(self, *args):
    super().__init__(*args)
    self.setModal(True)
    # -----------
    # Table View
    # -----------
    self.tbl = FRCompTableView(minimal=True)
    # Keeps track of which columns were edited by the user
    self.dirtyColIdxs = []

    # -----------
    # Warning Message
    # -----------
    self.titles = np.array(self.tbl.mgr.colTitles)
    self.warnLbl = QtWidgets.QLabel(self)
    self.warnLbl.setStyleSheet("font-weight: bold; color:red; font-size:14")
    self.updateWarnMsg([])

    # -----------
    # Widget buttons
    # -----------
    self.applyBtn = QtWidgets.QPushButton('Apply')
    self.closeBtn = QtWidgets.QPushButton('Close')

    # -----------
    # Widget layout
    # -----------
    btnLayout = QtWidgets.QHBoxLayout()
    btnLayout.addWidget(self.applyBtn)
    btnLayout.addWidget(self.closeBtn)

    centralLayout = QtWidgets.QVBoxLayout()
    centralLayout.addWidget(self.warnLbl)
    centralLayout.addWidget(self.tbl)
    centralLayout.addLayout(btnLayout)
    self.setLayout(centralLayout)
    self.setMinimumWidth(self.tbl.width())

    # -----------
    # UI Element Signals
    # -----------
    self.closeBtn.clicked.connect(self.close)
    self.applyBtn.clicked.connect(self.accept)
    # TODO: Find if there's a better way to see if changes happen in a table
    for colIdx in range(len(self.titles)):
      deleg = self.tbl.itemDelegateForColumn(colIdx)
      deleg.commitData.connect(partial(self.reflectDataChanged, colIdx))

  def updateWarnMsg(self, updatableCols: Sequence[str]):
    warnMsg = 'Note! '
    if len(updatableCols) == 0:
      tblInfo = 'No information'
    else:
      tblInfo = "Only " + ", ".join(updatableCols)
    warnMsg += f'{tblInfo} will be updated from this view.'
    self.warnLbl.setText(warnMsg)

  @property
  def data(self):
    return self.tbl.mgr.compDf.iloc[[0],:]

  def setData(self, compDf: df, colIdxs: Sequence):
    # New selection, so reset dirty columns
    self.dirtyColIdxs = []
    # Hide columns that weren't selected by the user since these changes
    # Won't propagate
    for ii in range(len(self.titles)):
      if ii not in colIdxs:
        self.tbl.hideColumn(ii)
    self.tbl.mgr.rmComps()
    self.tbl.mgr.addComps(compDf, addtype=FR_ENUMS.COMP_ADD_AS_MERGE)
    self.updateWarnMsg([])

  def reflectDataChanged(self, editedColIdx: int):
    """Responds to user editing the value in a cell by setting a dirty bit for that column"""
    if editedColIdx not in self.dirtyColIdxs:
      self.dirtyColIdxs.append(editedColIdx)
      self.dirtyColIdxs.sort()
      self.updateWarnMsg(self.titles[self.dirtyColIdxs])

  def reject(self):
    # On dialog close be sure to unhide all columns / reset dirty cols
    self.dirtyColIdxs = []
    for ii in range(len(self.titles)):
      self.tbl.showColumn(ii)
    super().reject()

@FR_SINGLETON.registerGroup(FR_CONSTS.CLS_COMP_TBL)
class FRCompTableView(QtWidgets.QTableView):
  """
  Table for displaying :class:`FRComponentMgr` data.
  """
  sigSelectionChanged = Signal(object)

  @classmethod
  def __initEditorParams__(cls):
    cls.showOnCreate = FR_SINGLETON.generalProps.registerProp(cls, FR_CONSTS.PROP_SHOW_TBL_ON_COMP_CREATE)

  def __init__(self, *args, minimal=False):
    """
    Creates the table.

    :param minimal: Whether to make a table with minimal features.
       If false, creates extensible table with context menu options.
       Otherwise, only contains minimal features.
    """
    super().__init__(*args)
    # self.__initEditorParams__()
    self._prevSelRows = np.array([])
    self.setSortingEnabled(True)

    self.mgr = FRComponentMgr()
    self.setModel(self.mgr)

    self.minimal = minimal
    if not minimal:
      self.popup = FRPopupTableDialog(*args)
      # Create context menu for changing table rows
      self.menu = self.createContextMenu()
      self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
      cursor = QtGui.QCursor()
      self.customContextMenuRequested.connect(lambda: self.menu.exec_(cursor.pos()))

    self.instIdColIdx = TBL_FIELDS.index(REQD_TBL_FIELDS.INST_ID)

    for ii, field in enumerate(TBL_FIELDS):
      curType = field.valType
      curval = field.value
      paramDict = dict(type=curType, default=curval)
      if curType == 'Enum':
        paramDict['type'] = 'list'
        paramDict.update(values=list(type(curval)))
      elif curType == 'FRParam':
        paramDict['type'] = 'list'
        paramDict.update(values=list(curval.group))
      elif curType == 'bool':
        # TODO: Get checkbox to stay in table after editing for a smoother appearance.
        #   For now, the easiest solution is to just use dropdown
        paramDict['type'] = 'list'
        paramDict.update(values={'True': True, 'False': False})
      try:
        self.setItemDelegateForColumn(ii, pgregistered.FRPgParamDelegate(paramDict, self))
      except FRS3AException:
        # Parameter doesn't have a registered pyqtgraph editor, so default to
        # generic text editor
        paramDict['type'] = 'text'
        paramDict['default'] = str(curval)
        self.setItemDelegateForColumn(ii, pgregistered.FRPgParamDelegate(paramDict, self))

    self.horizontalHeader().setSectionsMovable(True)

  # When the model is changed, get a reference to the FRComponentMgr
  def setModel(self, modelOrProxy: QtCore.QAbstractTableModel):
    super().setModel(modelOrProxy)
    try:
      # If successful we were given a proxy model
      self.mgr = modelOrProxy.sourceModel()
    except AttributeError:
      self.mgr = modelOrProxy

  def selectionChanged(self, curSel: QtCore.QItemSelection, prevSel: QtCore.QItemSelection):
    """
    When the selected rows in the table change, this retrieves the corresponding previously
    and newly selected IDs. They are then emitted in sigSelectionChanged.
    """
    super().selectionChanged(curSel, prevSel)
    selectedIds = []
    selection = self.selectionModel().selectedIndexes()
    for item in selection:
      selectedIds.append(item.sibling(item.row(),self.instIdColIdx).data(QtCore.Qt.EditRole))
    newRows = pd.unique(selectedIds)
    if np.array_equal(newRows, self._prevSelRows):
      return
    self._prevSelRows = newRows
    self.sigSelectionChanged.emit(pd.unique(selectedIds))

  def createContextMenu(self):
    menu = QtWidgets.QMenu(self)
    menu.setTitle('Table Actions')

    remAct = QtWidgets.QAction("Remove", menu)
    remAct.triggered.connect(lambda: self.removeSelectedRows_gui())
    menu.addAction(remAct)

    overwriteAct = QtWidgets.QAction("Set Same As First", menu)
    overwriteAct.triggered.connect(lambda: self.overwriteSelectedRows_gui())
    menu.addAction(overwriteAct)

    setAsAct = QtWidgets.QAction("Set As...", menu)
    menu.addAction(setAsAct)
    setAsAct.triggered.connect(lambda: self.setSelectedCellsAs_gui())

    return menu

  @FR_SINGLETON.shortcuts.registerMethod(FR_CONSTS.SHC_TBL_DEL_ROWS)
  def removeSelectedRows_gui(self):
    if self.minimal: return

    # Make sure the user actually wants this
    dlg = QtWidgets.QMessageBox()
    confirm  = dlg.question(self, 'Remove Rows', 'Are you sure you want to remove these rows?',
                 dlg.Yes | dlg.Cancel)
    if confirm == dlg.Yes:
      # Proceed with operation
      idList = [idx.sibling(idx.row(), self.instIdColIdx).data(QtCore.Qt.EditRole) for idx in self.selectedIndexes()]
      # Since each selection represents a row, remove duplicate row indices
      idList = pd.unique(idList)
      self.mgr.rmComps(idList)
      self.clearSelection()


  @FR_SINGLETON.shortcuts.registerMethod(FR_CONSTS.SHC_TBL_SET_SAME_AS_FIRST)
  def overwriteSelectedRows_gui(self):
    if self.minimal: return

    # Make sure the user actually wants this
    dlg = QtWidgets.QMessageBox()
    warnMsg = f'This operation will overwrite *ALL* selected columns with the corresponding column values from'\
              f' the FIRST row in your selection. PLEASE USE CAUTION. Do you wish to proceed?'
    confirm  = dlg.question(self, 'Overwrite Rows', warnMsg, dlg.Yes | dlg.Cancel)
    if confirm != dlg.Yes:
      return
    idList, colIdxs = self.getIds_colsFromSelection()
    if idList is None:
      return
    toOverwrite = self.mgr.compDf.loc[idList].copy()
    # Some bug is preventing the single assignment value from broadcasting
    setVals = [toOverwrite.iloc[0,colIdxs] for _ in range(len(idList)-1)]
    toOverwrite.iloc[1:, colIdxs] = setVals
    self.mgr.addComps(toOverwrite, addtype=FR_ENUMS.COMP_ADD_AS_MERGE)
    self.clearSelection()

  def getIds_colsFromSelection(self, ignoreNoEditCols=False, ignoreNoneSelection=False):
    selectedIdxs = self.selectedIndexes()
    idList = []
    colIdxs = []
    for idx in selectedIdxs:
      # 0th row contains instance ID
      # TODO: If the user is allowed to reorder columns this needs to be revisited
      idAtIdx = idx.sibling(idx.row(), 0).data(QtCore.Qt.EditRole)
      idList.append(idAtIdx)
      colIdxs.append(idx.column())
    idList = pd.unique(idList)
    colIdxs = pd.unique(colIdxs)
    if not ignoreNoEditCols:
      colIdxs = np.setdiff1d(colIdxs, self.model().sourceModel().noEditColIdxs)
    if not ignoreNoneSelection and (len(idList) == 0 or len(colIdxs) == 0):
      warn('No editable columns selected.', FRS3AWarning)
      return None, None
    return idList, colIdxs

  def setCellsAs(self, idList: Sequence[int], colIdxs: Sequence[int],
                 overwriteData: df):
    if colIdxs is None:
      colIdxs = np.arange(self.mgr.columnCount(), dtype=int)
      colIdxs = np.setdiff1d(colIdxs, self.mgr.noEditColIdxs)
    if len(idList) == 0 or len(colIdxs) == 0:
      warn('No editable cells were specified so no action was performed',
           FRS3AWarning)
      return

    toOverwrite = self.mgr.compDf.loc[idList].copy()
    # Only overwrite dirty columns from popup

    # Some bug is preventing the single assignment value from broadcasting
    setVals = [overwriteData.iloc[0, colIdxs] for _ in range(len(idList))]
    toOverwrite.iloc[:, colIdxs] = setVals
    self.mgr.addComps(toOverwrite, addtype=FR_ENUMS.COMP_ADD_AS_MERGE)

  @FR_SINGLETON.shortcuts.registerMethod(FR_CONSTS.SHC_TBL_SET_AS)
  def setSelectedCellsAs_gui(self, overrideIds: OneDArr=None, overrideColIdxs: OneDArr=None):
    if self.minimal: return

    idList, _ = self.getIds_colsFromSelection(ignoreNoEditCols=True,
                                                    ignoreNoneSelection=True)
    if overrideIds is not None:
      idList = overrideIds
    if overrideColIdxs is not None:
      colIdxs = overrideColIdxs
    else:
      colIdxs = np.arange(len(FR_SINGLETON.tableData.allFields))
    colIdxs = np.setdiff1d(colIdxs, self.mgr.noEditColIdxs)
    if idList is None or len(colIdxs) == 0:
      return
    dataToSet = self.mgr.compDf.loc[[idList[0]],:].copy()
    with FR_SINGLETON.actionStack.ignoreActions():
      self.popup.setData(dataToSet, colIdxs)
      wasAccepted = self.popup.exec()
    if wasAccepted and len(self.popup.dirtyColIdxs) > 0:
      colIdxs = np.intersect1d(colIdxs, self.popup.dirtyColIdxs)
      # False positive
      # noinspection PyTypeChecker
      self.setCellsAs(idList, colIdxs, self.popup.data)