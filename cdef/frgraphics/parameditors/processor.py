from __future__ import annotations

from os.path import join
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

import numpy as np
from pyqtgraph.Qt import QtWidgets, QtCore
from pyqtgraph.parametertree import Parameter
from pyqtgraph.parametertree.parameterTypes import ListParameter

from cdef.procwrapper import FRImgProcWrapper
from cdef.projectvars import REQD_TBL_FIELDS, MENU_OPTS_DIR
from cdef.structures import FRComplexVertices, FRVertices, NChanImg, FRParam, \
  FRAlgProcessorError
from imageprocessing.processing import ImageProcess
from .genericeditor import FRParamEditor, _frPascalCaseToTitle
from .table import FRTableData

Signal = QtCore.pyqtSignal

class FRAlgPropsMgr(FRParamEditor):
  sigProcessorCreated = Signal(object) # Signal(FRAlgCollectionEditor)
  def __init__(self, parent=None):
    super().__init__(parent, fileType='', saveDir='')
    self.processorCtors : List[Callable[[], ImageProcess]] = []
    self.spawnedCollections : List[FRAlgCollectionEditor] = []

  def registerGroup(self, groupParam: FRParam, **opts):
    # Don't save a default file for this class
    return super().registerGroup(groupParam, saveDefault=False, **opts)

  def createProcessorForClass(self, clsObj) -> FRAlgCollectionEditor:
    clsName = type(clsObj).__name__
    editorDir = join(MENU_OPTS_DIR, clsName, '')
    # Strip "FR" from class name before retrieving name
    editorName = _frPascalCaseToTitle(clsName[2:]) + ' Processor'
    newEditor = FRAlgCollectionEditor(editorDir, self.processorCtors, name=editorName)
    self.spawnedCollections.append(newEditor)
    # Wrap in property so changes propagate to the calling class
    lims = newEditor.algOpts.opts['limits']
    defaultKey = next(iter(lims))
    defaultAlg = lims[defaultKey]
    newEditor.algOpts.setDefault(defaultAlg)
    newEditor.changeActiveAlg(proc=defaultAlg)
    self.sigProcessorCreated.emit(newEditor)
    return newEditor

  def addProcessCtor(self, procCtor: Callable[[], ImageProcess]):
    self.processorCtors.append(procCtor)
    for algCollection in self.spawnedCollections:
      algCollection.addImageProcessor(procCtor())

class FRAlgCollectionEditor(FRParamEditor):
  def __init__(self, saveDir, procCtors: List[Callable[[], ImageProcess]],
               name=None, parent=None):
    algOptDict = {
      'name': 'Algorithm', 'type':  'list', 'values': [], 'value': 'N/A'
    }
    self.treeAlgOpts: Parameter = Parameter(name='Algorithm Selection', type='group', children=[algOptDict])
    self.algOpts: ListParameter = self.treeAlgOpts.children()[0]
    self.algOpts.sigValueChanged.connect(lambda param, proc: self.changeActiveAlg(proc))
    super().__init__(parent, saveDir=saveDir, fileType='alg', name=name,
                     childForOverride=self.algOpts)

    Path(self.saveDir).mkdir(parents=True, exist_ok=True)

    self.curProcessor: Optional[FRImgProcWrapper] = None
    self.nameToProcMapping: Dict[str, FRImgProcWrapper] = {}
    self._image = np.zeros((1,1), dtype='uint8')

    self.VERT_LST_NAMES = ['fgVerts', 'bgVerts']
    self.vertBuffers: Dict[str, FRComplexVertices] = {
      vType: FRComplexVertices() for vType in self.VERT_LST_NAMES
    }

    wrapped : Optional[FRImgProcWrapper] = None
    for processorCtor in procCtors:
      # Retrieve proc so default can be set after
      wrapped = self.addImageProcessor(processorCtor())
    self.algOpts.setDefault(wrapped)
    self.changeActiveAlg(proc=wrapped)
    self.saveParamState('Default', allowOverwriteDefault=True)

  def run(self, **kwargs):
    # for vertsName in self.VERT_LST_NAMES:
    #   curVerts = kwargs[vertsName]
    #   if curVerts is not None:
    #     self.vertBuffers[vertsName].append(curVerts)
    # for vertsName in self.VERT_LST_NAMES:
    #   arg = self.vertBuffers[vertsName].stack()
    #   kwargs[vertsName] = arg
    for name in 'fgVerts', 'bgVerts':
      if kwargs[name] is None:
        kwargs[name] = FRVertices()
    retVal = self.curProcessor.run(**kwargs)
    # self.vertBuffers = {name: FRComplexVertices() for name in self.VERT_LST_NAMES}
    return retVal

  def resultAsVerts(self, localEstimate=True):
    return self.curProcessor.resultAsVerts(localEstimate=localEstimate)

  @property
  def image(self):
    return self._image
  @image.setter
  def image(self, newImg: NChanImg):
    if self.curProcessor is not None:
      self.curProcessor.image = newImg
    self._image = newImg

  def addImageProcessor(self, newProc: ImageProcess):
    processor = FRImgProcWrapper(newProc, self)
    self.tree.addParameters(self.params.child(processor.algName))

    self.nameToProcMapping.update({processor.algName: processor})
    self.algOpts.setLimits(self.nameToProcMapping.copy())
    return processor

  def saveParamState(self, saveName: str=None, paramState: dict=None,
                     allowOverwriteDefault=False):
    """
    The algorithm editor also needs to store information about the selected algorithm, so lump
    this in with the other parameter information before calling default save.
    """
    paramState = {'Selected Algorithm': self.algOpts.value().algName,
                  'Parameters': self.params.saveState(filter='user')}
    return super().saveParamState(saveName, paramState, allowOverwriteDefault)

  def loadParamState(self, stateName: str, stateDict: dict=None):
    stateDict = self._parseStateDict(stateName, stateDict)
    selectedOpt = stateDict.get('Selected Algorithm', None)
    # Get the impl associated with this option name
    isLegitSelection = selectedOpt in self.algOpts.opts['limits']
    if not isLegitSelection:
      selectedImpl = self.algOpts.value()
      raise FRAlgProcessorError(f'Selection {selectedOpt} does'
                                f' not match the list of available algorithms. Defaulting to {selectedImpl}')
    else:
      selectedImpl = self.algOpts.opts['limits'][selectedOpt]
    self.algOpts.setValue(selectedImpl)
    super().loadParamState(self.lastAppliedName, stateDict['Parameters'])

  def changeActiveAlg(self, proc: FRImgProcWrapper):
    # Hide all except current selection
    # TODO: Find out why hide() isn't working. Documentation indicates it should
    # Instead, use the parentChanged utility as a hacky workaround
    selectedParam = self.params.child(proc.algName)
    for ii, child in enumerate(self.params.children()):
      shouldHide = child is not selectedParam
      # Offset by 1 to account for self.algOpts
      self.tree.setRowHidden(1 + ii, QtCore.QModelIndex(), shouldHide)
    # selectedParam.show()
    self.curProcessor = proc
    self.curProcessor.image = self.image
