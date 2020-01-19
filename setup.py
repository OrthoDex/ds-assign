import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

okpy = [] # By default, do not require okpy so Gofer-Grader can be used instead.
try:
    import client
except ImportError:
    okpy = ['okpy']

setuptools.setup(
    name="dsassign",
    version="0.0.7",
    author="John DeNero, Will Huang",
    author_email="denero@berkeley.edu, wwhuang@berkeley.edu",
    description="Jupyter notebook assignment formatting and distribution",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/okpy/jupyter-assignment",
    packages=setuptools.find_packages(),
    package_data={'dsassign': ['*.tplx']},
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    entry_points = {
        'console_scripts': [
            'dsassign = jassign.jassign:main',
            'dsassign-pdf = jassign.jassign_pdf:main'
        ]
    },
    install_requires=okpy + [
        "pyyaml", "nbformat", "ipython", "nbconvert>=5.6.0", "tqdm", "setuptools"
    ],
)
