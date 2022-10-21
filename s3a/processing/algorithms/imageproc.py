import functools
from typing import Tuple, Union, Dict, Any

import cv2 as cv
import numpy as np
from scipy.ndimage import binary_fill_holes
from skimage import morphology as morph, img_as_float
from skimage import segmentation as seg
from skimage.measure import regionprops, label
from skimage.morphology import flood
from utilitys import fns
from utilitys.processing import *

from ...constants import PRJ_ENUMS
from ...generalutils import (
    cornersToFullBoundary,
    getCroppedImage,
    showMaskDifference,
    tryCvResize,
    getObjectsDefinedInSelfModule,
)
from ...structures import (
    BlackWhiteImg,
    XYVertices,
    ComplexXYVertices,
    NChanImg,
    GrayImg,
)

# `__all__` is defined at the bottom programmatically

UNSPEC = PRJ_ENUMS.HISTORY_UNSPECIFIED
FGND = PRJ_ENUMS.HISTORY_FOREGROUND
BGND = PRJ_ENUMS.HISTORY_BACKGROUND

# TODO: Establish better mechanism than global buffer
procCache: Dict[str, Any] = {"mask": np.array([[]], "uint8")}
"""
While a global structure is not ideal, it allows algorithms to use previous results 
across multiple calls. Only results that need to be accessed across several different 
functions should be stored here. If one function needs to maintain its state across 
multiple calls, it should be promoted to an AtomicProcess and use its own `result` 
structure. keys are: 
  - mask: Mask of previous results, where 0 = unspecified, 1 = background,
    2 = foreground
"""


def growSeedpoint(img: NChanImg, seeds: XYVertices, thresh: float) -> BlackWhiteImg:
    shape = np.array(img.shape[0:2])
    bwOut = np.zeros(shape, dtype=bool)
    # Turn x-y vertices into row-col seeds
    seeds = seeds[:, ::-1]
    # Remove seeds that don't fit in the image
    seeds = seeds[np.all(seeds >= 0, 1)]
    seeds = seeds[np.all(seeds < shape, 1)]

    for seed in seeds:
        for chan in range(img.shape[2]):
            curBwMask = flood(img[..., chan], tuple(seed), tolerance=thresh)
            bwOut |= curBwMask
    return bwOut


def _cvConnComps(
    image: np.ndarray, returnLabels=True, areaOnly=True, removeBackground=True
):
    if image.dtype != "uint8":
        image = image.astype("uint8")
    _, labels, conncomps, _ = cv.connectedComponentsWithStats(image)
    startIdx = 1 if removeBackground else 0
    if areaOnly:
        conncomps = conncomps[:, cv.CC_STAT_AREA]
    if returnLabels:
        return conncomps[startIdx:], labels
    return conncomps[startIdx:]


def format_vertices(
    image: NChanImg,
    foregroundVertices: XYVertices,
    backgroundVertices: XYVertices,
    oldComponentMask: BlackWhiteImg,
    firstRun: bool,
    useFullBoundary=True,
    keepVerticesHistory=True,
):

    if firstRun or not keepVerticesHistory or not np.any(procCache["mask"]):
        _historyMask = np.zeros(image.shape[:2], "uint8")
    else:
        _historyMask = procCache["mask"].copy()

    asForeground = True
    for fillClr, verts in enumerate(
        [backgroundVertices, foregroundVertices], PRJ_ENUMS.HISTORY_BACKGROUND
    ):
        if not verts.empty and verts.connected:
            cv.fillPoly(_historyMask, [verts], fillClr)

    if useFullBoundary:
        if not foregroundVertices.empty:
            foregroundVertices = cornersToFullBoundary(foregroundVertices)
        if not backgroundVertices.empty:
            backgroundVertices = cornersToFullBoundary(backgroundVertices)

    procCache["mask"] = _historyMask
    curHistory = _historyMask.copy()
    if foregroundVertices.empty and not backgroundVertices.empty:
        # Invert the mask and paint foreground pixels
        asForeground = False
        # Invert the history mask too
        curHistory[_historyMask == FGND] = BGND
        curHistory[_historyMask == BGND] = FGND
        foregroundVertices = backgroundVertices
        backgroundVertices = XYVertices()

    if asForeground:
        foregroundAdjustedCompMask = oldComponentMask
    else:
        foregroundAdjustedCompMask = ~oldComponentMask

    # Default to bound slices that encompass the whole image
    bounds = np.array([[0, 0], image.shape[:2][::-1]])
    boundSlices = slice(*bounds[:, 1]), slice(*bounds[:, 0])
    return ProcessIO(
        image=image,
        summaryInfo=None,
        foregroundVertices=foregroundVertices,
        backgroundVertices=backgroundVertices,
        asForeground=asForeground,
        historyMask=curHistory,
        oldComponentMask=foregroundAdjustedCompMask,
        unformattedOldComponentMask=oldComponentMask,
        boundSlices=boundSlices,
    )


def crop_to_local_area(
    image: NChanImg,
    foregroundVertices: XYVertices,
    backgroundVertices: XYVertices,
    oldComponentMask: BlackWhiteImg,
    prevCompVerts: ComplexXYVertices,
    viewbox: XYVertices,
    historyMask: GrayImg,
    reference="viewbox",
    marginPct=10,
    maxSize=0,
    useMinSpan=False,
):
    """
    Parameters
    ----------
    reference
      pType: list
      limits: ['image', 'component', 'viewbox', 'roi']
    maxSize
        Maximum side length for a local portion of the image. If the local area exceeds
        this, it will be rescaled to match this size. It can be beneficial for
        algorithms that take a long time to run, and quality of segmentation can be
        retained. Set to <= 0 to have no maximum size
    useMinSpan
        When ``viewbox`` is the reference, this determines whether to crop to the area
        defined by the shortest side of the viewbox. So, for aspect ratios far from 1
        (i.e. heavily rectangular), this prevents a large area from being used every time
    """
    roiVerts = np.vstack([foregroundVertices, backgroundVertices])
    compVerts = np.vstack([prevCompVerts.stack(), roiVerts])
    if reference == "image":
        allVerts = np.array([[0, 0], image.shape[:2]])
    elif reference == "roi" and len(roiVerts) > 1:
        allVerts = roiVerts
    elif reference == "component" and len(compVerts) > 1:
        allVerts = compVerts
    else:
        # viewbox or badly sized previous region/roi
        if useMinSpan:
            center = viewbox.mean(0)
            spans = center - viewbox[0]
            adjustments = (spans - min(spans)).astype(viewbox.dtype)
            # maxs need to be subtracted toward center, mins need to be extended toward
            # center
            dim = np.argmax(adjustments)
            adjust = adjustments[dim]
            maxs = viewbox[:, dim] == viewbox[:, dim].max()
            mins = ~maxs
            viewbox[maxs, dim] -= adjust
            viewbox[mins, dim] += adjust
        allVerts = np.vstack([viewbox, roiVerts])
    # Lots of points, use their bounded area
    try:
        vertArea_rowCol = (allVerts.ptp(0))[::-1]
    except ValueError:
        # 0-sized
        vertArea_rowCol = 0
    margin = int(round(max(vertArea_rowCol) * (marginPct / 100)))
    cropped, bounds = getCroppedImage(image, allVerts, margin)
    ratio = 1
    curMaxDim = np.max(cropped.shape[:2])
    if 0 < maxSize < curMaxDim:
        ratio = maxSize / curMaxDim

    vertOffset = bounds.min(0)
    useVerts = [foregroundVertices, backgroundVertices]
    for ii in range(2):
        # Add additional offset
        tmp = ((useVerts[ii] - vertOffset) * ratio).astype(int)
        useVerts[ii] = np.clip(
            tmp,
            a_min=[0, 0],
            a_max=(bounds[1, :] - 1) * ratio,
            dtype=int,
            casting="unsafe",
        )
    foregroundVertices, backgroundVertices = useVerts

    boundSlices = slice(*bounds[:, 1]), slice(*bounds[:, 0])
    croppedCompMask = oldComponentMask[boundSlices]
    curHistory = historyMask[boundSlices]

    rectThickness = int(max(1, *image.shape) * 0.005)
    # Much faster not to do any rescaling if the image is already the right type
    if image.dtype == np.uint8:
        toPlot = image.copy()
    else:
        image = image.astype("float")
        toPlot = (((image - image.min()) / image.ptp()) * 255).astype("uint8")
    borderColor = 255
    if image.ndim > 2:
        borderColor = (255, *[0 for _ in range(image.shape[2] - 1)])
    cv.rectangle(
        toPlot, tuple(bounds[0, :]), tuple(bounds[1, :]), borderColor, rectThickness
    )
    info = {"name": "Selected Area", "image": toPlot}
    out = ProcessIO(
        image=cropped,
        foregroundVertices=foregroundVertices,
        backgroundVertices=backgroundVertices,
        oldComponentMask=croppedCompMask,
        boundSlices=boundSlices,
        historyMask=curHistory,
        resizeRatio=ratio,
        summaryInfo=info,
    )
    if ratio < 1:
        for kk in "image", "oldComponentMask", "historyMask":
            out[kk] = cv_resize(out[kk], ratio, interpolation="INTER_NEAREST")
    return out


def apply_process_result(
    image: NChanImg,
    asForeground: bool,
    oldComponentMask: BlackWhiteImg,
    unformattedOldComponentMask: BlackWhiteImg,
    boundSlices: Tuple[slice, slice],
    resizeRatio: float,
):
    if asForeground:
        bitOperation = np.bitwise_or
    else:
        # Add to background
        bitOperation = lambda curRegion, other: ~(curRegion | other)
    # The other basic operations need the rest of the component mask to work properly,
    # so expand the current area of interest only as much as needed. Returning to full
    # size now would incur unnecessary addtional processing times for the full-sized
    # image
    outMask = unformattedOldComponentMask.copy()
    change = bitOperation(oldComponentMask, image)
    if resizeRatio < 1:
        origSize = (
            boundSlices[0].stop - boundSlices[0].start,
            boundSlices[1].stop - boundSlices[1].start,
        )
        # Without first converting to float, interpolation will be cliped to
        # True/False. This causes 'jagged' edges in the output
        change = cv_resize(
            change.astype(float),
            origSize[::-1],
            asRatio=False,
            interpolation="INTER_LINEAR",
        )
    # Vast majority of samples will be near 0 or 1, filter these out
    subset = change[(change > 0.01) & (change < 0.99)].ravel()
    if len(subset) == 0:
        change = change > 0
    else:
        change = change > subset.mean()
    outMask[boundSlices] = change
    xywhRect = cv.boundingRect(outMask.astype("uint8", copy=False))
    # Keep algorithm from failing when no foreground pixels exist
    if not any(xywhRect[2:]):
        mins = [0, 0]
        maxs = [1, 1]
    else:
        mins = xywhRect[:2][::-1]
        maxs = xywhRect[1] + xywhRect[3], xywhRect[0] + xywhRect[2]

    # Add 1 to max slice so stopping value is last foreground pixel
    newSlices = (slice(mins[0], maxs[0] + 1), slice(mins[1], maxs[1] + 1))
    return ProcessIO(
        image=outMask[newSlices], boundSlices=newSlices, summaryInfo={"image": change}
    )


def return_to_full_size(
    image: NChanImg,
    unformattedOldComponentMask: BlackWhiteImg,
    boundSlices: Tuple[slice],
):
    out = np.zeros_like(unformattedOldComponentMask)
    if image.ndim > 2:
        image = image.mean(2).astype(int)
    out[boundSlices] = image

    infoMask = showMaskDifference(unformattedOldComponentMask[boundSlices], image)

    return ProcessIO(
        image=out, summaryInfo={"image": infoMask, "name": "Finalize Region"}
    )


def fill_holes(image: NChanImg):
    return ProcessIO(image=binary_fill_holes(image))


def disallow_paint_tool(
    _image: NChanImg, foregroundVertices: XYVertices, backgroundVertices: XYVertices
):
    if len(np.vstack([foregroundVertices, backgroundVertices])) < 2:
        raise ValueError(
            "This algorithm requires an enclosed area to work."
            " Only one vertex was given as an input."
        )
    return ProcessIO(image=_image)


@fns.dynamicDocstring(morphOps=[d for d in dir(cv) if d.startswith("MORPH_")])
def morph_op(image: NChanImg, radius=1, op: str = "", shape="rectangle"):
    """
    Perform a morphological operation on the input image.

    Parameters
    ----------
    radius
        Radius of the structuring element. Note that the total side length of the
        structuring element will be (2*radius)+1.
    shape
        Structuring element shape
        pType: list
        limits: ['rectangle', 'disk', 'diamond']
    op
        pType: list
        limits: {morphOps}
    """
    opType = getattr(cv, op)
    if image.ndim > 2:
        image = image.mean(2)
    image = image.astype("uint8")
    ksize = [radius]
    if shape == "rectangle":
        ksize = [ksize[0] * 2 + 1] * 2
    strel = getattr(morph, shape)(*ksize)
    outImg = cv.morphologyEx(image.copy(), opType, strel)
    return ProcessIO(image=outImg)


opening = AtomicProcess(morph_op, "Opening", op="MORPH_OPEN")
closing = AtomicProcess(morph_op, "Closing", op="MORPH_CLOSE")


def keep_largest_component(image: NChanImg):
    if not np.any(image):
        return ProcessIO(image=image)
    areas, labels = _cvConnComps(image)
    # 0 is background, so skip it
    out = np.zeros_like(image, shape=image.shape[:2])
    # Offset by 1 since 0 was removed earlier
    maxAreaIdx = np.argmax(areas) + 1
    out[labels == maxAreaIdx] = True
    return ProcessIO(image=out)


def remove_small_components(image: NChanImg, sizeThreshold=30):
    areas, labels = _cvConnComps(image, areaOnly=True)
    validLabels = np.flatnonzero(areas >= sizeThreshold) + 1
    out = np.isin(labels, validLabels)
    return ProcessIO(image=out)


def draw_vertices(image: NChanImg, foregroundVertices: XYVertices):
    if len(foregroundVertices):
        image = ComplexXYVertices([foregroundVertices]).toMask(image.shape[:2]) > 0
    else:
        image = np.zeros(image.shape[:2], dtype=bool)
    return ProcessIO(image=image)


def convert_to_squares(image: NChanImg):
    outMask = np.zeros(image.shape, dtype=bool)
    for region in regionprops(label(image)):
        outMask[region.bbox[0] : region.bbox[2], region.bbox[1] : region.bbox[3]] = True
    return ProcessIO(image=outMask)


class CvGrabcut(AtomicProcess):
    def __init__(self, **kwargs):
        super().__init__(self.grabcut, "Cv Grabcut", **kwargs)

    def grabcut(
        self,
        image: NChanImg,
        oldComponentMask: BlackWhiteImg,
        foregroundVertices: XYVertices,
        firstRun: bool,
        historyMask: GrayImg,
        iters=5,
    ):
        if image.size == 0:
            return ProcessIO(image=np.zeros_like(oldComponentMask))
        img = cv.cvtColor(image, cv.COLOR_RGB2BGR)
        historyMask = historyMask.copy()
        if historyMask.size:
            historyMask[foregroundVertices.rows, foregroundVertices.columns] = FGND
        mask = np.zeros(oldComponentMask.shape, dtype="uint8")
        if historyMask.shape == mask.shape:
            mask[oldComponentMask == 1] = cv.GC_PR_FGD
            mask[oldComponentMask == 0] = cv.GC_PR_BGD
            mask[historyMask == FGND] = cv.GC_FGD
            mask[historyMask == BGND] = cv.GC_BGD
        if len(foregroundVertices) and np.all(foregroundVertices.ptp(0) > 1):
            cvRect = np.array(
                [foregroundVertices.min(0), foregroundVertices.ptp(0)]
            ).flatten()
            mode = None
        else:
            # Can't make a rect out of empty / 0-length vertices, use dummy values
            cvRect = None
            mode = cv.GC_INIT_WITH_MASK

        if firstRun or self.result is None or "fgdModel" not in self.result:
            bgdModel = np.zeros((1, 65), np.float64)
            fgdModel = np.zeros((1, 65), np.float64)
        else:
            bgdModel = self.result["bgdModel"]
            fgdModel = self.result["fgdModel"]

        if not np.any(mask):
            if cvRect[2] == 0 or cvRect[3] == 0:
                return ProcessIO(image=np.zeros_like(oldComponentMask))
            mode = mode or cv.GC_INIT_WITH_RECT
        else:
            mode = mode or cv.GC_INIT_WITH_MASK
        cv.grabCut(img, mask, cvRect, bgdModel, fgdModel, iters, mode=mode)
        outMask = np.where((mask == cv.GC_BGD) | (mask == cv.GC_PR_BGD), False, True)
        return ProcessIO(labels=outMask, fgdModel=fgdModel, bgdModel=bgdModel)


@functools.partial(
    AtomicProcess,
    docFunc=seg.quickshift,
    name="Quickshift Segmentation",
    ignoreKeys={"return_tree", "convert2lab", "random_seed"},
)
def quickshift_segmentation(
    image: NChanImg, ratio=1.0, max_dist=10.0, kernel_size=5, sigma=0.0
):
    if max_dist == 0:
        # Make sure output is still 1-channel
        segImg = image.mean(2).astype(int) if image.ndim > 2 else image
    else:
        if image.ndim < 3:
            image = np.tile(image[:, :, None], (1, 1, 3))
        segImg = seg.quickshift(
            image, ratio=ratio, kernel_size=kernel_size, max_dist=max_dist, sigma=sigma
        )
    return ProcessIO(labels=segImg)


# Taken from example page: https://scikit-image.org/docs/dev/auto_examples/segmentation/plot_morphsnakes.html  # noqa
def morph_acwe(image: NChanImg, initialCheckerSize=6, iters=35, smoothing=3):
    image = img_as_float(image)
    if image.ndim > 2:
        image = image.mean(2)
    initLs = seg.checkerboard_level_set(image.shape, initialCheckerSize)
    outLevels = seg.morphological_chan_vese(
        image, iters, init_level_set=initLs, smoothing=smoothing
    )
    return ProcessIO(labels=outLevels)


def k_means_segmentation(image: NChanImg, kValue=5, attempts=10):
    # Logic taken from
    # https://docs.opencv.org/master/d1/d5c/tutorial_py_kmeans_opencv.html
    numChannels = 1 if image.ndim < 3 else image.shape[2]
    clrs = image.reshape(-1, numChannels)
    clrs = clrs.astype("float32")
    criteria = (cv.TERM_CRITERIA_EPS + cv.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    ret, lbls, imgMeans = cv.kmeans(
        clrs, kValue, None, criteria, attempts, cv.KMEANS_RANDOM_CENTERS
    )
    # Now convert back into uint8, and make original image
    imgMeans = imgMeans.astype(image.dtype)
    lbls = lbls.reshape(image.shape[:2])

    return ProcessIO(labels=lbls, means=imgMeans)


def _labelBoundaries_cv(labels: np.ndarray, thickness: int):
    """Code stolen and reinterpreted for cv from skimage.segmentation.boundaries"""
    if thickness % 2 == 0:
        thickness += 1
    thickness = max(thickness, 3)
    if labels.dtype not in [np.uint8, np.uint16, np.int16, np.float16, np.float32]:
        labels = labels.astype(np.uint16)
    strel = cv.getStructuringElement(cv.MORPH_RECT, (thickness, thickness))
    return cv.morphologyEx(labels, cv.MORPH_DILATE, strel) != cv.morphologyEx(
        labels, cv.MORPH_ERODE, strel
    )


def binarize_labels(
    image: NChanImg,
    labels: BlackWhiteImg,
    foregroundVertices: XYVertices,
    historyMask: BlackWhiteImg,
    touchingRoiOnly=True,
    useMeanColor=True,
    lineThickness=2,
):
    """
    For a given binary image input, only keeps connected components that are directly
    in contact with at least one of the specified vertices. In essence, this function
    can make a wide variety of operations behave similarly to region growing.

    Parameters
    ----------
    touchingRoiOnly
        Whether to only keep labeled regions that are in contact with the current ROI
    useMeanColor
        Whether to color the summary info image with mean values or (if *False*) just
        draw the boundaries around each label.
    lineThickness
        How thick to draw label boundary and ROI vertices lines
    """
    if labels.ndim > 2:
        raise ValueError(
            "Cannot handle multichannel labels.\n" f"(labelss.shape={labels.shape})"
        )
    seeds = np.clip(foregroundVertices[:, ::-1], 0, np.array(labels.shape) - 1)
    if image.ndim < 3:
        image = image[..., None]
    if touchingRoiOnly and len(seeds):
        out = np.zeros_like(labels, dtype=bool)
        for seed in seeds:
            out |= flood(
                labels,
                tuple(seed),
            )
    elif not len(seeds) and historyMask.shape != labels.shape:
        raise ValueError(
            "Cannot binarize labels without vertices and with misshapen history Mask"
        )
    elif not len(seeds):
        # Treat 0 as background
        out = labels > 0
    else:
        keepColors = labels[seeds[:, 0], seeds[:, 1]]
        out = np.isin(labels, keepColors)
    # Zero out negative regions from previous runs
    if np.issubdtype(labels.dtype, np.bool_):
        # Done for stage summary only
        labels = label(labels)
    if historyMask.shape == out.shape:
        out[historyMask == BGND] = False
    nChans = image.shape[2]
    if useMeanColor:
        summaryImg = np.zeros_like(image)
        # Offset by 1 to avoid missing 0-labels
        for lbl in regionprops(labels + 1):
            coords = lbl.coords
            intensity = image[coords[:, 0], coords[:, 1], ...].mean(0)
            summaryImg[coords[:, 0], coords[:, 1], :] = intensity
    else:
        boundaries = _labelBoundaries_cv(labels, lineThickness)
        summaryImg = image.copy()
        summaryImg[boundaries, ...] = [255 for _ in range(nChans)]
    color = (255,) + tuple(0 for _ in range(1, nChans))
    if len(foregroundVertices):
        cv.drawContours(summaryImg, [foregroundVertices], -1, color, lineThickness)
    return ProcessIO(image=out, summaryInfo={"image": summaryImg})


def region_grow_segmentation(
    image: NChanImg, foregroundVertices: XYVertices, seedThreshold=10
):
    if image.size == 0:
        return ProcessIO(image=np.zeros(image.shape[:2], bool))
    if np.all(foregroundVertices == foregroundVertices[0, :]):
        # Remove unnecessary redundant seedpoints
        foregroundVertices = foregroundVertices[[0], :]
    # outMask = np.zeros(image.shape[0:2], bool)
    # For small enough shapes, get all boundary pixels instead of just shape vertices
    if foregroundVertices.connected:
        foregroundVertices = cornersToFullBoundary(foregroundVertices, 50e3)

    # Don't let region grow outside area of effect
    # img_aoe, coords = getCroppedImg(image, foregroundVertices, areaOfEffect, returnSlices=True)
    # Offset vertices before filling
    # seeds = foregroundVertices - [coords[1].start, coords[0].start]
    outMask = growSeedpoint(image, foregroundVertices, seedThreshold)

    return ProcessIO(image=outMask)


slic_segmentation = AtomicProcess(
    seg.slic,
    "SLIC Segmentation",
    ignoreKeys=[
        "max_iter",
        "spacing",
        "multichannel",
        "convert2lab",
        "enforce_connectivity",
        "slic_zero",
        "start_label",
        "mask",
    ],
    sigma=dict(limits=[0, None], step=0.1, type="float"),
    start_label=1,
    wrapWith=["labels"],
)


@fns.dynamicDocstring(inters=[attr for attr in vars(cv) if attr.startswith("INTER_")])
def cv_resize(
    image: np.ndarray,
    newSize: Union[float, tuple] = 0.5,
    asRatio=True,
    interpolation="INTER_CUBIC",
):
    """
    Like skimage.transform.resize, but uses cv2.resize instead for speed where posible

    Parameters
    ----------
    image
        Image to resize
    interpolation
        pType: list
        limits: {inters}
    newSize
        pType: float
        step: 0.1
    asRatio
        readonly: True
    """
    if isinstance(interpolation, str):
        interpolation = getattr(cv, interpolation)
    return tryCvResize(image, newSize, asRatio, interpolation)


# Lots of functions, __all__ should just take everything public defined in this
# module
_selfModule = cv_resize.__module__
# Prepopulate with functions that won't be programmatically found
__all__ = [
    "procCache",
    "opening",
    "closing",
    "slic_segmentation",
] + getObjectsDefinedInSelfModule(vars(), _selfModule)
