import os

os.environ["S3A_PLATFORM"] = "minimal"
from typing import Type

import pytest

from s3a.constants import PRJ_ENUMS
from s3a import constants, mkQApp
from helperclasses import CompDfTester
from s3a.views.s3agui import S3A
from testingconsts import (
    SAMPLE_IMG,
    SAMPLE_IMG_FNAME,
    NUM_COMPS,
    SAMPLE_SMALL_IMG,
    SAMPLE_SMALL_IMG_FNAME,
)
from s3a.plugins.tablefield import VerticesPlugin
from s3a.plugins.file import FilePlugin

mkQApp()

dfTester = CompDfTester(NUM_COMPS)
dfTester.fillRandomVerts(imageShape=SAMPLE_IMG.shape)


@pytest.fixture(scope="module")
def sampleComps():
    return dfTester.compDf.copy()


# Assign temporary project directory
@pytest.fixture(scope="session", autouse=True)
def app(tmpdir_factory):
    constants.APP_STATE_DIR = tmpdir_factory.mktemp("settings")
    app_ = S3A(Image=SAMPLE_IMG_FNAME, log=PRJ_ENUMS.LOG_TERM, loadLastState=False)
    app_.filePlugin.projData.create(
        name=str(tmpdir_factory.mktemp("proj")), parent=app_.filePlugin.projData
    )
    return app_


@pytest.fixture(scope="session")
def filePlugin(app):
    plg: FilePlugin = app.filePlugin
    return plg


@pytest.fixture(scope="session")
def mgr(app):
    return app.compMgr


@pytest.fixture(scope="session", autouse=True)
def vertsPlugin(app) -> VerticesPlugin:
    try:
        # False positive, since clsToPluginMapping returns valid subclasses of plugins too
        # noinspection PyTypeChecker
        plg: VerticesPlugin = app.clsToPluginMapping[VerticesPlugin]
    except KeyError:
        raise RuntimeError(
            "Vertices plugin was not provided. Some tests are guaranteed to fail."
        )

    plg.queueActions = False
    plg.procEditor.changeActiveProcessor("Basic Shapes")
    return plg


# Each test can request wheter it starts with components, small image, etc.
# After each test, all components are removed from the app
@pytest.fixture(autouse=True)
def resetAppAndTester(request, app, filePlugin, mgr):
    for img in filePlugin.projData.images:
        try:
            if img != app.srcImgFname:
                filePlugin.projData.removeImage(img)
        except (FileNotFoundError,):
            pass
    app.mainImg.shapeCollection.forceUnlock()
    if "smallimage" in request.keywords:
        app.setMainImg(SAMPLE_SMALL_IMG_FNAME, SAMPLE_SMALL_IMG)
    else:
        app.setMainImg(SAMPLE_IMG_FNAME, SAMPLE_IMG)
    if "withcomps" in request.keywords:
        dfTester.fillRandomVerts(app.mainImg.image.shape)
        mgr.addComps(dfTester.compDf.copy())
    yield
    app.sharedAttrs.actionStack.clear()
    app.clearBoundaries()


def assertExInList(exList, typ: Type[Exception] = Exception):
    assert any(issubclass(ex[0], typ) for ex in exList)
