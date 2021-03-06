import typing as t
import warnings
from collections import defaultdict

import numpy as np
import pandas as pd
from pandas.core.dtypes.missing import array_equivalent
from utilitys import PrjParam

from ..constants import REQD_TBL_FIELDS as RTF

_serFunc = t.Callable
_deserFunc = t.Callable
_valsType: t.Sequence[t.Any]


def _newHandlerTemplate():
    return {"serialize": None, "deserialize": None, "takesParam": False}


_serdesHandlers = pd.DataFrame(columns=list(_newHandlerTemplate()))


def registerIoHandler(pType: str, force=False, **kwargs):
    global _serdesHandlers
    if pType not in _serdesHandlers.index:
        _serdesHandlers.loc[pType] = _newHandlerTemplate()
    for which in {"serialize", "deserialize"} & set(kwargs):
        if _serdesHandlers.loc[pType, which] is not None and not force:
            raise KeyError(f'Already have {which} handler for "{pType}"')
    _serdesHandlers.loc[pType, list(kwargs)] = list(kwargs.values())


def _runFunc(param: PrjParam, values, which: str, default: t.Callable, returnErrs=True):
    # Using a dictionary instead of list of (value, key) pairs would clash on duplicate keys
    out = []
    errs = []
    parseType = param.opts.get("parser", param.pType)
    if parseType not in _serdesHandlers.index:
        handlerRow = _newHandlerTemplate()
    else:
        handlerRow = _serdesHandlers.loc[parseType]
    _handler = handlerRow[which] or default
    takesParam = handlerRow["takesParam"]
    # Retain initial index if series-like is passed
    if isinstance(values, pd.Series):
        enumerator = values.iteritems()
    else:
        enumerator = enumerate(values)
    for ii, val in enumerator:
        try:
            valIndexPair = (_handler(param, val) if takesParam else _handler(val), ii)
            out.append(valIndexPair)
        except Exception as ex:
            errs.append((ex, ii))
    out = pd.Series(*zip(*out), name=param, dtype=object)
    if returnErrs:
        return out, pd.Series(*zip(*errs), name=param, dtype=object)
    return out


def serialize(param: PrjParam, values: t.Sequence[t.Any], returnErrs=True):
    default = lambda *args: str(args[-1])
    hasValues = len(values) > 0
    # Series objects will use loc-based indexing, so use an iterator to guarantee first
    # access regardless of sequence type
    alreadyStr = hasValues and isinstance(next(iter(values)), str)
    defaultRet = [pd.Series(name=param, dtype=str) for _ in range(2)]
    if hasValues and not alreadyStr:
        return _runFunc(param, values, "serialize", default, returnErrs)
    elif alreadyStr:
        # TODO: handle heterogeneous arrays?
        # Calling 'serialize' on already serialized data is a no-op
        defaultRet[0] = pd.Series(values, name=param)
    # If no values, return type is already set and no need to do anything more
    if returnErrs:
        return defaultRet
    # Just values
    return defaultRet[0]


def deserialize(param: PrjParam, values: t.Sequence[str], returnErrs=True):
    # Calling 'deserialize' on a stringified data is a no-op
    # TODO: heterogeneous arrays?
    # Unlike serialize, dtype could be different depending on 'param', so leave empty creation to the handler
    # Series objects will use loc-based indexing, so use an iterator to guarantee first access regardless of sequence
    # type
    if len(values) and not isinstance(next(iter(values)), str):
        return pd.Series(values, name=param)
    paramType = type(param.value)
    # Also account for when takesParam=True, where val will be the last option
    default = lambda *args: paramType(args[-1])
    return _runFunc(param, values, "deserialize", default, returnErrs)


def checkVertBounds(vertSer: pd.Series, imageShape: tuple):
    """
    Checks whether any vertices in the imported dataframe extend past image dimensions. This is an indicator
    they came from the wrong import file.

    :param vertSer: Vertices from incoming component dataframe
    :param imageShape: Shape of the main image these vertices are drawn on
    :return: Raises error if offending vertices are present, since this is an indication the component file
      was from a different image
    """
    if imageShape is None or len(vertSer) == 0:
        # Nothing we can do if no shape is given
        return
    # Image shape from row-col -> x-y
    imageShape = np.array(imageShape[1::-1])[None, :]
    # Remove components whose vertices go over any image edges
    vertMaxs = [verts.stack().max(0) for verts in vertSer if len(verts) > 0]
    vertMaxs = np.vstack(vertMaxs)
    offendingIds = np.nonzero(np.any(vertMaxs > imageShape, axis=1))[0]
    if len(offendingIds) > 0:
        warnings.warn(
            f"Vertices on some components extend beyond image dimensions. "
            f"Perhaps this export came from a different image?\n"
            f"Offending IDs: {offendingIds}",
            UserWarning,
        )


def compareDataframes(compDf, loadedDf):
    matchingCols = np.setdiff1d(compDf.columns, [RTF.INST_ID, RTF.IMG_FILE])
    # For some reason, there are cases in which all values truly are equal but np.array_equal,
    # x.equals(y), x.eq(y), etc. all fail. Something to do with block ordering?
    # https://github.com/pandas-dev/pandas/issues/9330 indicates it should be fixed, but the error still occasionally
    # happens for me. array_equivalent is not affected by this, in testing so far
    dfCmp = array_equivalent(loadedDf[matchingCols].values, compDf[matchingCols].values)
    problemCells = defaultdict(list)

    if not dfCmp:
        dfA = loadedDf[matchingCols]
        dfB = compDf[matchingCols]
        for ii in range(len(dfA)):
            for jj in range(len(dfA.columns)):
                if not np.array_equal(dfA.iat[ii, jj], dfB.iat[ii, jj]):
                    problemCells[compDf.at[dfB.index[ii], RTF.INST_ID]].append(
                        str(matchingCols[jj])
                    )
        # The only way to prevent "truth value of array is ambiguous" is cell-by-cell iteration
        problemMsg = [f"{idx}: {cols}" for idx, cols in problemCells.items()]
        problemMsg = "\n".join(problemMsg)
        # Try to fix the problem with an iloc write
        warnings.warn(
            "<b>Warning!</b> Saved components do not match current component"
            " state. This can occur when pandas incorrectly caches some"
            " table values. Problem cells (shown as [id]: [columns]):\n"
            f"{problemMsg}\n"
            f"Please try manually altering these values before exporting again.",
            UserWarning,
        )
