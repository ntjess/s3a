import string
from time import time

import numpy as np
import pyqtgraph as pg
from numpy.random import randint
from pandas import DataFrame as df
from tqdm import tqdm

from sys import path
path.append('..')

from Annotator import Annotator, makeCompDf
from Annotator.constants import TEMPLATE_COMP as TC

np.random.seed(42)
outTimes = {}

maxCompSz = 200

lettersArr = np.array(list(string.ascii_letters + string.digits))
fnames = [f'C:/Users/njessurun/Desktop/Git/ImgAnnotator/Images/{name}.tif' for name in ['fast', 'med', 'orig']]
for inImg in tqdm(fnames, 'Files'):
  outTimes[inImg] = {}
  win = Annotator(inImg)
  winImgShape = win.mainImg.image.shape
  for numComps in tqdm([round(10**x) for x in [2,2.5,3,3.25]], 'Num Comps'):
    comps = makeCompDf(numComps)
    xVerts = randint(winImgShape[1], size=(randint(maxCompSz),1, numComps))
    yVerts = randint(winImgShape[0], size=xVerts.shape)
    allVerts = np.hstack((xVerts, yVerts))
    allVerts_list = [allVerts[:,:,ii] for ii in range(numComps)]
    notes = [''.join(lettersArr[randint(52, size=randint(30))]) for ii in range(numComps)]
    devTxt = [''.join(lettersArr[randint(52, size=randint(30))]) for ii in range(numComps)]
    boardTxt = [''.join(lettersArr[randint(52, size=randint(30))]) for ii in range(numComps)]
    comps[TC.VERTICES] = allVerts_list
    comps[TC.NOTES] = notes
    comps[TC.DEV_TEXT] = devTxt
    comps[TC.BOARD_TEXT] = boardTxt
    timeToAdd = time()
    win.compMgr.addComps(comps)
    timeToAdd = time() - timeToAdd
    win.compMgr.rmComps()
    outTimes[inImg][numComps] = timeToAdd
  outDf = df(outTimes)
  outDf.to_csv('./outTimes.csv')
win.close()
# pg.plot(outTimes)