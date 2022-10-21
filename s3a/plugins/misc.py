from __future__ import annotations

import sys
from typing import Callable, Sequence

from pyqtgraph import console as pg_console
from pyqtgraph.Qt import QtCore
from utilitys import ParamEditorPlugin, widgets as uw

from ..constants import PRJ_CONSTS as CNST, REQD_TBL_FIELDS as RTF


class RandomToolsPlugin(ParamEditorPlugin):
    name = "Tools"
    _showFuncDetails = True

    _deferredRegisters: dict[Callable, dict] = {}
    """
    Keeps track of requested functions to register (the key) and the arguments to pass 
    during registration (kwargs are the value)
    """

    def attachWinRef(self, win):
        super().attachWinRef(win)

        self.registerFunc(self.showDevConsoleGui, name="Show Dev Console")
        self.registerFunc(win.clearBoundaries, btnOpts=CNST.TOOL_CLEAR_BOUNDARIES)
        self.registerFunc(
            win.componentController.exportComponentOverlay,
            name="Export Component Overlay",
            toClipboard=True,
        )
        self.registerFunc(lambda: win.setMainImage(None), name="Clear Current Image")

        self._hookupFieldDisplay(win)

        for deferred, kwargs in self._deferredRegisters.items():
            self.registerFunc(deferred, **kwargs)

    def _hookupFieldDisplay(self, window):
        display = window.componentController
        # This option shouldn't show in the menu dropdown, so register directly to the
        # tools
        _, param = self.toolsEditor.registerFunc(
            display.fieldInfoProc,
            name="Show Field Info",
            returnParam=True,
        )

        # There should also be an option that *does* show in the menu, which displays
        # field info for every component
        def toggleAll():
            if display.fieldDisplay.inUseDelegates:
                display.fieldDisplay.callDelegateFunction("clear")
            else:
                display.fieldInfoProc(
                    ids=window.componentManager.compDf.index, force=True
                )

        self.registerFunc(toggleAll, name="Toggle All Field Info")

        fieldsParam = param.child("fields")

        def updateLims():
            fieldsParam.setLimits(
                [
                    str(f)
                    for f in window.sharedAttrs.tableData.allFields
                    if f not in display.fieldDisplay.ignoreColumns
                ]
            )
            fieldsParam.setValue(fieldsParam.opts["limits"])

        window.sharedAttrs.tableData.sigConfigUpdated.connect(updateLims)
        updateLims()

    def showDevConsoleGui(self):
        """
        Opens a console that allows dynamic interaction with current variables. If
        IPython is on your system, a qt console will be loaded. Otherwise, a (less
        capable) standard pyqtgraph console will be used.
        """
        namespace = dict(app=self.win, rtf=RTF)
        # "dict" default is to use repr instead of string for internal elements,
        # so expanding into string here ensures repr is not used
        nsPrintout = [f"{k}: {v}" for k, v in namespace.items()]
        text = f"Starting console with variables:\n" f"{nsPrintout}"
        # Broad exception is fine, fallback is good enough. Too many edge cases to
        # properly diagnose when Pycharm's event loop is sync-able with the Jupyter dev
        # console noinspection PyBroadException
        try:
            # See https://intellij-support.jetbrains.com/hc/en-us/community/posts/205819799/comments/206004059  # noqa
            # for detecting whether this is run in debug
            # mode. PyCharm among other IDEs crash trying to spawn a jupyter console
            # without a stack trace, so attempt to catch this situation early
            if sys.gettrace() is None:
                console = uw.ConsoleWidget(
                    parent=self.win, namespace=namespace, text=text
                )
            else:
                # Raising an error goes into the except clause
                raise RuntimeError(
                    "Cannot spawn Jupyter console in a debug environment"
                )
        except Exception:
            # Ipy kernel can have issues for many reasons. Always be ready to fall back
            # to traditional console
            console = pg_console.ConsoleWidget(
                parent=self.win, namespace=namespace, text=text
            )
        console.setWindowFlags(QtCore.Qt.WindowType.Window)
        console.show()

    @classmethod
    def deferredRegisterFunction(cls, func: Callable, **registerKwargs):
        cls._deferredRegisters[func] = registerKwargs


def miscFunctionsPluginFactory(
    name_: str = None,
    functions: Sequence[Callable] = None,
    titles: Sequence[str] = None,
    showFunctionDetails=False,
):
    class FuncContainerPlugin(ParamEditorPlugin):
        name = name_
        _showFuncDetails = showFunctionDetails

        def attachWinRef(self, win: s3abase.S3ABase):
            super().attachWinRef(win)

            nonlocal functions, titles
            if functions is None:
                functions = []
            if titles is None:
                titles = [None] * len(functions)
            for func, title in zip(functions, titles):
                self.registerFunc(func, title)

    return FuncContainerPlugin
