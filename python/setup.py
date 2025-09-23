#!/usr/bin/env python3

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="capnweb",
    version="0.1.0",
    author="Kenton Varda",
    author_email="kenton@cloudflare.com",
    description="Python implementation of Cap'n Web: A capability-based RPC system",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/cloudflare/capnweb",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.8",
    install_requires=[
        "websockets>=11.0",
        "aiohttp>=3.8",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "pytest-asyncio>=0.21",
            "black",
            "mypy",
        ],
    },
)