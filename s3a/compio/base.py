from __future__ import annotations

import inspect
import typing as t
from pathlib import Path

import numpy as np
import pandas as pd
from qtextras import FROM_PREV_IO, OptionsDict, ParameterEditor, fns
from qtextras.typeoverloads import FilePath

from .helpers import checkVerticesBounds, deserialize, serialize
from ..constants import REQD_TBL_FIELDS as RTF
from ..generalutils import deprecateKwargs, toDictGen
from ..shims import typing_extensions
from ..structures import AnnInstanceError, AnnParseError
from ..tabledata import IOTemplateManager, TableData, getFieldAliases


class TableContainer:
    """
    Dummy component io in case a raw tableData is directly given to an importer/exporter
    """

    def __init__(self, tableData=None):
        self.tableData = tableData


class TblContainer_T(typing_extensions.Protocol):
    tableData: TableData


class _GenericExportProtocol(typing_extensions.Protocol):
    def __call__(
        self, componentDf: pd.DataFrame, exportObject, **kwargs
    ) -> (t.Any, pd.DataFrame):
        return exportObject, NO_ERRORS.copy()


class _updateExportObjectProtocol(typing_extensions.Protocol):
    def __call__(self, inst: dict, exportObject, **kwargs) -> t.Any:
        return exportObject


# Alias just for better readability
NO_ERRORS = pd.DataFrame()

_exportCallable = t.Callable[[pd.DataFrame, t.Any], t.Tuple[t.Any, pd.DataFrame]]


class AnnotationIOBase:
    class UNSET_IO_TYPE:
        pass

    __name__: t.Optional[str] = None
    ioType: t.Optional[str] = None
    """
    Type indicating what required fields from the IOTemplateManager should be applied
    """

    def __init__(self, ioType=UNSET_IO_TYPE, options=None):
        """
        Provides access to a modularized version of the common import structure:
          * read a file
          * parse bulk columns, where applicable (one to one column mapping)
          * parse individual instances, where applicable (one to many or many to
            one column mapping)
          * apply formatting

        This is all viewable under the ``__call__`` function.

        Parameters
        ----------
        ioType
            Determines which config template's required fields are necessary for this
            input. That way, required fields don't have to be explicitly enumerated in
            a project's table configuration
        options
            Dict-like metadata for this importer/exporter. If *None*, defaults to empty
            option set. This will be updated with kwargs from being called.
        """
        # Compatibility with function analysis done in ComponentIO
        clsName = type(self).__name__
        prefix = "import" if "Importer" in clsName else "export"
        fmtName = type(self).__name__.replace("Importer", "").replace("Exporter", "")
        self.__name__ = self.__name__ or f"{prefix}{fmtName}"

        useType = ioType
        if useType is self.UNSET_IO_TYPE:
            useType = self.ioType or fmtName.lower()
        self.ioType = useType

        if options is None:
            options = {}
        self.options = options

    def populateMetadata(self, **kwargs):
        return self._forwardMetadata(**kwargs)

    @classmethod
    def optionsMetadata(cls):
        """
        Get all metadata descriptions from self and any base class ``populateMetadata``.
        """
        metadata = {}
        classes = [
            curcls
            for curcls in inspect.getmro(cls)
            if issubclass(curcls, AnnotationIOBase)
        ]
        # Reverse so most current class is last to override options
        for subcls in reversed(classes):
            parsed = ParameterEditor.defaultInteractor.functionToParameterDict(
                subcls.populateMetadata, title=fns.nameFormatter
            )
            curMeta = {
                ch["name"]: ch
                for ch in parsed["children"]
                if not ch.get("ignore", False)
                and ch.get("value") is not FROM_PREV_IO
                and not ch["name"].startswith("_")
            }
            metadata.update(curMeta)
        return metadata

    def _forwardMetadata(self, locals_=None, **kwargs):
        """
        Convenience function to update __call__ kwargs from some locals and extra
        keywords, since this is a common paradigm in `populateMetadata`
        """
        if locals_ is None:
            locals_ = {}
        keySource = {**locals_, **kwargs}

        useKeys = set(kwargs).union(self.optionsMetadata())
        # Can only populate requested keys if they exist in the keysource
        return {kk: keySource[kk] for kk in useKeys.intersection(keySource)}


class AnnotationExporter(AnnotationIOBase):
    exportObject: t.Any
    componentDf: t.Optional[pd.DataFrame] = None

    bulkExport: _GenericExportProtocol | None = None
    """
    Can be defined if bulk-exporting (whole dataframe at once) is possible. Must
    accept inputs (component dataframe, export object, **kwargs) and output
    tuple[export object, error dataframe]. If no errors, error dataframe should
    be present but empty.
    """

    updateExportObject: _updateExportObjectProtocol | None = None
    """
    Can be defined if individual importing (row-by-row) is possible. This is fed
    the current dataframe row as a dict of cell values and is expected to output the 
    updated export object (which will be passed to writeFile). Must accept inputs
    (instance dict, export object, **kwargs) and output the export object.
    """

    class ERROR_COL:
        pass

    """Sentinel class to add errors to an explanatory message during export"""

    def writeFile(self, file: FilePath, exportObject, **kwargs):
        raise NotImplementedError

    def createExportObject(self, **kwargs):
        raise NotImplementedError

    def individualExport(self, componentDf: pd.DataFrame, exportObject, **kwargs):
        """
        Returns an export object + dataframe of row + errors, if any occurred for some
        rows
        """
        if self.updateExportObject is None:
            # Can't do anything, don't modify the object and save time not iterating
            # over rows
            return exportObject, NO_ERRORS.copy()
        errs = []
        for row in toDictGen(componentDf):
            try:
                exportObject = self.updateExportObject(row, exportObject, **kwargs)
            except Exception as err:
                row[self.ERROR_COL] = err
                errs.append(row)
        return exportObject, pd.DataFrame(errs)

    def formatReturnObject(self, exportObject, **kwargs):
        # If metadata options change return behavior, that can be resolved here.
        return exportObject

    def __call__(
        self,
        componentDf: pd.DataFrame,
        file: FilePath = None,
        errorOk=False,
        **kwargs,
    ):
        file = Path(file) if isinstance(file, FilePath.__args__) else None
        self.componentDf = componentDf

        kwargs.update(file=file)
        activeOpts = {**self.options, **kwargs}
        meta = self.populateMetadata(**activeOpts)
        kwargs.update(**meta)

        exportObject = self.createExportObject(**kwargs)
        for func in (
            self.bulkExport,
            self.individualExport,
        ):  # type: _GenericExportProtocol
            if func is None:
                continue
            exportObject, errs = func(componentDf, exportObject, **kwargs)
            if len(errs) and not errorOk:
                raise ValueError(
                    "Encountered problems exporting the following annotations:\n"
                    + errs.to_string()
                )
        self.exportObject = exportObject
        if file is not None:
            self.writeFile(kwargs.pop("file"), exportObject, **kwargs)
        toReturn = self.formatReturnObject(exportObject, **kwargs)
        self.componentDf = None
        return toReturn


class AnnotationImporter(AnnotationIOBase):
    importObj: t.Any

    formatSingleInstance = None
    """
    Can be defined to cause row-by-row instance parsing. If defined, must accept
    inputs (instance dict, **kwargs) and output a dict of instance values.
    """

    bulkImport = None
    """
    Can be defined to parse multiple traits from the imported object into a component 
    dataframe all at once. Must accept inputs (import object, **kwargs) and output a
    dataframe of instance values. Note that in some cases, a direct conversion of 
    instances to a dataframe is convenient, so ``defaultBulkImport`` is provided for 
    these cases. Simply set bulkImport = ``AnnotationImporter.defaultBulkImport`` if 
    you wish.
    """

    def __init__(
        self,
        tableData: TableData | TblContainer_T = None,
        ioType=AnnotationIOBase.UNSET_IO_TYPE,
    ):
        """
        Provides access to a modularized version of the common import structure:

          * read a file
          * parse bulk columns, where applicable (one to one column mapping)
          * parse individual instances, where applicable (one to many or many to one
            column mapping)
          * apply formatting

        This is all viewable under the `__call__` function.

        Parameters
        ----------
        tableData
            Table configuration for fields in the input file. If a container,
            ``container.tableData`` leads to the table data. This allows references to
            be reassigned in e.g. an outer ComponentIO without losing connection to
            this importer

        ioType
            Determines which config template's required fields are necessary for this
            input. That way, required fields don't have to be explicitly enumerated in
            a project's table configuration
        """

        # Make a copy to allow for internal changes such as adding extra required
        # fields, aliasing, etc. 'and' avoids asking for 'config' of 'none' table
        super().__init__(ioType=ioType)
        if tableData is None:
            tableData = TableData()
        if isinstance(tableData, TableData):
            container = TableContainer(tableData)
        else:
            container = tableData
        self.container = container
        self.tableData = TableData()
        self.destinationTable = self.container.tableData
        self.refreshTableData()

    def refreshTableData(self):
        self.destinationTable = tableData = self.container.tableData
        requiredCfg = IOTemplateManager.getTableConfig(self.ioType)
        if tableData is not None:
            # Make sure not to incorporate fields that only exist to provide logistics
            # for the other table setup
            optionalFields = {
                key: val
                for key, val in tableData.config["fields"].items()
                if key not in tableData.template["fields"]
            }
            optionalCfg = {"fields": optionalFields}
        else:
            optionalCfg = None
        self.tableData.template = requiredCfg
        self.tableData.loadConfig(configDict=optionalCfg)

    def readFile(self, file: FilePath, **kwargs):
        raise NotImplementedError

    def getInstances(self, importObj, **kwargs):
        raise NotImplementedError

    @staticmethod
    def _findSourceFieldForDestination(destField, allSourceFields):
        """
        Helper function during ``finalizeImport`` to find a match between a
        yet-to-serialize dataframe and destination tableData. Basically,
        a more primitive version of ``resolveFieldAliases`` Returns *None* if no
        sensible mapping could be found, and errs if multiple sources alias to the same
        destination
        """
        # Check for destination aliases primitively (one-way mappings). A full (
        # two-way) check will occur later (see __call__ -> resolveFieldAliases)
        match = tuple(getFieldAliases(destField) & allSourceFields)
        if not match:
            return
        if len(match) == 1:
            srcField = match[0]
        else:
            # Make sure there aren't multiple aliases, since this is not easily
            # resolvable The only exception is that direct matches trump alias matches,
            # so check for this directly
            if destField.name in match:
                srcField = destField.name
            else:
                raise IndexError(
                    f'Multiple aliases to "{destField}": {match}\n'
                    f"Cannot determine appropriate column matchup."
                )
        return srcField

    def finalizeImport(self, componentDf, **kwargs):
        """Deserializes any columns that are still strings"""

        # Objects in the original frame may be represented as strings, so try to
        # convert these as needed
        outDf = pd.DataFrame()
        # Preserve / transcribe fields that are already OptionsDicts
        for destField in [f for f in componentDf.columns if isinstance(f, OptionsDict)]:
            outDf[destField] = componentDf[destField]

        # Need to serialize / convert string names since they indicate yet-to-serialize
        # columns
        toConvert = set(componentDf.columns)
        for destField in self.tableData.allFields:
            srcField = self._findSourceFieldForDestination(destField, toConvert)
            if not srcField:
                # No match
                continue
            dfVals = componentDf[srcField]
            # Parsing functions only know how to convert from strings to themselves.
            # So, assume the exting types can first convert themselves to strings
            serializedDfVals, errs = serialize(destField, dfVals)
            parsedDfVals, parsedErrs = deserialize(destField, serializedDfVals)
            # Turn problematic cells into instance errors for detecting problems in the
            # outer scope
            errs = errs.apply(AnnInstanceError)
            parsedErrs = parsedErrs.apply(AnnInstanceError)
            parsedDfVals = pd.concat([parsedDfVals, errs, parsedErrs])
            outDf[destField] = parsedDfVals
        # All recognized output fields should now be deserialied; make sure required
        # fields exist
        return outDf

    @deprecateKwargs(keepExtraColumns="keepExtraFields", warningType=FutureWarning)
    def __call__(
        self,
        inputFileOrObject: t.Union[FilePath, t.Any],
        *,
        parseErrorOk=False,
        reindex=False,
        keepExtraFields=False,
        addMissingFields=False,
        **kwargs,
    ):
        self.refreshTableData()

        file = (
            Path(inputFileOrObject)
            if isinstance(inputFileOrObject, FilePath.__args__)
            else None
        )
        if file is not None:
            inputFileOrObject = self.readFile(inputFileOrObject, **kwargs)
        self.importObj = inputFileOrObject

        kwargs.update(file=file, reindex=reindex)
        activeOpts = {**self.options, **kwargs}
        meta = self.populateMetadata(**activeOpts)
        kwargs.update(meta)

        parsedDfs = []
        for func in (
            self.individualImport,
            self.bulkImport,
        ):  # type: t.Callable[[t.Any, ...], pd.DataFrame]
            # Default to empty dataframes for unspecified importers
            if func is None:
                func = lambda *_args, **_kw: pd.DataFrame()
            parsedDfs.append(func(inputFileOrObject, **kwargs))

        indivParsedDf, bulkParsedDf = parsedDfs
        # Overwrite bulk-parsed information with individual if needed, or add to it
        bulkParsedDf[indivParsedDf.columns] = indivParsedDf
        # Some cols could be deserialized, others could be serialized still. Handle the
        # still serialized cases
        parsedDf = self.finalizeImport(bulkParsedDf, **kwargs)

        # Determine any destination mappings
        importedCols = parsedDf.columns.copy()
        if self.destinationTable:
            parsedDf.columns = self.destinationTable.resolveFieldAliases(
                parsedDf.columns, kwargs.get("mapping", {})
            )

        if keepExtraFields:
            # Columns not specified in the table data should be kept in their
            # unmodified state
            extraCols = bulkParsedDf.columns.difference(importedCols)
            alreadyParsed = np.isin(bulkParsedDf.columns, importedCols)
            # Make sure column ordering matches original
            newOrder = np.array(bulkParsedDf.columns)
            newOrder[alreadyParsed] = parsedDf.columns

            parsedDf[extraCols] = bulkParsedDf[extraCols]
            parsedDf = parsedDf[newOrder]

        if addMissingFields:
            # False positive SettingWithCopyWarning occurs if missing fields were added
            # and the df was reordered, but copy() is not a performance bottleneck
            # and at least grants the new `parsedDf` explicit ownership of its data
            parsedDf = parsedDf.copy()

            # Desintation fields that never showed up should be appended
            for field in self.destinationTable.allFields:
                # Special case: instance id is handled below
                if field not in parsedDf and field != RTF.ID:
                    parsedDf[field] = field.value

        # Make sure IDs are present
        parsedDf = self._ensureIdsAsIndex(parsedDf, reindex=reindex)

        # Now that all column names and settings are resolve, handle any bad imports
        validDf = self.validInstances(parsedDf, parseErrorOk)
        # Ensure reindexing still takes place if requested
        if reindex and len(validDf) != len(parsedDf):
            validDf[RTF.ID] = validDf.index = np.arange(len(validDf))

        # Ensure vertices present, optionally check against known image shape
        if "imageShape" in kwargs and RTF.VERTICES in validDf:
            checkVerticesBounds(validDf[RTF.VERTICES], kwargs.get("imageShape"))
        return validDf

    @staticmethod
    def _ensureIdsAsIndex(df, reindex=None):
        alreadyExists = RTF.ID in df
        if reindex or not alreadyExists:
            sequentialIds = np.arange(len(df), dtype=int)
            if alreadyExists:  # Just reindexing
                df[RTF.ID] = sequentialIds
            # Ensure instance ID is the first column if new
            else:
                df.insert(0, RTF.ID, sequentialIds)
        elif not pd.api.types.is_integer_dtype(df[RTF.ID]):
            # pandas 1.4 introduced FutureWarnings for object-dtype assignments so ensure
            # Instance ID is integer type
            df[RTF.ID] = df[RTF.ID].astype(int)
        return df.set_index(RTF.ID, drop=False)

    @classmethod
    def validInstances(cls, parsedDf: pd.DataFrame, parseErrorOk=False):
        errIdxs = parsedDf.apply(
            lambda row: any(isinstance(vv, AnnInstanceError) for vv in row), axis=1
        ).to_numpy(bool)
        if not np.any(errIdxs):
            return parsedDf
        if not parseErrorOk:
            raise AnnParseError(instances=parsedDf, invalidIndexes=errIdxs)
        # If only a subset is kept, `copy` is necessary to avoid SettingsWithCopyWarning
        # when this df is modified elsewhere
        return parsedDf[~errIdxs].copy()

    def defaultBulkImport(self, importObj, **kwargs) -> pd.DataFrame:
        return pd.DataFrame(self.getInstances(importObj, **kwargs))

    def individualImport(self, importObj, **kwargs):
        parsed = []
        if self.formatSingleInstance is None:
            return pd.DataFrame()
        for inst in self.getInstances(importObj, **kwargs):
            parsedInst = self.formatSingleInstance(inst, **kwargs)
            parsed.append(parsedInst)

        indivParsedDf = pd.DataFrame(parsed)
        return indivParsedDf
