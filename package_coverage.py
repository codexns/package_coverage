# coding: utf-8
from __future__ import unicode_literals, division, absolute_import, print_function

import sys
import os
import re
import threading
import imp
import time
import unittest
import sublime
import sublime_plugin
import coverage
import coverage.files
import shellenv
import sqlite3
import subprocess
import webbrowser
import shutil
from datetime import datetime
from textwrap import dedent

if sys.platform == 'win32':
    from ctypes import windll, create_unicode_buffer

if sys.version_info >= (3,):
    from io import StringIO
else:
    from cStringIO import StringIO


class PackageCoverageExecCommand(sublime_plugin.WindowCommand):

    """
    Runs the tests for a package and displays the output in an output panel
    """

    def run(self, do_coverage=False):
        testable_packages = find_testable_packages()

        if not testable_packages:
            sublime.error_message(format_message('''
                Package Coverage

                No testable packages could be found
            '''))
            return

        settings = sublime.load_settings('Package Coverage.sublime-settings')
        self.coverage_database = get_setting(self.window, settings, 'coverage_database')
        self.do_coverage = do_coverage
        self.packages = testable_packages
        self.window.show_quick_panel(testable_packages, self.on_done)

    def on_done(self, index):
        """
        User input handler for selecting the package to run the tests for

        :param index:
            An integer - will be -1 if user cancelled selection, otherwise will
            be the index of the package name in the self.packages list
        """

        if index == -1:
            return

        package_name = self.packages[index]
        package_dir = os.path.join(sublime.packages_path(), package_name)

        db = None
        if self.coverage_database:
            db = open_database(self.coverage_database)

        db_results_file = None
        if self.do_coverage:
            include_dir = os.path.join(package_dir, '*.py')
            omit_dir = os.path.join(package_dir, 'dev', '*.py')
            if sys.platform == 'win32':
                short_include_dir = create_short_path(os.path.dirname(include_dir))
                if short_include_dir:
                    include_dir = [include_dir, os.path.join(short_include_dir, '*.py')]
                short_omit_dir = create_short_path(os.path.dirname(omit_dir))
                if short_omit_dir:
                    omit_dir = [omit_dir, os.path.join(short_omit_dir, '*.py')]
            cov = coverage.Coverage(include=include_dir, omit=omit_dir)
            cov.start()
            db_results_file = StringIO()
            title = 'Measuring %s Coverage' % package_name
        else:
            title = 'Running %s Tests' % package_name

        tests_module, panel = create_resources(self.window, package_name, package_dir)
        panel_queue = StringQueue()

        self.window.run_command('show_panel', {'panel': 'output.%s_tests' % package_name})
        t1 = threading.Thread(target=display_results, args=(title, panel, panel_queue, db_results_file))
        t2 = threading.Thread(target=run_tests, args=(tests_module, panel_queue))

        t1.start()
        t2.start()

        t2.join()

        if self.do_coverage:
            panel_queue.write('\n')
            cov.stop()
            cov_data = cov.get_data()
            buffer = StringIO()
            cov.report(show_missing=False, file=buffer)

            old_length = len(package_dir)
            new_length = len(package_name) + 2

            output = buffer.getvalue()

            all_short = False
            short_package_dir = None
            if sys.platform == 'win32':
                short_package_dir = create_short_path(package_dir)
                all_short = True
            new_root = '.' + os.sep + package_name
            new_output = []
            for line in output.splitlines():
                line_parts = re.split('\\s+', line)
                if len(line_parts) == 4:
                    if not short_package_dir:
                        line = line.replace(package_dir, new_root)
                    else:
                        for possible_prefix in [package_dir, short_package_dir]:
                            if line.startswith(possible_prefix):
                                line = line.replace(possible_prefix, new_root)
                                if possible_prefix == package_dir:
                                    all_short = False
                                break
                new_output.append(line)
            output = '\n'.join(new_output)

            if all_short:
                old_length = len(short_package_dir)

            # Shorten the file paths to be relative to the Packages dir
            output = output.replace('-' * old_length, '-' * new_length)
            output = output.replace('Name' + (' ' * (old_length - 4)), 'Name' + (' ' * (new_length - 4)))
            output = output.replace('TOTAL' + (' ' * (old_length - 5)), 'TOTAL' + (' ' * (new_length - 5)))

            panel_queue.write(output)

        panel_queue.write('\x04')
        t1.join()

        if self.do_coverage and db:
            try:
                is_clean = is_git_clean(package_dir)
            except (OSError) as e:
                print(format_message('''
                    Package Coverage: not saving results to coverage database
                    since an error occurred fetching the git status: %s
                ''', e.args[0]))
                return

            if not is_clean:
                print(format_message('''
                    Package Coverage: not saving results to coverage database
                    since git repository has modified files
                '''))
                return

            commit_hash, commit_date, summary = git_commit_info(package_dir)

            data_file = StringIO()
            cov_data.write_fileobj(data_file)
            data_bytes = data_file.getvalue()

            platform = {
                'win32': 'windows',
                'darwin': 'osx'
            }.get(sys.platform, 'linux')

            python_version = '%s.%s' % sys.version_info[0:2]
            if all_short:
                path_prefix = short_package_dir + os.sep
            else:
                path_prefix = package_dir + os.sep
            output = db_results_file.getvalue()

            cursor = db.cursor()
            cursor.execute("""
                INSERT INTO coverage_results (
                    project,
                    commit_hash,
                    commit_summary,
                    commit_date,
                    data,
                    platform,
                    python_version,
                    path_prefix,
                    output
                ) VALUES (
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )
            """, (
                package_name,
                commit_hash,
                summary,
                commit_date,
                data_bytes,
                platform,
                python_version,
                path_prefix,
                output
            ))
            db.commit()
            cursor.close()

            print('Package Coverage: saved results to coverage database')


class PackageCoverageSetDatabasePathCommand(sublime_plugin.WindowCommand):

    """
    Allows the user to set the path to the sqlite database to store coverage
    data inside of
    """

    def run(self):
        self.has_project_api = int(sublime.version()) >= 3000
        self.has_project = False

        if self.has_project_api:
            self.has_project = len(self.window.project_file_name()) > 0

        coverage_settings = sublime.load_settings('Package Coverage.sublime-settings')
        example_location = os.path.expanduser(os.path.join('~', 'Dropbox', 'package_coverage.sqlite'))
        existing_coverage_database = get_setting(
            self.window,
            coverage_settings,
            'coverage_database',
            example_location
        )

        if self.has_project:
            self.caption = 'Project-Specific Coverage Database Path'
        else:
            self.caption = 'User-Specific Coverage Database Path'

        self.show_input(existing_coverage_database)

    def show_input(self, initial):
        """
        Displays the input panel to allow the user to specify the coverage
        database file path

        :param initial:
            A unicode string of the path that should initially populate the
            input field
        """

        self.window.show_input_panel(
            self.caption,
            initial,
            self.on_done,
            None,
            None
        )

    def on_done(self, requested_path):
        """
        User input handler for file path to coverage database

        :param requested_path:
            A string containing the path the user entered for the coverage db
        """

        requested_dirname = os.path.dirname(requested_path)
        requested_basename = os.path.basename(requested_path)

        if requested_basename == '':
            sublime.error_message(format_message('''
                Package Coverage

                No filename provided for coverage database
            '''))
            self.show_input(requested_path)
            return

        if not os.path.exists(requested_dirname) or not os.path.dirname(requested_dirname):
            sublime.error_message(format_message(
                '''
                Package Coverage

                Folder provided for coverage database does not exist:

                %s
                ''',
                [requested_dirname]
            ))
            self.show_input(requested_path)
            return

        if self.has_project:
            project_data = self.window.project_data()
            if 'settings' not in project_data:
                project_data['settings'] = {}
            if 'Package Coverage' not in project_data['settings']:
                project_data['settings']['Package Coverage'] = {}
            project_data['settings']['Package Coverage']['coverage_database'] = requested_path
            self.window.set_project_data(project_data)
        else:
            coverage_settings = sublime.load_settings('Package Coverage.sublime-settings')
            coverage_settings.set('coverage_database', requested_path)
            sublime.save_settings('Package Coverage.sublime-settings')

        sublime.status_message('Package Coverage coverage database path saved')


class PackageCoverageDisplayReportCommand(sublime_plugin.WindowCommand):

    """
    Allows the user to pick a commit and show a report of coverage details in
    their browser
    """

    def run(self):
        testable_packages = find_testable_packages()

        if not testable_packages:
            sublime.error_message(format_message('''
                Package Coverage

                No testable packages could be found
            '''))
            return

        settings = sublime.load_settings('Package Coverage.sublime-settings')
        self.coverage_database = get_setting(self.window, settings, 'coverage_database')
        self.packages = testable_packages
        self.window.show_quick_panel(testable_packages, self.selected_package)

    def selected_package(self, index):
        """
        User input handler for user selecting package

        :param index:
            An integer index of the package name in self.packages - -1 indicates
            user cancelled operation
        """

        if index == -1:
            return

        package_name = self.packages[index]

        settings = sublime.load_settings('Package Coverage.sublime-settings')
        coverage_database = get_setting(self.window, settings, 'coverage_database')

        self.package_name = package_name
        self.coverage_database = coverage_database

        thread = threading.Thread(target=self.find_commits, args=(package_name, coverage_database))
        thread.start()

    def find_commits(self, package_name, coverage_database):
        """
        Queries the SQLite coverage database to fetch commits the use can pick
        from.

        RUNS IN A THREAD

        :param package_name:
            A unicode string of the package name

        :param coverage_database:
            The filename of the coverage database
        """

        connection = open_database(coverage_database)

        cursor = connection.cursor()
        cursor.execute("""
            SELECT
                commit_hash,
                MAX(commit_date) AS commit_date,
                MAX(commit_summary) AS commit_summary
            FROM
                coverage_results
            WHERE
                project = ?
            GROUP BY
                project,
                commit_hash
            ORDER BY
                MAX(commit_date) DESC
        """, (package_name,))

        hashes = []
        titles = []
        for row in cursor:
            title = '%s %s (%s)' % (
                row['commit_hash'],
                row['commit_summary'],
                re.sub('\\..*$', '', row['commit_date'])
            )
            hashes.append(row['commit_hash'])
            titles.append(title)

        cursor.close()
        connection.close()

        # Since this method is running in a thread, we schedule the results in
        # the main Sublime Text UI thread
        sublime.set_timeout(lambda: self.show_commits(hashes, titles), 10)

    def show_commits(self, commit_hashes, commit_titles):
        """
        Displays a list of commits with coverage results for the specified
        package

        :param commit_hashes:
            A list of unicode strings of git SHA1 hashes

        :param commit_titles:
            A list of unicode strings of commit titles for the user to pick from
        """

        if not commit_hashes:
            sublime.error_message(format_message(
                '''
                Package Coverage

                No coverage results exists for %s
                ''',
                [self.package_name]
            ))
            return

        self.hashes = commit_hashes
        self.titles = commit_titles
        self.window.show_quick_panel(commit_titles, self.selected_commit)

    def selected_commit(self, index):
        """
        User input handler for quick panel selection of commit hash

        :param index:
            An integer of the commit chosen from self.hashes - -1 indicates that
            the user cancelled the operation
        """

        if index == -1:
            return

        commit_hash = self.hashes[index]
        package_dir = os.path.join(sublime.packages_path(), self.package_name)

        args = (self.package_name, package_dir, self.coverage_database, commit_hash)
        thread = threading.Thread(target=self.generate_report, args=args)
        thread.start()

    def generate_report(self, package_name, package_dir, coverage_database, commit_hash):
        """
        Loads all of the coverage data in the database for the commit specified
        and generates an HTML report, opening it in the user's web browser

        RUNS IN A THREAD

        :param package_name:
            A unicode string of the package to generate the report for

        :param package_dir:
            A unicode string of the path to the package's directory

        :param coverage_database:
            A unicode string of the path to the SQLite coverage database

        :param commit_hash:
            A unicode string of the git SHA1 hash of the commit to display
            the results for
        """

        connection = open_database(coverage_database)

        cursor = connection.cursor()
        cursor.execute("""
            SELECT
                path_prefix,
                data,
                commit_summary
            FROM
                coverage_results
            WHERE
                project = ?
                AND commit_hash = ?
            ORDER BY
                commit_date ASC
        """, (package_name, commit_hash))

        commit_summary = None
        data = coverage.CoverageData()
        for row in cursor:
            if commit_summary is None:
                commit_summary = row['commit_summary']
            byte_string = StringIO()
            byte_string.write(row['data'])
            byte_string.seek(0)
            temp_data = coverage.CoverageData()
            temp_data.read_fileobj(byte_string)
            aliases = coverage.files.PathAliases()
            aliases.add(row['path_prefix'], package_dir + os.sep)
            data.update(temp_data, aliases)

        cursor.close()
        connection.close()

        coverage_reports_dir = os.path.join(package_dir, 'dev', 'coverage_reports')
        if not os.path.exists(coverage_reports_dir):
            os.mkdir(coverage_reports_dir)

        report_dir = os.path.join(coverage_reports_dir, commit_hash)
        if not os.path.exists(report_dir):
            os.mkdir(report_dir)

        data_file_path = os.path.join(report_dir, '.coverage')
        data.write_file(data_file_path)

        cov = coverage.Coverage(data_file=data_file_path)
        cov.load()
        title = '%s (%s %s) coverage report' % (package_name, commit_hash, commit_summary)
        cov.html_report(directory=report_dir, title=title)

        html_path = os.path.join(report_dir, 'index.html')
        if sys.platform != 'win32':
            html_path = 'file://' + html_path
        webbrowser.open_new(html_path)


class PackageCoverageCleanupReportsCommand(sublime_plugin.WindowCommand):

    """
    Deletes all HTML coverage reports currently on disk
    """

    def run(self):
        testable_packages = find_testable_packages()

        if not testable_packages:
            sublime.error_message(format_message('''
                Package Coverage

                No cleanable packages could be found
            '''))
            return

        self.packages_path = sublime.packages_path()

        cleanable_packages = []
        for testable_package in testable_packages:
            coverage_reports_dir = os.path.join(self.packages_path, testable_package, 'dev', 'coverage_reports')
            if not os.path.exists(coverage_reports_dir):
                continue
            has_dir = False
            for entry in os.listdir(coverage_reports_dir):
                if entry in set(['.', '..']):
                    continue
                if not os.path.isdir(os.path.join(coverage_reports_dir, entry)):
                    continue
                has_dir = True
                break

            if has_dir:
                cleanable_packages.append(testable_package)

        if not cleanable_packages:
            sublime.error_message(format_message('''
                Package Coverage

                No cleanable packages could be found
            '''))
            return

        self.packages = cleanable_packages
        self.window.show_quick_panel(cleanable_packages, self.selected_package)

    def selected_package(self, index):
        """
        User input handler for user selecting package

        :param index:
            An integer index of the package name in self.packages - -1 indicates
            user cancelled operation
        """

        if index == -1:
            return

        package_name = self.packages[index]

        coverage_reports_dir = os.path.join(self.packages_path, package_name, 'dev', 'coverage_reports')
        thread = threading.Thread(target=self.clean_dirs, args=(package_name, coverage_reports_dir))
        thread.start()

    def clean_dirs(self, package_name, coverage_reports_dir):
        """
        Deletes old coverage report dirs from a package's dev/coverage_reports/
        directory.

        RUNS IN A THREAD

        :param package_name:
            A unicode string of the package name

        :param coverage_reports_dir:
            A unicode string of the path to the directory to clean out

        """

        for entry in os.listdir(coverage_reports_dir):
            if entry in set(['.', '..']):
                continue
            entry_path = os.path.join(coverage_reports_dir, entry)
            if not os.path.isdir(entry_path):
                continue
            if not re.match('^[a-f0-9]{6,}$', entry):
                continue
            shutil.rmtree(entry_path)

        # Since this method is running in a thread, we schedule the result
        # notice to be run from the main UI thread
        def show_completed():
            message = 'Package Coverage: coverage reports successfully cleaned for %s' % package_name
            sublime.status_message(message)

        sublime.set_timeout(show_completed, 10)


def get_setting(window, settings, name, default=None):
    """
    Retrieves a setting from the current project, of the editor-wide Package Coverage
    settings file.

    :param window:
        The current sublime.Window object

    :param settings:
        The sublime.Settings object for Package Coverage.sublime-settings

    :param name:
        A unicode string of the name of the setting to retrieve

    :param default:
        A the value to use if the setting is not currently set

    :return:
        The setting value, or the default value
    """

    window_settings = window.active_view().settings().get('Package Coverage', {})
    if name in window_settings:
        return window_settings[name]
    return settings.get(name, default)


class StringQueue():

    """
    An output data sink for unittest that is used to fetch output to display
    in an output panel
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.queue = ''

    def write(self, data):
        self.lock.acquire()
        self.queue += data
        self.lock.release()

    def get(self):
        self.lock.acquire()
        output = self.queue
        self.queue = ''
        self.lock.release()
        return output

    def flush(self):
        pass


def create_resources(window, package_name, package_dir):
    """
    Prepares resources to run tests, including:

     1. Loading the dev/tests.py module from a package
     2. Creating a sublime.View output panel to display the results

    :param window:
        A sublime.Window object that the output panel will be created within

    :param package_name:
        A unicode string of the name of the package to test

    :param package_dir:
        A unicode string of the filesystem path to the folder containing the
        package

    :return:
        A 2-element tuple of: (tests module, sublime.View object)
    """

    panel = window.get_output_panel('%s_tests' % package_name)
    panel.settings().set('word_wrap', True)
    panel.settings().set("auto_indent", False)
    panel.settings().set("tab_width", 2)

    if sys.version_info >= (3,):
        old_path = os.getcwd()
    else:
        old_path = os.getcwdu()

    reloader_path = os.path.join(package_dir, 'dev', 'reloader.py')
    os.chdir(package_dir)

    dev_module_name = '%s.dev' % package_name
    tests_module_name = '%s.dev.tests' % package_name
    reloader_module_name = '%s.dev.reloader' % package_name

    for mod_name in [dev_module_name, tests_module_name, reloader_module_name]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    if os.path.exists(reloader_path):
        reloader_module_info = imp.find_module('reloader', ["./dev"])
        imp.load_module(reloader_module_name, *reloader_module_info)

    dev_module_info = imp.find_module('dev', ["."])
    imp.load_module(dev_module_name, *dev_module_info)

    tests_module_info = imp.find_module('tests', ["./dev"])
    tests_module = imp.load_module(tests_module_name, *tests_module_info)

    os.chdir(old_path)

    return (tests_module, panel)


def display_results(headline, panel, panel_queue, db_results_file):
    """
    Displays the results of a test run

    :param headline:
        A unicode string title to display in the output panel

    :param panel:
        A sublime.View to write the results to

    :param panel_queue:
        The StringQueue object to fetch test results from

    :param db_results_file:
        None or a StringIO object so output can be saved in the coverage
        database
    """

    # We use a function here so that chars is not redefined in the while
    # loop before the timeout get fired
    def write_to_panel(chars):
        sublime.set_timeout(lambda: panel.run_command('insert', {'characters': chars}), 10)

    write_to_panel('%s\n\n  ' % headline)

    while True:
        chars = panel_queue.get()
        wrapped_chars = chars.replace('\n', '\n  ')

        if chars == '':
            time.sleep(0.05)
            continue

        if chars[-1] == '\x04':
            chars = chars[0:-1]
            if db_results_file:
                db_results_file.write(chars)
            wrapped_chars = wrapped_chars[0:-1]
            write_to_panel(wrapped_chars)
            break

        if db_results_file:
            db_results_file.write(chars)
        write_to_panel(wrapped_chars)


def run_tests(tests_module, queue):
    """
    Executes the tests within a module and sends the output through the queue
    for display via another thread

    :param tests_module:
        The module that contains unittest.TestCase classes to execute

    :param queue:
        A StringQueue object to send the results to
    """

    suite = unittest.TestLoader().loadTestsFromModule(tests_module)
    unittest.TextTestRunner(stream=queue, verbosity=1).run(suite)


def git_commit_info(package_dir):
    """
    Get the git SHA1 hash, commit date and summary for the current git commit

    :param package_dir:
        A unicode string of the filesystem path to the folder containing the
        package

    :return:
        A tuple containing:
        [0] A unicode string of the short commit hash
        [1] A datetime.datetime object of the commit date
        [2] A unicode string of the commit message summary
    """

    startupinfo = None
    if sys.platform == 'win32':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    _, env = shellenv.get_env()
    proc = subprocess.Popen(
        ['git', 'log', '-n', '1', "--pretty=format:%h %at %s", 'HEAD'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=package_dir,
        startupinfo=startupinfo
    )
    stdout, stderr = proc.communicate()
    if stderr:
        raise OSError(stderr.decode('utf-8').strip())
    parts = stdout.decode('utf-8').strip().split(' ', 2)
    return (parts[0], datetime.utcfromtimestamp(int(parts[1])), parts[2])


def is_git_clean(package_dir):
    """
    Detects if the git repository is currently all committed

    :param package_dir:
        A unicode string of the filesystem path to the folder containing the
        package

    :return:
        A boolean - if the repository is clean
    """

    startupinfo = None
    if sys.platform == 'win32':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    _, env = shellenv.get_env()
    proc = subprocess.Popen(
        ['git', 'status', '--porcelain'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=package_dir,
        startupinfo=startupinfo
    )
    stdout, stderr = proc.communicate()
    if stderr:
        raise OSError(stderr.decode('utf-8').strip())
    return len(stdout.decode('utf-8').strip()) == 0


def open_database(coverage_database):
    """
    Opens and, if needed, initializes the coverage database for saving results

    :param coverage_database:
        A unicode string of the path to the sqlite file to use as the database

    :return:
        A Python sqlite3.Connection object
    """

    connection = sqlite3.connect(coverage_database, detect_types=sqlite3.PARSE_DECLTYPES)
    connection.row_factory = sqlite3.Row

    cursor = connection.cursor()
    cursor.execute("""
        SELECT
            name
        FROM
            sqlite_master
        WHERE
            type = 'table'
            AND name = 'coverage_results'
    """)
    if len(cursor.fetchall()) != 1:
        if sys.version_info >= (3,):
            sql_bytes = sublime.load_binary_resource('Packages/Package Coverage/coverage.sql')
        else:
            dirname = os.path.dirname(__file__)
            with open(os.path.join(dirname, 'coverage.sql'), 'rb') as f:
                sql_bytes = f.read()
        sql = sql_bytes.decode('utf-8')
        cursor.execute(sql)
    cursor.close()

    return connection


def find_testable_packages():
    """
    Returns a list of unicode strings containing testable packages

    :return:
        A list of unicode strings of package names
    """

    testable_packages = []
    packages_dir = sublime.packages_path()
    for name in os.listdir(packages_dir):
        if name[0] == '.':
            continue
        subdir_path = os.path.join(packages_dir, name)
        if not os.path.isdir(subdir_path):
            continue
        tests_path = os.path.join(subdir_path, 'dev', 'tests.py')
        if not os.path.exists(tests_path):
            continue
        testable_packages.append(name)
    return testable_packages


def format_message(string, params=None, strip=True, indent=None):
    """
    Takes a multi-line string and does the following:

     - dedents
     - removes a single leading newline if the second character is not a newline also
     - converts newlines with text before and after into a single line
     - removes a single trailing newline if the second-to-laster character is not a newline also

    :param string:
        The string to format

    :param params:
        Params to interpolate into the string

    :param strip:
        If the last newline in the string should be removed

    :param indent:
        If all lines should be indented by a set indent after being dedented

    :return:
        The formatted string
    """

    output = string

    # Only dedent if not a single-line string. This allows for
    # single-line-formatted string to be printed that include intentional
    # whitespace.
    if output.find('\n') != -1:
        output = dedent(output)

    # If the string starts with just a newline, we want to trim it because
    # it is a side-effect of the code formatting, but if there are two newlines
    # then that means we intended there to be newlines at the beginning
    if output[0] == '\n' and output[1] != '\n':
        output = output[1:]

    # Unwrap lines, taking into account bulleted lists, ordered lists and
    # underlines consisting of = signs
    if output.find('\n') != -1:
        output = re.sub('(?<=\\S)\n(?=[^ \n\t\d\*\-=])', ' ', output)

    # By default we want to trim a single trailing newline from a string since
    # that is likely from the code formatting, but that trimming is prevented
    # if strip == False, or if there are two trailing newlines, which means we
    # actually wanted whitespace at the end
    if output[-1] == '\n' and strip and output[-2] != '\n':
        output = output[0:-1]

    if params is not None:
        output = output % params

    if indent is not None:
        output = indent + output.replace('\n', '\n' + indent)

    return output


if sys.platform == 'win32':
    def create_short_path(path):
        """
        Returns the 8.3 formatted version of a path, if available

        :param path:
            A unicode string of a file path

        :return:
            None if no 8.3 path, otherwise a unicode string of the short path
        """

        short_path = path
        buf = create_unicode_buffer(512)
        if windll.kernel32.GetShortPathNameW(path, buf, len(buf)):
            short_path = buf.value
        if short_path != path:
            return short_path
        return None
