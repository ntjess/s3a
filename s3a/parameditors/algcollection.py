from __future__ import annotations

from inspect import isclass
from pathlib import Path
from typing import Optional, Dict, List, Callable, Union, Type

from pyqtgraph.Qt import QtCore
from pyqtgraph.parametertree import Parameter
from pyqtgraph.parametertree.parameterTypes import ListParameter
from s3a.constants import MENU_OPTS_DIR
from s3a.generalutils import pascalCaseToTitle
from s3a.structures import AlgProcessorError

from .genericeditor import ParamEditor
from .pgregistered import ProcGroupParameter
from ..processing import GeneralProcWrapper, GeneralProcess

Signal = QtCore.Signal

class AlgCtorCollection(ParamEditor):
  # sigProcessorCreated = Signal(object) # Signal(AlgCollectionEditor)
  def __init__(self, procWrapType: Type[GeneralProcWrapper], parent=None):
    super().__init__(parent, saveDir='', fileType='')
    self.processorCtors : List[Callable[[], GeneralProcess]] = []
    self.spawnedCollections : List[AlgParamEditor] = []
    self.procWrapType = procWrapType

  def createProcessorForClass(self, clsObj, editorName='Processor') -> AlgParamEditor:
    if not isclass(clsObj):
      clsObj = type(clsObj)
    clsName = clsObj.__name__
    formattedClsName = pascalCaseToTitle(clsName)
    editorDir = MENU_OPTS_DIR/formattedClsName.lower()
    newEditor = AlgParamEditor(editorDir, self.processorCtors, self.procWrapType, name=editorName)
    self.spawnedCollections.append(newEditor)
    # Wrap in property so changes propagate to the calling class
    lims = newEditor.algOpts.opts['limits']
    defaultKey = next(iter(lims))
    defaultAlg = lims[defaultKey]
    newEditor.algOpts.setDefault(defaultAlg)
    newEditor.switchActiveProcessor(proc=defaultAlg)
    # self.sigProcessorCreated.emit(newEditor)
    return newEditor

  def addProcessCtor(self, procCtor: Callable[[], GeneralProcess]):
    self.processorCtors.append(procCtor)
    for algCollection in self.spawnedCollections:
      algCollection.addProcessor(procCtor())

  def addProcessFunction(self, func: Callable, procType: Type[GeneralProcess], name:str=None, **kwargs):
    def ctor():
      return procType.fromFunction(func, name=name, **kwargs)
    self.addProcessCtor(ctor)

class AlgParamEditor(ParamEditor):
  def __init__(self, saveDir, procCtors: List[Callable[[], GeneralProcess]],
               procWrapType: Type[GeneralProcWrapper], name=None, parent=None):
    algOptDict = {
      'name': 'Algorithm', 'type':  'list', 'values': [], 'value': 'N/A'
    }
    self.treeAlgOpts: Parameter = Parameter(name='Algorithm Selection', type='group', children=[algOptDict])
    self.algOpts: ListParameter = self.treeAlgOpts.children()[0]
    self.algOpts.sigValueChanged.connect(lambda param, proc: self.switchActiveProcessor(proc))
    super().__init__(parent, saveDir=saveDir, fileType='alg', name=name,
                     topTreeChild=self.algOpts)
    self.expandAllBtn.hide()
    self.collapseAllBtn.hide()

    self.saveDir.mkdir(parents=True, exist_ok=True)

    self.curProcessor: Optional[GeneralProcWrapper] = None
    self.procWrapType = procWrapType
    self.nameToProcMapping: Dict[str, GeneralProcWrapper] = {}

    wrapped : Optional[GeneralProcWrapper] = None
    for processorCtor in procCtors:
      # Retrieve proc so default can be set after
      wrapped = self.addProcessor(processorCtor())
    self.algOpts.setDefault(wrapped)
    self.switchActiveProcessor(proc=wrapped)
    # self.saveParamState('Default', allowOverwriteDefault=True)

  def addProcessor(self, newProc: GeneralProcess):
    processor = self.procWrapType(newProc, parentParam=self.params)
    self.tree.addParameters(self.params.child(processor.algName))

    self.nameToProcMapping.update({processor.algName: processor})
    self.algOpts.setLimits(self.nameToProcMapping.copy())
    return processor

  def saveParamState(self, saveName: str=None, paramState: dict=None, **kwargs):
    """
    The algorithm editor also needs to store information about the selected algorithm, so lump
    this in with the other parameter information before calling default save.
    """
    if paramState is None:
      paramDict = self.paramDictWithOpts(addList=['enabled'], addTo=[ProcGroupParameter],
                                         removeList=['value'])
      paramState = {'Selected Algorithm': self.algOpts.value().algName,
                    'Parameters': paramDict}
    return super().saveParamState(saveName, paramState, **kwargs)

  def loadParamState(self, stateName: Union[str, Path], stateDict: dict=None,
                     addChildren=False, removeChildren=False, applyChanges=True):
    stateDict = self._parseStateDict(stateName, stateDict)
    selectedOpt = stateDict.get('Selected Algorithm', None)
    # Get the impl associated with this option name
    isLegitSelection = selectedOpt in self.algOpts.opts['limits']
    if not isLegitSelection:
      selectedImpl = self.algOpts.value()
      raise AlgProcessorError(f'Selection {selectedOpt} does'
                                f' not match the list of available algorithms. Defaulting to {selectedImpl}')
    else:
      selectedImpl = self.algOpts.opts['limits'][selectedOpt]
    self.algOpts.setValue(selectedImpl)
    super().loadParamState(stateName, stateDict['Parameters'])

  def switchActiveProcessor(self, proc: Union[str, GeneralProcWrapper]):
    """
    Changes which processor is active. if ImgProcWrapper, uses that as the processor.
    If str, looks for that name in current processors and uses that
    """
    if isinstance(proc, str):
      proc = self.nameToProcMapping[proc]
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