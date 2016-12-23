import os
import re
import sys
from setuptools import setup, find_packages


PY_VER = sys.version_info

if PY_VER >= (3, 4):
    pass
else:
    raise RuntimeError("Only support Python version >= 3.4")


def get_version():
    with open(os.path.join(os.path.dirname(__file__), 'gitlab_mr.py')) as f:
        for line in f.readlines():
            m = re.match(r"__version__ = '(.*?)'", line)
            if m:
                return m.group(1)
    raise ValueError('Cannot find version')


def parse_requirements(req_file_path):
    with open(req_file_path) as f:
        return f.readlines()


setup(
    name="gitlab-mr",
    version=get_version(),
    author="Alexander Koval",
    author_email="kovalidis@gmail.com",
    description=("Console utility to create gitlab merge requests."),
    license="Apache License 2.0",
    keywords="git gitlab merge-request",
    url="https://github.com/anti-social/gitlab-merge-request",
    py_modules=[
        'gitlab_mr',
    ],
    entry_points = {
        'console_scripts': ['gitlab-mr = gitlab_mr:main'],
    },
    install_requires=parse_requirements('requirements.txt'),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Environment :: Console",
        "Topic :: Software Development :: Version Control",
        "Topic :: Utilities",
    ],
)
