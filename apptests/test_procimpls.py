import cv2 as cv
import numpy as np
from imageprocessing.processing import ImageProcess

from appsetup import (TESTS_DIR, defaultApp_tester)
from s3a.structures import FRVertices

EXPORT_DIR = TESTS_DIR/'files'

app, dfTester = defaultApp_tester()
# Use a small image for faster testing
baseImg = np.zeros((5,5), 'uint8')
baseImg[2,2] = 255
imgSrc = cv.resize(baseImg, (250,250), interpolation=cv.INTER_NEAREST)
imgSrc = np.tile(imgSrc[:,:,None], (1,1,3))
mgr = app.compMgr
mImg = app.mainImg
pc = mImg.procCollection
allAlgs = pc.nameToProcMapping.keys()
mImg.setImage(imgSrc)

def test_algs_working():
  for alg in allAlgs:
    pc.switchActiveProcessor(alg)
    mImg.handleShapeFinished(FRVertices())

def test_disable_top_stages():
  for proc in pc.nameToProcMapping.values():
    for stage in proc.processor.stages:
      if stage.allowDisable and isinstance(stage, ImageProcess):
        proc.setStageEnabled([stage.name], False)
    pc.switchActiveProcessor(proc)
    mImg.handleShapeFinished(FRVertices())





