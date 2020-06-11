from __future__ import annotations

from abc import ABC
from functools import wraps
from typing import Tuple, List, Sequence
from warnings import warn

import numpy as np
from pyqtgraph.parametertree import Parameter

import s3a
from imageprocessing.processing import ImageIO, ProcessStage, AtomicProcess, Process, \
  ImageProcess, ProcessIO
from .frgraphics.parameditors import genericeditor
from .frgraphics.parameditors.pgregistered import FRCustomMenuParameter
from .processingimpls import crop_to_verts, update_area, basicOpsCombo, \
  return_to_full_size
from .structures import FRParam, FRComplexVertices, FRAlgProcessorError, FRVertices


def atomicRunWrapper(proc: AtomicProcess, names: List[str], params: List[Parameter]):
  oldRun = proc.run
  @wraps(oldRun)
  def newRun(io: ProcessIO = None, force=False, disable=False, verbose=False) -> ProcessIO:
    newIo = {name: param.value() for name, param in zip(names, params)}
    proc.updateParams(**newIo)
    return oldRun(io, force, disable, verbose)
  return newRun

def procRunWrapper(proc: Process, groupParam: Parameter):
  oldRun = proc.run
  @wraps(oldRun)
  def newRun(io: ProcessIO = None, force=False, disable=False, verbose=False):
    proc.disabled = not groupParam.opts['enabled']
    return oldRun(io, force=force, disable=disable, verbose=verbose)
  return newRun

class FRGeneralProcWrapper(ABC):
  def __init__(self, processor: ImageProcess, editor: s3a.frgraphics.parameditors.genericeditor.FRParamEditor):
    self.processor = processor
    self.algName = processor.name
    self.algParam = FRParam(self.algName)
    self.output = np.zeros((0,0), bool)

    self.editor = editor
    editor.registerGroup(self.algParam, nameFromParam=True, forceCreate=True)
    self.unpackStages(self.processor)

  def unpackStages(self, stage: ProcessStage, paramParent: Tuple[str,...]=()):
    if isinstance(stage, AtomicProcess):
      params: List[Parameter] = []
      for key, val in stage.input.hyperParams.items():
        curParam = FRParam(name=key, value=val)
        pgParam = self.editor.registerProp(self.algName, curParam, paramParent, asProperty=False)
        params.append(pgParam)
      stage.run = atomicRunWrapper(stage, stage.input.hyperParamKeys, params)
      return
    # else: # Process
    stage: Process
    curGroup = self.editor.params.child(self.algName, *paramParent)
    stage.run = procRunWrapper(stage, curGroup)
    # Special case of a process comprised of just one atomic function
    if len(stage.stages) == 1 and isinstance(stage.stages[0], AtomicProcess):
      self.unpackStages(stage.stages[0], paramParent=paramParent)
      return
    for childStage in stage.stages:
      valType = 'atomicgroup'
      if isinstance(childStage, Process) and childStage.allowDisable:
        valType = 'procgroup'
      curGroup = FRParam(name=childStage.name, valType=valType, value=[])
      self.editor.registerProp(self.algName, curGroup, paramParent, asProperty=False)
      self.unpackStages(childStage, paramParent=paramParent + (childStage.name,))

  def run(self, **kwargs):
    raise NotImplementedError

class FRImgProcWrapper(FRGeneralProcWrapper):
  def __init__(self, processor: ImageProcess, editor: s3a.frgraphics.parameditors.genericeditor.FRParamEditor):
    # Each processor is encapsulated in processes that crop the image to the region of
    # interest specified by the user, and re-expand the area after processing
    cropStage = ImageProcess.fromFunction(crop_to_verts, name='Crop to Vertices')
    cropStage.allowDisable = False
    updateStage = ImageProcess.fromFunction(update_area, 'Update Cropped Area')
    updateStage.allowDisable = False
    resizeStage = ImageProcess.fromFunction(return_to_full_size, 'Return to Full Size')
    resizeStage.allowDisable = False
    processor.stages = [cropStage] + processor.stages + [updateStage, basicOpsCombo(), resizeStage]
    super().__init__(processor, editor)

  def run(self, **kwargs):
    if kwargs.get('image', None) is None:
      raise FRAlgProcessorError('Cannot run processor without an image')
    image = kwargs['image']
    for name in 'fgVerts', 'bgVerts':
      if kwargs.get(name, None) is None:
        kwargs[name] = FRVertices()
    if kwargs.get('prevCompMask', None) is None:
      noPrevMask = True
      kwargs['prevCompMask'] = np.zeros(image.shape[:2], bool)
    else:
      noPrevMask = False
    newIo = ImageIO(**kwargs, noPrevMask=noPrevMask)

    try:
      result = self.processor.run(newIo, force=True)
    except Exception as ex:
      warn(f'Exception during processor run:\n{ex}')
      result = ImageIO(image=kwargs['prevCompMask'])

    outImg = result['image'].astype(bool)
    if outImg.ndim > 2:
      outImg = np.bitwise_or.reduce(outImg, 2)
    self.output = outImg
    return outImg

  def resultAsVerts(self, localEstimate=True):
    initialList = FRComplexVertices.fromBwMask(self.output)
    if len(initialList) == 0:
      return initialList
    if not localEstimate:
      return [FRComplexVertices([lst]) for lst in initialList]
    # else, all vertices belong to the same component
    else:
      return [initialList]

  def setStageEnabled(self, stageIdx: Sequence[str], enabled: bool):
    paramForStage: FRCustomMenuParameter = self.editor.params.child(self.algName, *stageIdx)
    prevEnabled = paramForStage.opts['enabled']
    if prevEnabled != enabled:
      paramForStage.menuActTriggered('Toggle Enable')