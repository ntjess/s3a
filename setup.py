from pathlib import Path

from setuptools import setup, find_packages

__version__ = ""
line = open(Path(__file__).parent / "s3a/__version__.py").readline()
exec(line)


def _mltGlob(curDir, *globs):
    curDir = Path(curDir)
    outFiles = []
    for curGlob in globs:
        outFiles.extend(curDir.glob(curGlob))
    outFiles = [str(f) for f in outFiles]
    return outFiles


here = Path(__file__).parent


def getRequirements():
    lines = (here / "requirements.txt").open("r").readlines()
    required = []

    for line in lines:
        if not line.startswith("#"):
            required.append(line.strip("\n"))
    return required


# Get the long description from the README file
long_description = (here / "README.md").read_text(encoding="utf-8")

INSTALL_REQUIRES = getRequirements()

if __name__ == "__main__":
    setup(
        name="s3a",
        version=__version__,
        package_dir={"s3a": "s3a"},
        packages=find_packages(),
        data_files=[
            ("s3a/s3a/icons", _mltGlob("s3a/icons", "*.svg", "*.png")),
            ("s3a/s3a", _mltGlob("s3a/cfg", "*.yml")),
            ("s3a/s3a/compio/templates", _mltGlob("s3a/compio/templates", "*.tblcfg")),
        ],
        entry_points={
            "console_scripts": [
                "s3a-gui = s3a.__main__:mainCli",
            ]
        },
        install_requires=INSTALL_REQUIRES,
        extras_require={
            "full": ["opencv-python-headless>=4.1.2.30", "PySide2>=5.13.0"]
        },
        include_package_data=True,
        url="https://gitlab.com/ficsresearch/s3a",
        download_url="https://gitlab.com/ficsresearch/s3a",
        license="MIT",
        description="Semantic labeling made simple",
        long_description=long_description,
        long_description_content_type="text/markdown",
        keywords="semantic, labeling, image, annotation",
        author="Nathan Jessurun",
        author_email="njessurun@ufl.edu",
        classifiers=[
            "Development Status :: 3 - Alpha",  # Chose either "3 - Alpha", "4 - Beta" or "5 - Production/Stable" as the current state of your package
            "Intended Audience :: Developers",  # Define that your audience are developers
            "Topic :: Software Development :: Build Tools",
            "License :: OSI Approved :: MIT License",  # Again, pick a license
            "Programming Language :: Python :: 3",  # Specify which python versions that you want to support
        ],
        python_requires=">=3.7.*",
    )
