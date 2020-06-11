import typing
from typing import Union

from numpy import ndarray
from os import PathLike
from pathlib import Path

"""
Functions that just return a `np.ndarray` are often hard to interpret. Is the output a simple array, image, 
etc.? If it _is_ an image, what is the output shape? Developers and users will have to comb through the function body
or rely on detailed documentation for an answer. This is a simple answer to the problem. By redefining np.ndarray in 
several ways, users and devs can more clearly interpret the intenionality behind various np.ndarray (and other) types.
"""

BlackWhiteImg = ndarray
GrayImg = ndarray
RgbImg = ndarray
RgbaImg = ndarray
NChanImg = ndarray
OneDArr = ndarray
TwoDArr = ndarray
ThreeDArr = ndarray

FilePath = Union[PathLike, str, Path]
