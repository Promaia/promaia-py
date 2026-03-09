#!/usr/bin/env python3
"""Setup script for the maia package."""

from setuptools import setup, find_packages
import os

# Read the contents of your README file
this_directory = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(this_directory, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='promaia',
    version='0.1.0',
    description='Notion Integration & Automation Framework',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='Promaia Team',
    packages=['promaia'],
    package_dir={'promaia': 'promaia'},
    include_package_data=True,
    install_requires=[
        'aiohappyeyeballs==2.6.1',
        'aiohttp==3.11.18',
        'anthropic==0.64.0',
        'fastapi==0.115.12',
        'google-generativeai',
        'google-auth==2.35.0',
        'google-auth-oauthlib==1.2.1',
        'google-api-python-client==2.158.0',
        'httpx==0.28.1',
        'notion-client==2.2.1',
        'openai==1.76.0',
        'pydantic==2.11.3',
        'python-dotenv==1.0.1',
        'resend',
        'requests==2.32.3',
        'rich==13.7.0',
        'uvicorn==0.34.2',
    ],
    entry_points={
        'console_scripts': [
            'maia=promaia.__main__:main',
        ],
    },
    python_requires='>=3.8',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
    ],
) 