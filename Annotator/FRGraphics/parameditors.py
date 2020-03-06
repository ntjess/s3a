from __future__ import annotations
import pickle as pkl
import re
import sys
from dataclasses import dataclass
from functools import partial
from os.path import join
from pathlib import Path
from typing import Sequence, Union, Callable, Any, Optional, List, Dict, Tuple, Set

import numpy as np

from pyqtgraph.Qt import QtCore, QtWidgets, QtGui
from pyqtgraph.parametertree import (Parameter, ParameterTree, parameterTypes)

from Annotator.constants import MENU_OPTS_DIR
from Annotator.interfaces import FRImageProcessor
from .graphicsutils import dialogSaveToFile
from .. import appInst
from ..constants import (
  SCHEMES_DIR, GEN_PROPS_DIR, FILTERS_DIR, SHORTCUTS_DIR,
  TEMPLATE_COMP as TC, TEMPLATE_COMP_TYPES as COMP_TYPES, FR_CONSTS)
from ..exceptions import FRIllRegisteredPropError
from ..params import FRParam

Signal = QtCore.pyqtSignal

def _genList(nameIter, paramType, defaultVal, defaultParam='value'):
  """Helper for generating children elements"""
  return [{'name': name, 'type': paramType, defaultParam: defaultVal} for name in nameIter]


def _camelCaseToTitle(name: str) -> str:
  """
  Helper utility to turn a CamelCase name to a 'Title Case' title
  :param name: camel-cased name
  :return: Space-separated, properly capitalized version of :param:`Name`
  """
  if not name:
    return name
  else:
    name = re.sub(r'(\w)([A-Z])', r'\1 \2', name)
    return name.title()

def _class_fnNamesFromFnQualname(qualname: str) -> (str, str):
  """
  From the fully qualified function name (e.g. module.class.fn), return the function
  name and class name (module.class, fn).
  :param qualname: output of fn.__qualname__
  :return: (clsName, fnName)
  """
  lastDotIdx = qualname.find('.')
  fnName = qualname
  if lastDotIdx < 0:
    # This function isn't inside a class, so defer
    # to the global namespace
    fnParentClass = 'Global'
  else:
    # Get name of class containing this function
    fnParentClass = qualname[:lastDotIdx]
    fnName = qualname[lastDotIdx:]
  return fnParentClass, fnName


@dataclass
class FRShortcutCtorGroup:
  constParam: FRParam
  func: Callable
  args: list

class FREditableShortcut(QtWidgets.QShortcut):
  paramIdx: QtGui.QKeySequence

class ShortcutParameterItem(parameterTypes.WidgetParameterItem):
  """
  Class for creating custom shortcuts. Must be made here since pyqtgraph doesn't
  provide an implementation.
  """

  def __init__(self, param, depth):
    super().__init__(param, depth)
    self.item: Optional[QtGui.QKeySequence] = None

  def makeWidget(self):
    item = QtWidgets.QKeySequenceEdit()

    item.sigChanged = item.editingFinished
    item.value = item.keySequence
    item.setValue = item.setKeySequence
    self.item = item
    return self.item

  def updateDisplayLabel(self, value=None):
    # Make sure the key sequence is human readable
    self.displayLabel.setText(self.widget.keySequence().toString())

  # def contextMenuEvent(self, ev: QtGui.QContextMenuEvent):
  #   menu = self.contextMenu
  #   delAct = QtWidgets.QAction('Set Blank')
  #   delAct.triggered.connect(lambda: self.widget.setValue(''))
  #   menu.addAction(delAct)
  #   menu.exec(ev.globalPos())

class ShortcutParameter(Parameter):
  itemClass = ShortcutParameterItem

  def __init__(self, **opts):
    # Before initializing super, turn the string keystroke into a key sequence
    value = opts.get('value', '')
    keySeqVal = QtGui.QKeySequence(value)
    opts['value'] = keySeqVal
    super().__init__(**opts)


class NoneParameter(parameterTypes.SimpleParameter):

  def __init__(self, **opts):
    opts['type'] = 'str'
    super().__init__(**opts)
    self.setWritable(False)


parameterTypes.registerParameterType('none', NoneParameter)
parameterTypes.registerParameterType('shortcut', ShortcutParameter)

@dataclass
class FRBoundFnParams:
  param: FRParam
  func: Callable
  defaultFnArgs: list

class FRParamEditor(QtWidgets.QDialog):
  sigParamStateCreated = Signal(str)
  sigParamStateUpdated = Signal(dict)

  def __init__(self, parent=None, paramList: List[Dict]=None, saveDir='.',
               saveExt='param', saveDlgName='Save As', name=None):
    # Place in list so an empty value gets unpacked into super constructor
    if paramList is None:
      paramList = []

    if name is None:
      try:
        propClsName = type(self).__name__
        name = propClsName[:propClsName.index('Editor')]
        name = _camelCaseToTitle(name)
      except ValueError:
        name = "Parameter Editor"

    super().__init__(parent)
    self.setWindowTitle(name)
    self.resize(500, 400)

    self.boundFnsPerClass: Dict[str, List[FRBoundFnParams]] = {}
    """Holds the parameters associated with this registered class"""

    self.classNameToParamMapping: Dict[str, FRParam] = {}
    """
    Allows the editor to associate a class name with its human-readable parameter
    name
    """

    self.classInstToEditorMapping: Dict[Any, FRParamEditor] = {}
    """
    For editors that register parameters for *other* editors (like
    :class:`AlgPropsMgr`), this allows parameters to be updated from the
    correct editor
    """

    self.instantiatedClassTypes = set()
    """
    Records whether classes with registered parameters have been instantiated. This way,
    base classes with registered parameters but no instances will not appear in the
    parameter editor.
    """



    # -----------
    # Construct parameter tree
    # -----------
    self.params = Parameter(name='Parameters', type='group', children=paramList)
    self.tree = ParameterTree()
    self.tree.setParameters(self.params, showTop=False)

    # Allow the user to change column widths
    for colIdx in range(2):
      self.tree.header().setSectionResizeMode(colIdx, QtWidgets.QHeaderView.Interactive)

    # -----------
    # Human readable name (for settings menu)
    # -----------
    self.name = name

    # -----------
    # Internal parameters for saving settings
    # -----------
    self.saveDir = saveDir
    self.fileType = saveExt
    self._saveDlgName = saveDlgName
    self._stateBeforeEdit = self.params.saveState()

    # -----------
    # Additional widget buttons
    # -----------
    self.saveAsBtn = QtWidgets.QPushButton('Save As...')
    self.applyBtn = QtWidgets.QPushButton('Apply')
    self.closeBtn = QtWidgets.QPushButton('Close')

    # -----------
    # Widget layout
    # -----------
    btnLayout = QtWidgets.QHBoxLayout()
    btnLayout.addWidget(self.saveAsBtn)
    btnLayout.addWidget(self.applyBtn)
    btnLayout.addWidget(self.closeBtn)

    centralLayout = QtWidgets.QVBoxLayout()
    centralLayout.addWidget(self.tree)
    centralLayout.addLayout(btnLayout)
    self.setLayout(centralLayout)
    # -----------
    # UI Element Signals
    # -----------
    self.saveAsBtn.clicked.connect(self.saveAsBtnClicked)
    self.closeBtn.clicked.connect(self.close)
    self.applyBtn.clicked.connect(self.applyBtnClicked)

  # Helper method for accessing simple parameter values
  def __getitem__(self, keys: Union[tuple, FRParam, Sequence[FRParam]]):
    """
    Convenience function for accessing child parameters within a parameter editor.
      - If :param:`keys` is a single :class:`FRParam`, the value at that parameter is
        extracted and returned to the user.
      - If :param:`keys` is a :class:`tuple`:

        * The first element of the tuple must correspond to the base name within the
          parameter grouping in order to properly extract the corresponding children.
          For instance, to extract MARGIN from :class:`GeneralPropertiesEditor`,
              you must first specify the group parent for that parameter:
              >>> margin = FR_SINGLETON.generalProps[FR_CONSTS.CLS_FOCUSED_IMG_AREA,
              >>>   FR_CONSTS.MARGIN]
        * The second parameter must be a signle :class:`FRParam` objects or a sequence
          of :class:`FRParam` objects. If a sequence is given, a list of output values
          respecting input order is provided.
        * The third parameter is optional. If provided, the :class:`Parameter<pyqtgraph.Parameter>`
          object is returned instead of the :func:`value()<Parameter.value>` data
          *within* the object.

    :param keys: One of of the following:
    :return:
    """
    returnSingle = False
    extractObj = False
    if isinstance(keys, tuple):
      if len(keys) > 2:
        extractObj = True
      baseParam = [keys[0].name]
      keys = keys[1]
    else:
      baseParam = []
    if not hasattr(keys, '__iter__'):
      keys = [keys]
      returnSingle = True
    outVals = []
    extractFunc = lambda name: self.params.child(*baseParam, name)
    if not extractObj:
      oldExtractFunc = extractFunc
      extractFunc = lambda name: oldExtractFunc(name).value()
    for curKey in keys: # type: FRParam
      outVals.append(extractFunc(curKey.name))
    if returnSingle:
      return outVals[0]
    else:
      return outVals

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
    self.params.restoreState(self._stateBeforeEdit)
    super().reject()

  def applyBtnClicked(self):
    self._stateBeforeEdit = self.params.saveState()
    outDict = self.params.getValues()
    self.sigParamStateUpdated.emit(outDict)
    return outDict

  def saveAsBtnClicked(self, saveName=None):
    """
    :return: List where each index corresponds to the tree's parameter.
    This is suitable for extending with an ID and vertex list, after which
    it can be placed into the component table.
    """
    paramState = self.params.saveState()
    if saveName is False or saveName is None:
      saveName = dialogSaveToFile(self, paramState, self._saveDlgName,
                                  self.saveDir, self.fileType, allowOverwriteDefault=False)
    else:
      with open(saveName, 'wb') as saveFile:
        pkl.dump(paramState, saveFile)
    if saveName is not None:
      # Accept new param state after saving
      self.applyBtnClicked()
      outDict = self.params.getValues()
      self.sigParamStateCreated.emit(saveName)
      return outDict
    # If no name specified
    return None

  def loadState(self, newStateDict):
    self.params.restoreState(newStateDict, addChildren=False)

  def registerProp(self, constParam: FRParam):
    # First add registered property to self list
    def funcWrapper(func):
      func, clsName = self.registerMethod(constParam)(func, True)

      @property
      def paramAccessor(*args, **kwargs):
        # Use function wrapper instead of directly returning so no errors are thrown when class isn't fully instantiated
        # Retrieve class name from the class instance, since this function call may have resulted from an inhereted class
        trueCls = type(args[0]).__qualname__
        xpondingEditor = self.classInstToEditorMapping[args[0]]
        return xpondingEditor[self.classNameToParamMapping[trueCls], constParam]
      @paramAccessor.setter
      def paramAccessor(clsObj, newVal):
        xpondingEditor = self.classInstToEditorMapping[clsObj]
        param = xpondingEditor[self.classNameToParamMapping[clsName], constParam, True]
        param.setValue(newVal)
      return paramAccessor
    return funcWrapper

  def registerMethod(self, constParam: FRParam, fnArgs=None):
    """
    Designed for use as a function decorator. Registers the decorated function into a list
    of methods known to the :class:`ShortcutsEditor`. These functions are then accessable from
    customizeable shortcuts.
    """
    if fnArgs is None:
      fnArgs = []

    def registerMethodDecorator(func: Callable, returnClsName=False):
      boundFnParam = FRBoundFnParams(param=constParam, func=func, defaultFnArgs=fnArgs)
      fnParentClass, _ = _class_fnNamesFromFnQualname(func.__qualname__)

      self._addParamToList(fnParentClass, boundFnParam)
      if returnClsName:
        return func, fnParentClass
      else:
        return func
    return registerMethodDecorator

  def _addParamToList(self, clsName: str, param: Union[FRParam, FRBoundFnParams]):
    clsParams = self.boundFnsPerClass.get(clsName, [])
    clsParams.append(param)
    self.boundFnsPerClass[clsName] = clsParams

  def registerClass(self, clsParam: FRParam, **opts):
    """
    Intended for use as a class decorator. Registers a class as able to hold
    customizable shortcuts.
    """
    def classDecorator(cls):
      clsName = cls.__qualname__
      oldClsInit = cls.__init__
      self._extendedClassDecorator(cls, clsParam, **opts)
      def newClassInit(clsObj, *args, **kwargs):
        self.classInstToEditorMapping[clsObj] = self
        # Don't add class parameters again if two of the same class instances were added
        if cls not in self.instantiatedClassTypes:
          self.instantiatedClassTypes.add(cls)
          self.addParamsFromClass(cls, clsParam)
          # Now that class params are registered, save off default file
          if opts.get('saveDefault', True):
            Path(self.saveDir).mkdir(parents=True, exist_ok=True)
            with open(join(self.saveDir, f'Default.{self.fileType}'), 'wb') as ofile:
              pkl.dump(self.params.saveState(), ofile)
          self.classNameToParamMapping[clsName] = clsParam

        retVal = oldClsInit(clsObj, *args, **kwargs)
        self._extendedClassInit(clsObj, clsParam)
        return retVal
      cls.__init__ = newClassInit
      return cls
    return classDecorator

  def _extendedClassInit(self, clsObj: Any, clsParam: FRParam):
    """
    For editors that need to perform any initializations within the decorated class,
      they must be able to access the decorated class' *init* function and modify it.
      Allow this by providing an overloadable stub that is inserted into the decorated
      class *init*.
    """
    return

  def _extendedClassDecorator(self, cls: Any, clsParam: FRParam, **opts):
    """
    Editors needing additional class decorator boilerplates will place it in this overloaded function
    """

  def addParamsFromClass(self, cls: Any, clsParam: FRParam):
    """
    For a given class, adds the registered parameters from that class to the respective
    editor. This is how the dropdown menus in the editors are populated with the
    user-specified variables.

    :param cls: Current class

    :param clsParam: :class:`FRParam` value encapsulating the human readable class name.
           This is how the class will be displayed in the :class:`ShortcutsEditor`.

    :return: None
    """
    # Make sure to add parameters from registered base classes, too
    iterClasses = []
    baseClasses = [cls]
    nextClsPtr = 0
    # Get all bases of bases, too
    while nextClsPtr < len(baseClasses):
      curCls = baseClasses[nextClsPtr]
      curBases = curCls.__bases__
      # Only add base classes that haven't already been added to prevent infinite recursion
      baseClasses.extend([tmpCls for tmpCls in curBases if tmpCls not in baseClasses])
      nextClsPtr += 1

    baseClses = []
    for baseCls in baseClasses:
      iterClasses.append(baseCls.__qualname__)

    for clsName in iterClasses:
      classParamList = self.boundFnsPerClass.get(clsName, [])
      # Don't add a category unless at least one list element is present
      if len(classParamList) == 0: continue
      # If a human-readable name was given, replace class name with human name
      paramChildren = []
      paramGroup = {'name': clsParam.name, 'type': 'group',
                    'children': paramChildren}
      for boundFn in classParamList:
        paramForTree = {'name': boundFn.param.name,
                         'type': boundFn.param.valType,
                         'value': boundFn.param.value}
        paramChildren.append(paramForTree)
      # If this group already exists, append the children to the existing group
      # instead of adding a new child
      paramExists = False
      existingParamIdx = None
      for ii, param in enumerate(self.params.childs):
        if param.name() == clsParam.name:
          paramExists = True
          existingParamIdx = ii
          break
      if paramExists:
        self.params.childs[existingParamIdx].addChildren(paramChildren)
      else:
        self.params.addChild(paramGroup)
    # Make sure all new names are properly displayed
    self.tree.resizeColumnToContents(0)
    self._stateBeforeEdit = self.params.saveState()

class GeneralPropertiesEditor(FRParamEditor):
  def __init__(self, parent=None):
    super().__init__(parent, paramList=[], saveDir=GEN_PROPS_DIR, saveExt='regctrl')


class TableFilterEditor(FRParamEditor):
  def __init__(self, parent=None):
    minMaxParam = _genList(['min', 'max'], 'int', 0)
    # Make max 'infinity'
    minMaxParam[1]['value'] = sys.maxsize
    validatedParms = _genList(['Validated', 'Not Validated'], 'bool', True)
    devTypeParam = _genList((param.name for param in COMP_TYPES), 'bool', True)
    xyVerts = _genList(['X Bounds', 'Y Bounds'], 'group', minMaxParam, 'children')
    _FILTER_DICT = [
        {'name': TC.INST_ID.name, 'type': 'group', 'children': minMaxParam},
        {'name': TC.VALIDATED.name, 'type': 'group', 'children': validatedParms},
        {'name': TC.DEV_TYPE.name, 'type': 'group', 'children': devTypeParam},
        {'name': TC.LOGO.name, 'type': 'str', 'value': '.*'},
        {'name': TC.NOTES.name, 'type': 'str', 'value': '.*'},
        {'name': TC.BOARD_TEXT.name, 'type': 'str', 'value': '.*'},
        {'name': TC.DEV_TEXT.name, 'type': 'str', 'value': '.*'},
        {'name': TC.VERTICES.name, 'type': 'group', 'children': xyVerts}
      ]
    super().__init__(parent, paramList=_FILTER_DICT, saveDir=FILTERS_DIR, saveExt='filter')

class ShortcutsEditor(FRParamEditor):

  def __init__(self, parent=None):

    self.shortcuts = []
    # Unlike other param editors, these children don't get filled in until
    # after the top-level widget is passed to the shortcut editor
    super().__init__(parent, [], saveDir=SHORTCUTS_DIR, saveExt='shortcut')

  def _extendedClassInit(self, clsObj: Any, clsParam: FRParam):
    clsName = type(clsObj).__qualname__
    boundParamList = self.boundFnsPerClass.get(clsName, [])
    for boundParam in boundParamList:
      appInst.topLevelWidgets()
      seqCopy = QtGui.QKeySequence(boundParam.param.value)
      # If the registered class is not a graphical widget, the shortcut
      # needs a global context
      allWidgets = appInst.topLevelWidgets()
      isGlobalWidget = [isinstance(o, QtWidgets.QMainWindow) for o in allWidgets]
      mainWin = allWidgets[np.argmax(isGlobalWidget)]

      try:
        shortcut = FREditableShortcut(seqCopy, clsObj)
      except TypeError:
        shortcut = FREditableShortcut(seqCopy, mainWin)
      shortcut.paramIdx = (clsParam, boundParam.param)
      shortcut.activated.connect(partial(boundParam.func, clsObj, *boundParam.defaultFnArgs))
      shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
      self.shortcuts.append(shortcut)

  def registerProp(self, constParam: FRParam):
    """
    Properties should never be registered as shortcuts, so make sure this is disallowed
    """
    raise FRIllRegisteredPropError('Cannot register property/attribute as a shortcut')

  def applyBtnClicked(self):
    for shortcut in self.shortcuts: #type: FREditableShortcut
      shortcut.setKey(self[shortcut.paramIdx])
    super().applyBtnClicked()

class AlgCollectionEditor(FRParamEditor):
  def __init__(self, saveDir, algMgr: AlgPropsMgr, name=None, parent=None):
    self.algMgr = algMgr
    super().__init__(parent, saveDir=saveDir, saveExt='alg', name=name)
    algOptDict = {
      'name': 'Algorithm', 'type': 'list', 'values': [], 'value': 'N/A'
    }
    self.treeAlgOpts: Parameter = Parameter(name='Algorithm Selection', type='group', children=[algOptDict])
    self.algOpts = self.treeAlgOpts.children()[0]
    # Since constructor forces self.params to be top level item, we need to reconstruct
    # the tree to avoid this
    self.tree.setParameters(self.algOpts)
    self.algOpts.sigValueChanged.connect(self.changeActiveAlg)

    Path(self.saveDir).mkdir(parents=True, exist_ok=True)
    with open(join(self.saveDir, f'Default.{self.fileType}'), 'wb') as ofile:
      pkl.dump(self.params.saveState(), ofile)

    # Allows only the current processor params to be shown in the tree
    #self.tree.addParameters(self.params, showTop=False)

    self.curProcessor: Optional[FRImageProcessor] = None
    self.processors: Dict[str, Tuple[str, FRImageProcessor]] = {}
    self._image = np.zeros((1,1), dtype='uint8')


    self.build_attachParams(algMgr)

  @property
  def image(self):
    return self._image
  @image.setter
  def image(self, newImg: np.ndarray):
    self.curProcessor.image = newImg
    self._image = newImg

  def build_attachParams(self, algMgr: AlgPropsMgr):
    # Step 1: Instantiate all processor algorithms
    for processorCtor in algMgr.processorCtors:
      processor = processorCtor()
      # Step 2: For each instantiated process, hook up accessor functions to self's
      # parameter tree
      algMgr.classInstToEditorMapping[processor] = self

      clsName = type(processor).__qualname__
      procParam = algMgr.classNameToParamMapping[clsName]
      self.processors.update({procParam.name: (procParam.name, processor)})

    # Step 3: Construct parameter tree
    mgrParams = algMgr.params.saveState()
    self.params.clearChildren()
    self.params.addChildren(mgrParams['children'])
    for param in self.params.children():
      self.tree.addParameters(param)
    # Make sure all new names are properly displayed
    self.tree.resizeColumnToContents(0)

    self.algOpts.setLimits(self.processors)

  def changeActiveAlg(self, selectedParam: Parameter, nameProcCombo: Tuple[str, FRImageProcessor]):
    # Hide all except current selection
    # TODO: Find out why hide() isn't working. Documentation indicates it should
    # Instead, use the parentChanged utility as a hacky workaround
    curParamSet = self.params.child(nameProcCombo[0])
    for ii, child in enumerate(self.params.children()):
      shouldHide = child is not curParamSet
      # Offset by 1 to account for self.algOpts
      self.tree.setRowHidden(1 + ii, QtCore.QModelIndex(), shouldHide)
    # selectedParam.show()
    self.curProcessor = nameProcCombo[1]
    self.curProcessor.image = self.image

class AlgPropsMgr(FRParamEditor):

  def __init__(self, parent=None):
    super().__init__(parent, saveExt='', saveDir='')
    self.processorCtors : List[Callable[[Any,...], FRImageProcessor]] = []

  def registerClass(self, clsParam: FRParam, **opts):
    # Don't save a default file for this class
    return super().registerClass(clsParam, saveDefault=False, **opts)

  def _extendedClassDecorator(self, cls: Any, clsParam: FRParam, **opts):
    if opts.get('addToList', True):
      ctorArgs = opts.get('args', [])
      procCtor = partial(cls, *ctorArgs)
      self.processorCtors.append(procCtor)

  def createProcessorForClass(self, clsObj) -> AlgCollectionEditor:
    clsName = type(clsObj).__name__
    editorDir = join(MENU_OPTS_DIR, clsName, '')
    # Strip "FR" from class name before retrieving name
    settingsName = _camelCaseToTitle(clsName[2:]) + ' Processor'
    newEditor = AlgCollectionEditor(editorDir, self, name=settingsName)
    FR_SINGLETON.editors.append(newEditor)
    FR_SINGLETON.editorNames.append(newEditor.name)
    # Wrap in property so changes propagate to the calling class
    return newEditor


class SchemeEditor(FRParamEditor):
  def __init__(self, parent=None):
    super().__init__(parent, paramList=[], saveDir=SCHEMES_DIR, saveExt='scheme')

class _FRSingleton:
  algParamMgr = AlgPropsMgr()

  shortcuts = ShortcutsEditor()
  scheme = SchemeEditor()
  generalProps = GeneralPropertiesEditor()
  filter = TableFilterEditor()

  annotationAuthor = None

  def __init__(self):
    self.editors: List[FRParamEditor] =\
      [self.scheme, self.shortcuts, self.generalProps, self.filter]
    self.editorNames: List[str] = []
    for editor in self.editors:
        self.editorNames.append(editor.name)

  def registerClass(self, clsParam: FRParam):
    def multiEditorClsDecorator(cls):
      # Since all legwork is done inside the editors themselves, simply call each decorator from here as needed
      for editor in self.editors:
        cls = editor.registerClass(clsParam)(cls)
      return cls
    return multiEditorClsDecorator

  def close(self):
    for editor in self.editors:
      editor.close()
# Encapsulate scheme within class so that changes to the scheme propagate to all GUI elements
FR_SINGLETON = _FRSingleton()