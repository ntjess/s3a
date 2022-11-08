from __future__ import annotations

import importlib
import inspect
import pydoc
import types
import typing as t
import webbrowser
from collections import defaultdict
from pathlib import Path

from pyqtgraph.Qt import QtCore
from qtextras import (
    ParameterContainer,
    ParameterEditor,
    RunOptions,
    bindInteractorOptions as bind,
    fns,
)
from qtextras.typeoverloads import FilePath

from . import MetaTreeParameterEditor
from ..constants import PRJ_ENUMS
from ..processing.pipeline import (
    PipelineFunction,
    PipelineParameter,
    PipelineStageType,
    maybeGetFunction,
)

Signal = QtCore.Signal
_topDictType = t.Dict[str, t.Union[t.List[str], PipelineParameter]]
_primitiveDictType = t.Dict[
    str, t.Union[PipelineFunction, t.List[str], PipelineParameter]
]


class _CollectionDict(t.TypedDict):
    top: _topDictType
    primitive: _primitiveDictType
    modules: t.List[str]


def _peekFirst(iterable):
    return next(iter(iterable))


def _splitNameValueMetaDict(processDict: dict):
    """
    Split a dict of ``{process: {valueOpts}, metaOpts}`` into a tuple of
    ``(process, valueOpts, metaOpts)``
    """
    # 1. First key is always the process name, values are new inputs for any
    # matching process
    # 2. Second key is whether the process is disabled
    processDict = processDict.copy()
    processName = _peekFirst(processDict)
    updateKwargs = processDict.pop(processName, {})
    if isinstance(updateKwargs, list):
        # TODO: Determine policy for loading nested procs, outer should already
        #  know about inner so it shouldn't _need_ to occur, given outer would've
        #  been saved previously
        raise ValueError("Parsing deep nested processes is currently undefined")
    # TODO: Add recursion, if it's something that will be done. For now, assume
    #  only 1-depth nesting. Otherwise, it's hard to distinguish between actual
    #  input optiosn and a nested process
    return processName, updateKwargs, processDict


def onlyFirstKeyValue(dict_):
    key, value = _peekFirst(dict_.items())
    return {key: value}


class AlgorithmEditor(MetaTreeParameterEditor):
    sigProcessorChanged = QtCore.Signal(str)
    """Name of newly selected process"""

    DEFAULT_PROCESS_NAME = "<None>"

    def __init__(self, collection: "AlgorithmCollection" = None, **kwargs):
        super().__init__(**kwargs)
        if collection is None:
            collection = AlgorithmCollection()
        self.collection = collection

        # Will be set by changeActiveProcessor
        self.currentProcessor = PipelineParameter(name=self.DEFAULT_PROCESS_NAME)
        self.props = ParameterContainer()
        self.registerFunction(
            self.changeActiveProcessor,
            runOptions=RunOptions.ON_CHANGED,
            parent=self._metaParameter,
            process="",
            container=self.props,
        )
        fns.setParametersExpanded(self._metaTree)

        def onStateUpdated():
            self.props.parameters["process"].setLimits(
                list(self.collection.topProcesses)
            )

        def onChange(name):
            self.props["process"] = name

        self.collection.stateManager.signals.loadRequested.connect(onStateUpdated)
        self.sigProcessorChanged.connect(onChange)
        onStateUpdated()
        self.changeActiveProcessor(next(iter(self.collection.topProcesses)))

    def saveParameterValues(
        self,
        saveName: str = None,
        stateDict: dict = None,
        *,
        includeDefaults=False,
        **kwargs,
    ):
        """
        The algorithm editor also needs to store information about the selected
        process, so lump this in with the other parameter information before calling
        default save.
        """
        proc = self.currentProcessor
        # Make sure any newly added stages are accounted for
        if stateDict is None:
            # Since inner nested processes are already recorded, flatten here to just
            # save updated parameter values for the outermost stage
            stateDict = self.unnestedProcessState(proc, filter=("meta",))
        self.collection.loadParameterValues(self.collection.stateName, stateDict)
        clctnState = self.collection.saveParameterValues(saveName, blockWrite=True)
        stateDict = {"active": self.currentProcessor.title(), **clctnState}
        return super().saveParameterValues(
            saveName, stateDict, includeDefaults=includeDefaults, **kwargs
        )

    def loadParameterValues(
        self, stateName: FilePath = None, stateDict: dict = None, **kwargs
    ):
        stateDict = self.stateManager.loadState(stateName, stateDict)
        processName = stateDict.pop("active", None)

        self.collection.loadParameterValues(stateName, stateDict, **kwargs)
        if processName:
            self.changeActiveProcessor(processName, saveBeforeChange=False, force=True)
        # Parameter tree is managed by the collection, so don't load any candidates
        return super().loadParameterValues(
            stateName, stateDict, candidateParameters=[], **kwargs
        )

    @bind(
        process=dict(type="popuplineeditor", limits=[], title="Algorithm"),
        force=dict(ignore=True),
    )
    def changeActiveProcessor(
        self, process: str | PipelineParameter, saveBeforeChange=True, force=False
    ):
        """
        Changes which processor is active.

        Parameters
        ----------
        process
            Processor to load
        saveBeforeChange
            Whether to propagate current algorithm settings to the processor collection
            before changing
        force
            Whether to force the change, even if the processor is already active. This
            is useful if the processor is changed from a state being loaded rather than
            an updated algorithm name.
        """
        # TODO: Maybe there's a better way of doing this? Ensures process label is updated
        #  for programmatic calls
        title = process.title() if isinstance(process, PipelineParameter) else process
        needsChange = process and (force or title != self.currentProcessor.title())
        # Easier to understand "if not needsChange" vs. a double negative from direct
        # evaluation
        if not needsChange:
            return
        if (
            saveBeforeChange
            and self.currentProcessor.name() != self.DEFAULT_PROCESS_NAME
        ):
            self.saveParameterValues(self.stateName, blockWrite=True)

        self.rootParameter.clearChildren()
        process = self._resolveProccessor(process)
        self.currentProcessor = process
        self.rootParameter.addChild(process)
        fns.setParametersExpanded(self.tree)
        self.sigProcessorChanged.emit(process.title())

    def _resolveProccessor(self, processor):
        if isinstance(processor, str):
            processor = self.collection.parseProcessName(processor)
        if processor == self.currentProcessor:
            return None
        return processor

    def editParameterValuesGui(self):
        webbrowser.open(self.formatFileName())

    def unnestedProcessState(self, process: PipelineParameter, **kwargs):
        outState = dict(top={}, primitive={}, modules=self.collection.includeModules)

        # Make sure to visit the most deeply nested stages first, so that stages
        # can be accurately ignored if they are already included in a parent stage
        visit = [process]
        depths = defaultdict(int, {process: 0})
        while visit:
            pipe = visit.pop()
            for child in filter(lambda el: isinstance(el, PipelineParameter), pipe):
                depths[child] = max(depths[child], depths[pipe] + 1)
                visit.append(child)

        for pipe in sorted(depths, key=depths.get, reverse=True):
            # Don't record meta changes for top process since it breaks
            # logic for loading from a collection.
            # Do this by only keeping the first key (non-meta information)
            dest = "top" if pipe is process else "primitive"
            outState[dest].update(
                onlyFirstKeyValue(pipe.saveState(recurse=False, **kwargs))
            )
        return outState


class AlgorithmCollection(ParameterEditor):
    def __init__(
        self,
        processType=PipelineParameter,
        suffix=".alg",
        template: FilePath = None,
        **kwargs,
    ):
        super().__init__(suffix=suffix, **kwargs)
        self.processType = processType
        self.primitiveProcesses: _primitiveDictType = {}
        self.topProcesses: _topDictType = {}
        self.includeModules: list[str] = []

        if template is not None:
            self.loadParameterValues(template)

    def saveStagesByReference(
        self,
        process: PipelineParameter,
        **kwargs,
    ):
        """
        To prevent duplication of stages that are already present in ``primitive``,
        replace ``primitive`` stages with their names in the top-level state.
        """
        processState = process.saveState(**kwargs)
        procTitle, allChildStates = _peekFirst(processState.items())

        for child, childState in zip(process, allChildStates):
            if not isinstance(child, PipelineParameter):
                continue
            childTitle = _peekFirst(childState)
            if childTitle in self.primitiveProcesses:
                # Set to blank; the presence of the child name will indicate
                # fetch should be from `primitive` dict
                childState[childTitle] = {}

        # Simplify dict of {chname: {}} to just chname
        for ii, childState in enumerate(allChildStates):
            if len(childState) == 1:
                childState[ii] = _peekFirst(childState)
        return processState

    def addProcess(self, process: PipelineStageType, top=False, force=False):
        addDict = self.topProcesses if top else self.primitiveProcesses
        isFunction = isinstance(process, PipelineFunction)
        title = process.title()
        saveObj = {title: process}

        if force or title not in addDict or type(addDict[title]) != type(process):
            addDict.update(saveObj)
        if isFunction:
            return title

        for stage in process:
            # Don't recurse 'top', since it should only hold the directly passed process
            if function := maybeGetFunction(stage) or isinstance(
                stage, PipelineParameter
            ):
                self.addProcess(function or stage, top=False, force=force)
        return process.name()

    def addAllModuleProcesses(self, module: str | types.ModuleType, force=False):
        if isinstance(module, str):
            module = importlib.import_module(module)

        added = []
        for name, process in inspect.getmembers(module):
            if isinstance(process, PipelineStageType.__args__):
                added.append(self.addProcess(process, force=force))
                continue
            if (
                not hasattr(process, "__module__")
                or process.__module__ != module.__name__
            ):
                continue
            if inspect.isclass(process):
                try:
                    process = process()
                except TypeError:
                    # Needs arguments
                    continue
                added.append(self.addProcess(process, force=force))
            elif callable(process):
                added.append(self.addFunction(process, force=force))
        return added

    def addFunction(self, func: t.Callable, top=False, force=False, **kwargs):
        """
        Helper function to wrap a function in a pipeline process and add it as a
        stage
        """
        return self.addProcess(PipelineFunction(func, **kwargs), top, force)

    def parseProcessName(
        self,
        processName: str,
        topFirst=True,
        **kwargs,
    ):
        """
        From a list of search locations (ranging from most primitive to topmost),
        find the first processor matching the specified name. If 'topFirst' is chosen,
        the search locations are parsed in reverse order.
        """
        proc = self.fetchProcess(
            processName, topFirst=topFirst, **kwargs
        ) or self.parseProcessQualname(processName, **kwargs)

        if proc is None:
            raise ValueError(f"Process `{processName}` not recognized")
        if not isinstance(proc, PipelineStageType.__args__):
            raise ValueError(
                f"Parsed `{processName}`, but got non-pipelinable result: {proc}"
            )

        return proc

    def fetchProcess(self, processName: str, searchDicts=None, topFirst=True, **kwargs):
        if searchDicts is None:
            searchDicts = [self.topProcesses, self.primitiveProcesses]
        if topFirst:
            searchDicts = searchDicts[::-1]
        proc = searchDicts[0].get(processName, searchDicts[1].get(processName))
        if isinstance(proc, (type(None), *PipelineStageType.__args__)):
            return proc
        elif isinstance(proc, list):
            return self.pipelineFromStages(proc, name=processName, **kwargs)
        else:
            raise ValueError(f"Unknown process type: {type(proc)}")

    def pipelineFromStages(
        self,
        stages: t.Sequence[dict | str],
        name: str = None,
        add=PRJ_ENUMS.PROCESS_NO_ADD,
        allowOverwrite=False,
    ):
        """
        Creates a :class:`PipelineParameter` from a sequence of process stages and
        optional name

        Parameters
        ----------
        stages
            Stages to parse
        name
            Pipeline name, defaults to ``:function:fns.nameFormatter(<unnamed>)`
        add
            Whether to add this new pipeline to the current collection's top or
            primitive process blocks, or to not add at all (if ``NO_ADD``)
        allowOverwrite
            If `add` is *True*, this determines whether the new process can overwite an
            already existing proess. If ``add=False``, this value is ignored.
        """
        out = self.processType(name=name)
        for stageName in stages:
            valueOpts, metaOpts = {}, {}
            if isinstance(stageName, dict):
                stageName, valueOpts, metaOpts = _splitNameValueMetaDict(stageName)
            stage = self.parseProcessName(stageName, topFirst=False)
            out.addStage(stage, stageInputOptions=valueOpts, **metaOpts)

        exists = out.name in self.topProcesses
        if add is not PRJ_ENUMS.PROCESS_NO_ADD and (not exists or allowOverwrite):
            self.addProcess(
                out, top=add == PRJ_ENUMS.PROCESS_ADD_TOP, force=allowOverwrite
            )
        return out

    def parseProcessQualname(self, processName: str, **kwargs):

        # Special case: Qualname-loaded procs should be added under their qualname
        # otherwise they won't be rediscoverable after saving->restarting S3A
        for prefix in ["", *self.includeModules]:
            fullModuleName = ".".join([prefix, processName])
            proc: t.Any = pydoc.locate(fullModuleName)
            if proc is not None:
                break

        success = True
        if inspect.isclass(proc) and issubclass(proc, PipelineStageType.__args__):
            # False positive assuming only `object` return type
            # noinspection PyCallingNonCallable
            proc: PipelineStageType = proc(**kwargs)
        elif callable(proc) and not isinstance(proc, PipelineFunction):
            proc = PipelineFunction(proc, **kwargs)
        else:
            success = False
        if success:
            if isinstance(proc, PipelineFunction):
                proc.__name__ = processName
            else:
                proc.setOpts(name=processName)
            return proc
        # else
        return None

    def saveParameterValues(
        self, saveName: str = None, stateDict: dict = None, **kwargs
    ):
        def converter(procDict):
            return {
                name: self.saveStagesByReference(stage)[name]
                if isinstance(stage, PipelineParameter)
                else stage
                for name, stage in procDict.items()
                if not isinstance(stage, PipelineFunction)
            }

        if stateDict is None:
            stateDict = {
                "top": converter(self.topProcesses),
                "primitive": converter(self.primitiveProcesses),
                "modules": self.includeModules,
            }
        return super().saveParameterValues(saveName, stateDict, **kwargs)

    def loadParameterValues(
        self,
        stateName: t.Union[str, Path] = None,
        stateDict: _CollectionDict = None,
        **kwargs,
    ):
        stateDict = self.stateManager.loadState(stateName, stateDict)
        top, primitive = stateDict.get("top", {}), stateDict.get("primitive", {})
        modules = stateDict.get("modules", [])
        self.includeModules = modules
        self.topProcesses.update(top)
        self.primitiveProcesses.update(primitive)
        stateDict = self.saveParameterValues(blockWrite=True)
        super().loadParameterValues(
            stateName, stateDict, candidateParameters=[], **kwargs
        )
        return stateDict
