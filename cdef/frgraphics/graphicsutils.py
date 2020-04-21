import pickle as pkl
import sys
from functools import partial
from functools import wraps
from glob import glob
from os.path import basename
from pathlib import Path
from typing import Optional, Union

from pyqtgraph.Qt import QtCore, QtWidgets, QtGui

from cdef.projectvars import ANN_AUTH_DIR

Signal = QtCore.pyqtSignal
QCursor = QtGui.QCursor

def disableAppDuringFunc(func):
  @wraps(func)
  def disableApp(*args, **kwargs):
    # Captures 'self' instance
    mainWin = args[0]
    try:
      mainWin.setEnabled(False)
      return func(*args, **kwargs)
    finally:
      mainWin.setEnabled(True)
  return disableApp

def popupFilePicker(parent, winTitle: str, fileFilter: str) -> Optional[str]:
  retVal = None
  fileDlg = QtWidgets.QFileDialog()
  fname, _ = fileDlg.getOpenFileName(parent, winTitle, '', fileFilter)

  if len(fname) > 0:
    retVal = fname
  return retVal

def dialogSaveToFile(parent, saveObj, winTitle, saveDir, fileType, allowOverwriteDefault=False)\
    -> Optional[str]:
  failedSave = True
  returnVal: Optional[str] = None
  while failedSave:
    saveName, ok = QtWidgets.QInputDialog() \
      .getText(parent, winTitle, winTitle + ':', QtWidgets.QLineEdit.Normal)
    # TODO: Make this more robust. At the moment just very basic sanitation
    for disallowedChar in ['/', '\\']:
      saveName = saveName.replace(disallowedChar, '')
    if ok and not saveName:
      # User presses 'ok' without typing anything except disallowed characters
      # Keep asking for a name
      continue
    elif not ok:
      # User pressed 'cancel' -- Doesn't matter whether they entered a name or not
      # Stop asking for name
      break
    else:
      # User pressed 'ok' and entered a valid name
      returnVal = saveName
      errMsg = saveToFile(saveObj, saveDir, saveName, fileType, allowOverwriteDefault)
      # Prevent overwriting default layout
      if errMsg is not None:
        QtWidgets.QMessageBox().information(parent, f'Error During Save', errMsg, QtWidgets.QMessageBox.Ok)
        return None
      else:
          failedSave = False
  return returnVal

def saveToFile(saveObj, saveDir, saveName, fileType, allowOverwriteDefault=False) -> str:
  """Attempts to svae to file, and returns the err message if there was a problem"""
  errMsg = None
  if not allowOverwriteDefault and saveName.lower() == 'default':
    errMsg = 'Cannot overwrite default setting.\n\'Default\' is automatically' \
             ' generated, so it should not be modified.'
  else:
    try:
      with open(f'{saveDir}{saveName}.{fileType}', 'wb') as saveFile:
        pkl.dump(saveObj, saveFile)
    except FileNotFoundError as e:
      errMsg = 'Invalid save name. Please rename the parameter state.'
  return errMsg

def dialogGetAuthorName(parent: QtWidgets.QMainWindow) -> str:
  """
  Attempts to load the username from a default file if found on the system. Otherwise,
  requests the user name.
  :param parent:
  :return:
  """
  annPath = Path(ANN_AUTH_DIR)
  annFile = annPath.joinpath('defaultAuthor.txt')
  msgDlg = QtWidgets.QMessageBox(parent)
  msgDlg.setModal(True)
  if annFile.exists():
    with open(str(annFile), 'r') as ifile:
      lines = ifile.readlines()
      if not lines:
        reply = msgDlg.No
      else:
        name = lines[0]
        reply = msgDlg.question(parent, 'Default Author',
                  f'The default author for this application is\n{name}.\n'
                     f'Is this you?', msgDlg.Yes, msgDlg.No)
      if reply == msgDlg.Yes:
        return name

  dlg = QtWidgets.QInputDialog(parent)
  dlg.setCancelButtonText('Quit')
  dlg.setModal(True)
  name = ''
  ok = False
  quitApp = False
  while len(name) < 1 or not ok:
    name, ok = dlg.getText(parent, 'Enter Username', 'Please enter your username: ',
                           QtWidgets.QLineEdit.Normal)
    if not ok:
      reply = msgDlg.question(parent, 'Quit Application',
                              f'Quit the application?', msgDlg.Yes, msgDlg.No)
      if reply == msgDlg.Yes:
        quitApp = True
        break
  if quitApp:
    sys.exit(0)
  return name

def attemptLoadSettings(fpath, openMode='rb', showErrorOnFail=True):
  """
  I/O helper function that, when given a file path, either returns the pickle object
  associated with that file or displays an error message and returns nothing.
  """
  pklObj = None
  try:
    curFile = open(fpath, openMode)
    pklObj = pkl.load(curFile)
    curFile.close()
  except IOError as err:
    if showErrorOnFail:
      QtWidgets.QErrorMessage().showMessage(f'Settings could not be loaded.\n'
                                      f'Error: {err}')
  finally:
    return pklObj

def addDirItemsToMenu(parentMenu, dirRegex, triggerFunc, removeExistingChildren=True):
  """Helper function for populating menu from directory contents"""
  # We don't want all menu children to be removed, since this would also remove the 'edit' and
  # separator options. So, do this step manually. Remove all actions after the separator
  if removeExistingChildren:
    encounteredSep = False
    for ii, action in enumerate(parentMenu.children()):
      if encounteredSep:
        parentMenu.removeAction(action)
      elif action.isSeparator():
        encounteredSep = True
  # TODO: At the moment param files that start with '.' aren't getting included in the
  #  glob
  itemNames = glob(dirRegex)
  for name in itemNames:
    # glob returns entire filepath, so keep only filename as layout name
    name = basename(name)
    # Also strip file extension
    name = name[0:name.rfind('.')]
    curAction = parentMenu.addAction(name)
    curAction.triggered.connect(partial(triggerFunc, name))

def create_addMenuAct(parent: QtWidgets.QMenu, title: str, asMenu=False) -> Union[QtWidgets.QMenu, QtWidgets.QAction]:
  menu = None
  if asMenu:
    menu = QtWidgets.QMenu(title)
    act = menu.menuAction()
  else:
    act = QtWidgets.QAction(title)
  parent.addAction(act)
  if asMenu:
    return menu
  else:
    return act