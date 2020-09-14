# --------------
# Errors used within the application
# --------------
class FRS3AException(Exception): pass
class FRS3AWarning(Warning): pass

class FRIOError(IOError, FRS3AException): pass
class FRInvalidDrawModeError(FRS3AException): pass
class FRParamEditorError(FRS3AException): pass
class FRInvalidROIEvType(FRS3AException): pass
class FRIllFormedVerticesError(FRS3AException): pass
class FRAlgProcessorError(FRS3AException): pass
class FRActionStackError(FRS3AException): pass