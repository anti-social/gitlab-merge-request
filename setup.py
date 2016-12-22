import os
from setuptools import setup, find_packages


def parse_requirements(req_file_path):
    with open(req_file_path) as req_file:
        return req_file.read().splitlines()


setup(
    name="gitlab-merge-request",
    version="0.1.0-dev",
    author="Alexander Koval",
    author_email="kovalidis@gmail.com",
    description=("Console utility to create gitlab merge requests."),
    license="Apache License 2.0",
    keywords="git gitlab merge-request",
    url="https://github.com/anti-social/gitlab-merge-request",
    py_modules=[
        'gitlab-mr',
    ],
    install_requires=parse_requirements('requirements.txt'),
    tests_requires=parse_requirements('requirements_test.txt'),
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
