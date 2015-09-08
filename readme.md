# Package Coverage

A Sublime Text package for running tests and reporting test coverage.

Utilizes the following Python packages, which are automatically installed into
Sublime Text via Package Control dependencies:

 - unittest
 - coverage
 - sqlite3

Any package that contains `dev/tests.py` can be tested with this package.
Coverage data from multiple machines and operating systems can be combined
together via a SQLite database the package will create and populate. Using a
solution like Dropbox to store the coverage database on makes for painless
coverage aggregation and reporting.

## Table of Contents

 - [Installation](#installation)
 - [Setup](#setup)
 - [Usage](#usage)

## Installation

Use [Package Control](https://packagecontrol.io):

 1. [Install Package Control](https://packagecontrol.io/installation)
 2. Run the *Package Control: Install Package* command from the command palette
 3. Type *Package Coverage* and select the package

## Setup

Package Coverage tries to keep things simple. It uses the `unittest` module
to find all test classes defined in `dev/tests.py` of your package directory.

The only configuration is where you would like to store the SQLite database that
tracks coverage results. One of the most convenient options is to store it in
a Dropbox or Google Drive folder that is automatically synced between machines.

 1. [Create the `dev` Directory](#create-the-dev-directory)
 2. [Write Tests in `dev/tests.py`](#write-tests-in-dev-tests-py)
 3. [Create `dev/reloader.py`](#create-dev-reloader-py)

### Create the `dev` Directory

In the root of your package directory, create a subdirectory named `dev/`.
Inside of the `dev/` folder, create a file named `__init__.py` to make the
`dev/` directory into a package.

### Write Tests in `dev/tests.py`

The file `dev/tests.py` should contain one or more `unittest.TestCase` classes.
Since `dev` is a package, you can create test classes in other files and then
use relative imports to import test classes into `dev/tests.py`.

### Create `dev/reloader.py`

For iterative development of Sublime Text packages, it is necessary to ensure
that the latest version of the Python code is running inside of Sublime Text‘s
Python interpreter.

By default, Sublime Text will automatically reload any files ending in `.py`
that are in the root of a package directory. While this works for simple
packages, it is often insufficient for more complex packages.

To handle more complex packages, create a file named `dev/reloader.py` and
paste the following boilerplate into it:

```python
# coding: utf-8
from __future__ import unicode_literals, division, absolute_import, print_function

import sys


# The name of the package
pkg_name = 'My Package'

# A list of all python files in subdirectories, listed in their dependency order
pkg_files = [
    'subdir._types',
    'subdir._osx',
    'subdir._linux',
    'subdir._win',
    'subdir',
]

if sys.version_info >= (3,):
    from imp import reload
    prefix = pkg_name + '.'
else:
    prefix = ''

for pkg_file in pkg_files:
    pkg_file_path = prefix + pkg_file
    if pkg_file_path in sys.modules:
        reload(sys.modules[pkg_file_path])

```

## Usage

Package Coverage provides the following command via the command palette:

 - [Run Tests](#run-tests)
 - [Measure Coverage](#measure-coverage)
 - [Set Database Path](#set-database-path)
 - [Display Report](#display-report)
 - [Cleanup Reports](#cleanup-reports)

### Run Tests

This command runs the tests contained within `dev/tests.py` and outputs the
results in a output panel at the bottom of Sublime Text.

Uses the quick panel to prompt the user for the package to test. *Only packages
in the `Packages/` folder with a file named `dev/tests.py` will be presented.*

### Measure Coverage

This command runs the tests in `dev/tests.py` and measures the code coverage.
If a database path has been set, it saves the results in the SQLite coverage
database.

Uses the quick panel to prompt the user for the package to test. *Only packages
in the `Packages/` folder with a file named `dev/tests.py` will be presented.*

### Set Database Path

Prompts the user to enter a full path to save the coverage database in. This
is a SQLite database for the purpose of generating HTML reports from coverage
results.

By saving the database in a sync-able filesystem such as Dropbox or Google
Drive, the results from different operating systems are then used when
generating an HTML report.

If the database path is set when a Sublime Text project is open, the database
setting will be set to the project settings in the project file. Otherwise, the
database setting will be editor-wide, and will be saved in
`Packages/User/Package Coverage.sublime-settings`.

Only results from clean git repository with no changes will be saved in the
coverage database.

### Display Report

Uses the quick panel to prompt the user to pick a package with coverage results
in the coverage database. Once a package is chosen, the user will be presented
with a list of commits that have coverage results. Choosing a commit will
compile the results from all different runs of the tests for that commit,
generate an HTML report and open it in the user's default web browser.

Generated reports are placed in the `dev/coverage_reports/` directory. It is
recommended that directory be ignored using `.gitignore` or `.hgignore`.
Exported reports are *not* automatically cleaned up, and must be purged using
the *Cleanups Reports* command.

### Cleanup Reports

Uses the quick panel to prompt the user with a list of packages that have
exported reports saved on disk. When a package is chosen, all reports are
permanently deleted.
