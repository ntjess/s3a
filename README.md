<div align="center">
<img src="https://gitlab.com/s3a/s3a/-/wikis/imgs/home/s3alogo-square.svg" width="150px"/>
<h1>Semi-Supervised Semantic Annotator (S3A)</h1>

[![pipeline status](https://gitlab.com/s3a/s3a/badges/development/pipeline.svg)](https://gitlab.com/s3a/s3a/-/commits/development)
[![coverage report](https://gitlab.com/s3a/s3a/badges/development/coverage.svg)](https://gitlab.com/s3a/s3a/-/commits/development)
[![Total alerts](https://img.shields.io/lgtm/alerts/g/ntjess/s3a.svg?logo=lgtm&logoWidth=18)](https://lgtm.com/projects/g/ntjess/s3a/alerts/)
[![Language grade: Python](https://img.shields.io/lgtm/grade/python/g/ntjess/s3a.svg?logo=lgtm&logoWidth=18)](https://lgtm.com/projects/g/ntjess/s3a/context:python)
[![SonarCloud](https://img.shields.io/static/v1?label=Scanned%20On&message=SonarCloud&color=orange)](https://sonarcloud.io/summary/new_code?id=s3a_s3a)

<img src="https://gitlab.com/s3a/s3a/-/wikis/s3a-window.jpg" width="75%"/>
</div>

## Description

A highly adaptable tool for both visualizing and generating semantic annotations for generic images.

Most software solutions for semantic (pixel-level) labeling are designed for low-resolution (<10MB) images with fewer than 10 components of interest. Violating either constraint (e.g. using a high-res image or annotating ~1000 components) incur detrimental performance impacts. S3A is designed to combat both these deficiencies. With images up to 150 MB and 2000 components, the tool remains interactive.

___

A more detailed overview can be found in the project wiki [here](https://gitlab.com/ficsresearch/s3a/-/wikis/docs/user's-guide).

___

## Installation

The easiest method for installing `s3a` is via `pip` after cloning the repository, or directly from pypi:

```bash
git clone https://gitlab.com/ficsresearch/s3a
pip install -e ./s3a

# Or from pypi using "pip install s3a"
```

Note that a version of OpenCV and Qt binding are required for S3A to work. These can be installed for you with the "full" option:
```bash
pip install -e ./s3a[full]
# Or "pip install s3a[full]"
```

## Running the App
Running the app is as easy as calling `s3a` as a module or using a provided entry point:
```bash
python -m s3a
```
Or, equivalently:
```bash
s3a-gui
```


From here, projects can be created to host groups of related images, or images can be annotated in the default project. Both options are available through the `File` menu.

## Detailed Feature List

More information about the capabilities of this tool are outlined in the [project wiki](https://gitlab.com/ficsresearch/s3a/-/wikis/home).

## <span style="color:red">Please Note</span>
S3A's programmatic API is still largely under development. It still needs refinement to allow for consistent naming schemes, removing vestigial elements, confirming private vs. public-facing elements, and a few other line items. However, the graphical interface should be minimally affected by these alterations.

Thus, while the GUI entry point should be consistently useful, be aware of these developments when using the scripting portion of S3A in great detail.

## License

This tool is free for personal and commercial use (except the limits imposed by the selected Qt binding). If you publish something based on results obtained through this app, please cite the following papers:

Jessurun, N., Paradis, O., Roberts, A., & Asadizanjani, N. (2020). Component Detection and Evaluation Framework (CDEF): A Semantic Annotation Tool. Microscopy and Microanalysis, 1-5. doi:10.1017/S1431927620018243

