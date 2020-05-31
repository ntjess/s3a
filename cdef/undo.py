"""
Inspired by 'undo' on pypi (https://bitbucket.org/aquavitae/undo/src/default/)
but there are many flaws and the project is not under active development. It is
also less pythonic than it could be, using functions where properties are more
appropriate.
"""
from __future__ import annotations

import contextlib
from collections import deque
from functools import wraps
from typing import Callable, Generator, Deque, Union, Type, Any

from typing_extensions import Protocol

from cdef.structures import FRUndoStackError


class _FRAction:
  """
  This represents an action which can be done and undone.
  """
  def __init__(self, generator: Callable[[...], Generator], args:tuple=None,
               kwargs:dict=None, descr: str=None, treatAsUndo=False):
    if args is None:
      args = []
    if kwargs is None:
      kwargs = {}
    if descr is None:
      descr = generator.__name__
    self._generator = generator
    self.args = args
    self.kwargs = kwargs
    self.descr = descr
    self._runner = None

    self.treatAsUndo = treatAsUndo
    if treatAsUndo:
      # Need to swap how methods are treated
      tmp = self.forward
      self.forward = self.backward
      self.backward = tmp


  def do(self):
    if self.treatAsUndo:
      return self.backward()
    else:
      return self.forward()

  def forward(self):
    """Do or redo the action"""
    self._runner = self._generator(*self.args, **self.kwargs)
    # Forward use is expired, so treat as backward now
    self.treatAsUndo = True
    return next(self._runner)

  def backward(self):
    """Undo the action"""
    self.treatAsUndo = False
    ret = None
    try:
      ret = next(self._runner)
    except StopIteration:
      # raise FRUndoError(f'Attempted to undo action {self.descr} which didn\'t define'
      #                   f' an undo block.')
      pass
    # Delete it so that its not accidentally called again
    del self._runner
    return ret

class EMPTY: pass
EmptyType = Type[EMPTY]
_FRONT = -1
_BACK = 0

class Appendable(Protocol):
  def append(self):
    raise NotImplementedError

class FRActionStack:
  """ The main undo stack. 
      
  The two key features are the :func:`redo` and :func:`undo` methods. If an 
  exception occurs during doing or undoing a undoable, the undoable
  aborts and the stack is cleared to avoid any further data corruption. 
  
  The stack provides two properties for tracking actions: *docallback* 
  and *undocallback*. Each of these allow a callback function to be set
  which is called when an action is done or undone repectively. By default, 
  they do nothing.
  
  >>> stack = FRActionStack()
  >>> def done():
  ...     print('Can now undo: {}'.format(stack.undotext))
  >>> def undone():
  ...     print('Can now redo: {}'.format(stack.redotext))
  >>> stack.doCallback = done
  >>> stack.undoCallback = undone
  >>> @undoable('An action')
  ... def action():
  ...     yield
  >>> action()
  Can now undo: Undo An action
  >>> stack.undo()
  Can now redo: Redo An action
  >>> stack.redo()
  Can now undo: Undo An action
  
  Setting them back to ``lambda: None`` will stop any further actions.
  
  >>> stack.doCallback = stack.undoCallback = lambda: None
  >>> action()
  >>> stack.undo()
  
  It is possible to mark a point in the undo history when the document
  handled is saved. This allows the undo system to report whether a 
  document has changed. The point is marked using :func:`savepoint` and
  :func:`haschanged` returns whether or not the state has changed (either
  by doing or undoing an action). Only one savepoint can be tracked,
  marking a new one removes the old one.
  
  >>> stack.setSavepoint()
  >>> stack.hasChanged
  False
  >>> action()
  >>> stack.hasChanged
  True
  """

  def __init__(self, maxlen:int=None):
    self.undos: Deque[_FRAction] = deque(maxlen=maxlen)
    self._redos: Deque[_FRAction] = deque(maxlen=maxlen)
    if maxlen is not None:
      maxlen *= 2
    self.actions: Deque[_FRAction] = deque(maxlen=maxlen)
    self._receiver = self.undos
    self._savepoint: Union[EmptyType, _FRAction] = EMPTY
    self.undoCallback = lambda: None
    self.doCallback = lambda: None

    self.key = None

  @contextlib.contextmanager
  def group(self, desc, newActBuffer: Appendable=None):
    """ Return a context manager for grouping undoable actions.

    All actions which occur within the group will be undone by a single call
    of `stack.undo`."""
    if newActBuffer is None:
      newActBuffer: Deque[_FRAction] = deque()
    with self.overrideBuffer(self, newActBuffer):
      yield
    def grpAct():
      for _ in range(2):
        for act in newActBuffer:
          act.do()
        yield
    self.actions.append(_FRAction(grpAct, treatAsUndo=True))

  def undoable(self, descr=None):
    """ Decorator which creates a new undoable action type.

    This decorator should be used on a generator of the following format::

      @undoable
      def operation(*args):
        do_operation_code
        yield returnval
        undo_operator_code
    """
    def decorator(generatorFn: Callable[[...], Generator]):
      nonlocal descr
      if descr is None:
        descr = generatorFn.__name__
      @wraps(generatorFn)
      def inner(*args, **kwargs):
        action = _FRAction(generatorFn, args, kwargs, descr)
        self.actions.appendleft(action)
        ret = self.redo()
        self.flushUnusedRedos()
        return ret
      return inner
    return decorator

  @property
  def canUndo(self):
    """ Return *True* if undos are available """
    try:
      return self.actions[-1].treatAsUndo
    except IndexError:
      return False

  @property
  def canRedo(self):
    """ Return *True* if redos are available """
    try:
      return not self.actions[0].treatAsUndo
    except IndexError:
      return False

  def flushUnusedRedos(self):
    while (len(self.actions) > 0
           and not self.actions[0].treatAsUndo):
      self.actions.popleft()

  def redo(self):
    """
    Redo the last undone action.
    
    This is only possible if no other actions have occurred since the 
    last undo call.
    """
    if self.canRedo:
      self.actions.rotate(-1)
      ret = self.processAct()
      self.doCallback()
      return ret
    else:
      raise FRUndoStackError('Nothing to redo')

  def undo(self):
    """
    Undo the last action.
    """
    if self.canUndo:
      ret = self.processAct()
      self.actions.rotate(1)
      self.undoCallback()
      return ret
    else:
      raise FRUndoStackError('Nothing to undo')

  def processAct(self):
    """ Undo the last action. """
    act = self.actions[-1]
    with self.overrideBuffer(act):
      try:
        return act.do()
      except Exception as ex:
        # In general exceptions are recoverable, so don't obliterate the undo stack
        # self.clear()
        raise

  def clear(self):
    """ Clear the undo list. """
    self.undos.clear()
    self._redos.clear()
    self._savepoint = EMPTY
    self.actions.clear()

  @contextlib.contextmanager
  def overrideBuffer(self, key: Any, newActQueue: deque=None):
    """ Return a contect manager which temporarily pauses the receiver. """
    if newActQueue is None:
      newActQueue = deque()

    if self.key is None:
      oldActions = self.actions
      self.actions = newActQueue
      self.key = key
    yield

    if key is self.key:
      try:
        self.actions = oldActions
      except NameError:
        raise FRUndoStackError('Correct key was given, but restore point was never'
                          ' created in the first place.')
      finally:
        self.key = None

  def append(self, action):
    """ Add a undoable to the stack, using ``receiver.append()``. """
    if self._receiver is not None:
      self._receiver.append(action)
    if self._receiver is self.undos:
      self._redos.clear()
      self.doCallback()

  def setSavepoint(self):
    """ Set the savepoint. """
    if self.canUndo:
      self._savepoint = self.undos[-1]
    else:
      self._savepoint = EMPTY

  @property
  def hasChanged(self):
    """ Return *True* if the state has changed since the savepoint. 
    
    This will always return *True* if the savepoint has not been set.
    """
    if self.canUndo:
      cmpAction = self.undos[-1]
    else:
      cmpAction = EMPTY
    return self._savepoint is not cmpAction
