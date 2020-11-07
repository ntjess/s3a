import shutil
from pathlib import Path
from typing import List, Optional, Set, Dict

import numpy as np
import pandas as pd
from skimage import io

from s3a.constants import BASE_DIR, REQD_TBL_FIELDS
from s3a.generalutils import resolveYamlDict
from s3a.graphicsutils import saveToFile, popupFilePicker
from s3a.io import ComponentIO
from s3a.parameditors.table import TableData
from s3a.structures import FilePath, NChanImg, S3AIOError


def hierarchicalUpdate(curDict: dict, other: dict):
  """Dictionary update that allows nested keys to be updated without deleting the non-updated keys"""
  for k, v in other.items():
    curVal = curDict.get(k, None)
    if isinstance(curVal, dict):
      hierarchicalUpdate(curVal, v)
    else:
      curDict[k] = v

class ProjectData:
  def __init__(self):
    self.tableData = TableData()
    self.cfg = {}
    self.cfgFname: Optional[Path] = Path()
    self.images: List[Path] = []
    self.baseImgDirs: Set[Path] = set()
    self.imgToAnnMapping: Dict[Path, Path] = {}
    """Records annotations belonging to each image"""
    self.exportOpts = {}

    self.compIo = ComponentIO()
    self.compIo.tableData = self.tableData

  @property
  def location(self):
      return self.cfgFname.parent
  @property
  def imagesDir(self):
      return self.location/'images'
  @property
  def annotationsDir(self):
      return self.location/'annotations'

  def loadCfg(self, cfgFname: FilePath=None, cfgDict: dict = None):
    _, defaultCfg = resolveYamlDict(BASE_DIR/'projectcfg.yml')
    cfgFname, cfgDict = resolveYamlDict(cfgFname, cfgDict)
    hierarchicalUpdate(defaultCfg, cfgDict)
    cfg = self.cfg = defaultCfg
    self.exportOpts = cfg['export-opts']
    self.cfgFname = cfgFname
    tableInfo = cfg.get('table-cfg', {})
    if isinstance(tableInfo, str):
      tableDict = None
      tableName = tableInfo
    else:
      tableDict = tableInfo
      tableName = cfgFname
    self.tableData.loadCfg(tableName, tableDict)

  def createProject(self, *, name: FilePath= './projectcfg.yml', cfg: dict=None):
    """
    Creates a new project with the specified settings in the specified directory.
    :param name:
      helpText: Project Name. The parent directory of this name indicates the directory in which to create the project
      pType: filepicker
    :param cfg: see `ProjectData.loadCfg` for information
    """
    name = Path(name)
    location = name.parent
    location = Path(location)
    location.mkdir(exist_ok=True, parents=True)

    if not name.exists() and cfg is None:
      cfg = {}
    self.loadCfg(name, cfg)

    self.annotationsDir.mkdir(exist_ok=True)
    self.imagesDir.mkdir(exist_ok=True)

    shouldCopy = self.cfg['import-opts']['copy-all']
    for image in self.cfg['images']:
      if not isinstance(image, dict):
        image = Path(image)
        if image.is_dir():
          self.addImageFolder(image, shouldCopy)
          continue
        image = {'name': image}
      self.addImage(**image, copyToProj=shouldCopy)
    for annotation in self.cfg['annotations']:
      if not isinstance(annotation, dict):
        annotation = {'name': annotation}
      self.addAnnotation(**annotation)

    newName = self.tableData.cfgFname.name
    saveToFile(self.tableData.cfg, location/newName, True)
    self.tableData.cfgFname = newName
    self.cfg['table-cfg'] = newName

    self.saveCfg()

  def saveCfg(self):
    strImgNames = []
    strAnnNames = []
    location = self.location
    for folder in self.baseImgDirs:
      if location in folder.parents:
        folder = folder.relative_to(location)
      strImgNames.append(str(folder))
    for img in self.images:
      if img.parent in self.baseImgDirs:
          # This image is already accounted for in the base directories
          continue
      strImgNames.append(str(img))
    for ann in self.imgToAnnMapping.values():
      if location in ann.parents:
        outName = str(ann.relative_to(location))
      else:
        outName = str(ann)
      strAnnNames.append(outName)
    self.cfg['images'] = strImgNames
    self.cfg['annotations'] = strAnnNames
    saveToFile(self.cfg, self.cfgFname)

  def addImage(self, name: FilePath, data: NChanImg=None, copyToProj=False):
    name = Path(name).resolve()
    if copyToProj or data is not None:
      name = self._copyImgToProj(name, data)
    if name not in self.images:
      self.images.append(name)
    return name

  def changeImgPath(self, oldName: Path, newName: Path=None):
    oldIdx = self.images.index(oldName)
    if newName is None or newName in self.images:
      del self.images[oldIdx]
    else:
      self.images[oldIdx] = newName

  def addImageFolder(self, folder: FilePath, copyToProj=False):
    folder = Path(folder)
    if copyToProj:
      newFolder = self.imagesDir/folder.name
      shutil.copytree(folder, newFolder)
      folder = newFolder
      copyToProj = False
    self.baseImgDirs.add(folder)
    for img in folder.glob('*.*'):
      self.addImage(img, copyToProj=copyToProj)

  def addImage_gui(self, copyToProject=True):
    fileFilter = "Image Files (*.png *.tif *.jpg *.jpeg *.bmp *.jfif);;All files(*.*)"
    fname = popupFilePicker(self, 'Add Image to Project', fileFilter)
    if fname is not None:
      self.addImage(fname, copyToProj=copyToProject)

  def removeImage(self, imgName: FilePath):
    imgName = Path(imgName).resolve()
    self.images.remove(imgName)
    # Remove copied annotations for this image
    for ann in self.annotationsDir.glob(f'{imgName.stem}.*'):
      ann.unlink()
    self.imgToAnnMapping.pop(imgName, None)

  def removeAnnotation(self, annName: FilePath):
    annName = Path(annName).resolve()
    # Since no mapping exists of all annotations, loop the long way until the file is found
    for key, ann in self.imgToAnnMapping.items():
      if annName == ann:
        del self.imgToAnnMapping[key]
        break

  def addAnnotation(self, name: FilePath=None, data: pd.DataFrame=None, image: FilePath=None,
                    overwriteOld=False):
    # Housekeeping for default arguments
    if name is None and data is None:
      raise S3AIOError('`name` and `data` cannot both be `None`')
    if data is None:
      data = ComponentIO.buildByFileType(name)
    if image is None:
      # If no explicit matching to an image is provided, try to determine based on annotation name
      xpondingImgs = np.unique(data[REQD_TBL_FIELDS.SRC_IMG_FILENAME].to_numpy())
      # Break into annotaitons by iamge
      for img in xpondingImgs:
        self.addAnnotation(name, data, img)
    image = self._getFullImgName(Path(image))
    # Since only one annotation file can exist per image, concatenate this with any existing files for the same image
    # if needed
    if image.parent != self.imagesDir:
      image = self._copyImgToProj(image)
    annForImg = self.imgToAnnMapping.get(image, None)
    oldAnns = []
    if annForImg is not None and not overwriteOld:
      oldAnns.append(ComponentIO.buildByFileType(annForImg))
    combinedAnns = oldAnns + [data]
    outAnn = pd.concat(combinedAnns, ignore_index=True)
    outAnn[REQD_TBL_FIELDS.INST_ID] = outAnn.index
    outFmt = f".{self.exportOpts['annotation-format']}"
    outName = self.annotationsDir / image.with_suffix(outFmt).name
    ComponentIO.exportByFileType(outAnn, outName, verifyIntegrity=False, readOnly=False, imgDir=self.imagesDir)
    self.imgToAnnMapping[image] = outName

  def _copyImgToProj(self, name: Path, data: NChanImg=None):
    name = name.resolve()
    newName = self.imagesDir/name.name
    if newName.exists():
      raise S3AIOError(f'Image {newName} already exists in the project')
    if name.exists() and data is None:
      shutil.copy(name, newName)
    elif data is not None:
      # Programmatically created or not from a local file
      # noinspection PyTypeChecker
      io.imsave(newName, data)
    else:
      raise S3AIOError(f'No image data associated with {name.name}. Either the file does not exist or no'
                       f' image information was provided.')
    newName = newName.resolve()
    if name in self.images:
      self.changeImgPath(name, newName)
    return newName.resolve()

  def _getFullImgName(self, name: Path, thorough=True):
    """
    From an absolute or relative image name, attempts to find the absolute path it corresponds
    to based on current project images. A match is located in the following order:
      - If the image path is already absolute, it is resolved, checked for existence, and returned
      - Solitary project images are searched to see if they end with the specified relative path
      - All base image directories are checked to see if they contain this subpath

    :param thorough: If `False`, as soon as a match is found the function returns. Otherwise,
      all solitary paths and images will be checked to ensure there is exactly one matching
      image for the name provided.
    """
    if name.is_absolute():
      return name.resolve()

    candidates = set()
    strName = str(name)
    for img in self.images:
      if str(img).endswith(strName):
        if not thorough:
          return img
        candidates.add(img)

    for parent in self.baseImgDirs:
      curName = (parent/name).resolve()
      if curName.exists():
        if not thorough:
          return curName
        candidates.add(curName)

    numCandidates = len(candidates)
    if numCandidates != 1:
      msg = f'Exactly one corresponding image file must exist for a given annotation. However,' \
            f' {numCandidates} candidate images were found'
      if numCandidates == 0:
        msg += '.'
      else:
        msg += f':\n{", ".join([c.name for c in candidates])}'
      raise S3AIOError(msg)
    return candidates.pop()