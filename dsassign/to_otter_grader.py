"""Generate student & autograder views of a notebook in otter-grader format."""

import copy
import json
import nbformat
import pprint
import os
import re
import shutil
import subprocess
import yaml

from collections import namedtuple

NB_VERSION = 4
BLOCK_QUOTE = "```"
COMMENT_PREFIX = "#"
TEST_HEADERS = ["TEST", "HIDDEN TEST"]
ALLOWED_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]*')
NB_VERSION = 4


def convert_to_ok(nb_path, dir, args):
    ## Preserving function name till tests are up
    """Convert a master notebook to an ok notebook, tests dir, and .ok file.

    nb -- Path
    endpoint -- OK endpoint for notebook submission (e.g., cal/data100/sp19)
    dir -- Path
    """
    ok_nb_path = dir / nb_path.name
    tests_dir = dir / 'tests'
    os.makedirs(tests_dir, exist_ok=True)
    open(tests_dir / '__init__.py', 'a').close()

    nb = nbformat.read(open(nb_path), NB_VERSION)
    ok_cells, manual_questions = gen_ok_cells(nb['cells'], tests_dir)
    dot_ok_name = gen_dot_ok(ok_nb_path, args.endpoint)
    init = gen_init_cell(dot_ok_name)

    nb['cells'] = [init] + ok_cells
    if not args.no_submit_cell:
        nb['cells'] += gen_submit_cells(nb_path, manual_questions, args.instructions)
    remove_output(nb)

    with open(ok_nb_path, 'w') as f:
        nbformat.write(nb, f, NB_VERSION)
    return ok_nb_path


def gen_dot_ok(notebook_path, endpoint):
    """Generate .ok file and return its name."""
    assert notebook_path.suffix == '.ipynb', notebook_path
    ok_path = notebook_path.with_suffix('.ok')
    name = notebook_path.stem
    src = [notebook_path.name]
    with open(ok_path, 'w') as out:
        json.dump({
            "name": name,
            "endpoint": endpoint,
            "src": src,
            "tests": {
                "tests/q*.py": "ok_test"
            },
            "protocols": [
                "file_contents",
                "grading",
                "backup"
            ]
            }, out)
    return ok_path.name


def gen_init_cell(dot_ok_name):
    """Generate a cell to initialize ok object."""
    cell = nbformat.v4.new_code_cell()
    cell.source = ("# Initialize Otter Grader\n"
    "import otter\n"
    "grader = otter.Notebook()")
    lock(cell)
    return cell


def gen_submit_cells(nb_path, manual_questions, instruction_text):
    """Generate submit cells."""
    instructions = nbformat.v4.new_markdown_cell()
    # TODO(denero) Force save.
    instructions.source = "# Submit\nMake sure you have run all cells in your notebook in order before running the cell below, so that all images/graphs appear in the output.\n**Please save before submitting!**"
    if instruction_text:
        instructions.source += '\n\n' + instruction_text
    if manual_questions:
        n = len(manual_questions)
        instructions.source += f'\n\n<!-- EXPECT {n} EXPORTED QUESTIONS -->'

    submit = nbformat.v4.new_code_cell()
    source_lines = ["# Save your notebook first, then run this cell to create a pdf for your reference."]
    if manual_questions:
        source_lines.append("import dsassign.to_pdf")
        source_lines.append("dsassign.to_pdf.generate_pdf('{}', '{}')".format(
            nb_path.name, nb_path.with_suffix('.pdf').name))
    # source_lines.append("ok.submit()")
    submit.source = "\n".join(source_lines)
    lock(instructions)
    lock(submit)
    return [instructions, submit]


def gen_ok_cells(cells, tests_dir):
    """Generate notebook cells for the OK version of a master notebook.

    Returns:
        (ok_cells, list of manual question names)
    """
    ok_cells = []
    question = {}
    processed_response = False
    tests = []
    manual_questions = []

    for cell in cells:
        if question and not processed_response:
            assert not is_question_cell(cell), cell
            assert not is_test_cell(cell), cell
            ok_cells.append(cell)
            processed_response = True
        elif question and processed_response and is_test_cell(cell):
            tests.append(read_test(cell))
        else:
            if question and processed_response:
                # The question is over
                if tests:
                    ok_cells.append(gen_test_cell(question, tests, tests_dir))
                question, processed_response, tests = {}, False, []
            if is_question_cell(cell):
                question = read_question_metadata(cell)
                manual = question.get('manual', False)
                format = question.get('format', '')
                if manual:
                    manual_questions.append(question['name'])
                ok_cells.append(gen_question_cell(cell, manual, format))
            else:
                assert not is_test_cell(cell), 'Test outside of a question: ' + str(cell)
                ok_cells.append(cell)

    if tests:
        ok_cells.append(gen_test_cell(question, tests, tests_dir))

    return ok_cells, manual_questions


def get_source(cell):
    """Get the source code of a cell in a way that works for both nbformat and json."""
    source = cell['source']
    if isinstance(source, str):
        return cell['source'].split('\n')
    elif isinstance(source, list):
        return [line.strip('\n') for line in source]
    assert 'unknown source type', type(source)


def is_question_cell(cell):
    """Whether cell contains BEGIN QUESTION in a block quote."""
    if cell['cell_type'] != 'markdown':
        return False
    return find_question_spec(get_source(cell)) is not None


def find_question_spec(source):
    """Return line number of the BEGIN QUESTION line or None."""
    block_quotes = [i for i, line in enumerate(source) if
                    line == BLOCK_QUOTE]
    assert len(block_quotes) % 2 == 0, 'cannot parse ' + str(source)
    begins = [block_quotes[i] + 1 for i in range(0, len(block_quotes), 2) if
              source[block_quotes[i]+1].strip(' ') == 'BEGIN QUESTION']
    assert len(begins) <= 1, 'multiple questions defined in ' + str(source)
    return begins[0] if begins else None


def gen_question_cell(cell, manual, format):
    """Return the cell with metadata hidden in an HTML comment."""
    cell = copy.deepcopy(cell)
    source = get_source(cell)
    begin_question_line = find_question_spec(source)
    start = begin_question_line - 1
    assert source[start].strip() == BLOCK_QUOTE
    end = begin_question_line
    while source[end].strip() != BLOCK_QUOTE:
        end += 1
    source[start] = "<!--"
    source[end] = "-->"
    if manual and format:
        source.append(f'<!-- EXPORT TO PDF format:{format} -->')
    elif manual:
        source.append('<!-- EXPORT TO PDF -->')
    cell['source'] = '\n'.join(source)
    lock(cell)
    return cell


def read_question_metadata(cell):
    """Return question metadata from a question cell."""
    source = get_source(cell)
    begin_question_line = find_question_spec(source)
    i, lines = begin_question_line + 1, []
    while source[i].strip() != BLOCK_QUOTE:
        lines.append(source[i])
        i = i + 1
    metadata = yaml.load('\n'.join(lines))
    assert ALLOWED_NAME.match(metadata.get('name', '')), metadata
    return metadata


def is_test_cell(cell):
    """Return whether it's a code cell containing a test."""
    if cell['cell_type'] != 'code':
        return False
    source = get_source(cell)
    delimiters = COMMENT_PREFIX + ' \n'
    return source and source[0].strip(delimiters) in TEST_HEADERS


Test = namedtuple('Test', ['input', 'output', 'hidden'])


def read_test(cell):
    """Return the contents of a test as an (input, output, hidden) tuple."""
    hidden = 'HIDDEN' in get_source(cell)[0]
    output = ''
    for o in cell['outputs']:
        output += ''.join(o.get('text', ''))
        results = o.get('data', {}).get('text/plain')
        if results and isinstance(results, list):
            output += results[0]
        elif results:
            output += results
    return Test('\n'.join(get_source(cell)[1:]), output, hidden)


def write_test(path, test):
    """Write an OK test file."""
    with open(path, 'w') as f:
        f.write('test = ')
        pprint.pprint(test, f, indent=4)


def gen_test_cell(question, tests, tests_dir):
    """Return a test cell."""
    cell = nbformat.v4.new_code_cell()
    cell.source = ['grader.check("{}")'.format(question['name'])] # Remove semi colon so that students can see what the expected answer should be
    suites = [gen_suite(tests)]
    test = {
        'name': question['name'],
        'points': question.get('points', 1),
        'suites': suites,
    }
    write_test(tests_dir / (question['name'] + '.py'), test)
    lock(cell)
    return cell


def gen_suite(tests):
    """Generate an ok test suite for a test."""
    cases = [gen_case(test) for test in tests]
    return  {
      'cases': cases,
      'scored': True,
      'setup': '',
      'teardown': '',
      'type': 'doctest'
    }


def gen_case(test):
    """Generate an ok test case for a test."""
    # TODO(denero) This should involve a Python parser, but it doesn't...
    code_lines = []
    for line in test.input.split('\n'):
        if line.startswith(' '):
            code_lines.append('... ' + line)
        else:
            code_lines.append('>>> ' + line)
    # Suppress intermediate output from evaluation
    for i in range(len(code_lines) - 1):
        if code_lines[i+1].startswith('>>>') and len(code_lines[i].strip()) > 3:
            code_lines[i] += ';'
    code_lines.append(test.output)
    return {
        'code': '\n'.join(code_lines),
        'hidden': test.hidden,
        'locked': False
    }


solution_assignment_re = re.compile('(\\s*[a-zA-Z0-9_ ]*=)(.*) #[ ]?SOLUTION')
def solution_assignment_sub(match):
    prefix = match.group(1)
    sol = match.group(2)
    return prefix + ' ...'


solution_line_re = re.compile('(\\s*)([^#\n]+) #[ ]?SOLUTION')
def solution_line_sub(match):
    prefix = match.group(1)
    return prefix + '...'


text_solution_line_re = re.compile(r'\s*\*\*SOLUTION:?\*\*:?.*')
begin_solution_re = re.compile(r'(\s*)# BEGIN SOLUTION( NO PROMPT)?')
skip_suffixes = ['# SOLUTION NO PROMPT', '# BEGIN PROMPT', '# END PROMPT']


SUBSTITUTIONS = [
    (solution_assignment_re, solution_assignment_sub),
    (solution_line_re, solution_line_sub),
]


def replace_solutions(lines):
    """Replace solutions in lines, a list of strings."""
    if text_solution_line_re.match(lines[0]):
        return ['*Write your answer here, replacing this text.*']
    stripped = []
    solution = False
    for line in lines:
        if any(line.endswith(s) for s in skip_suffixes):
            continue
        if solution and not line.endswith('# END SOLUTION'):
            continue
        if line.endswith('# END SOLUTION'):
            assert solution, 'END SOLUTION without BEGIN SOLUTION in ' + str(lines)
            solution = False
            continue
        begin_solution = begin_solution_re.match(line)
        if begin_solution:
            assert not solution, 'Nested BEGIN SOLUTION in ' + str(lines)
            solution = True
            if not begin_solution.group(2):
                line = begin_solution.group(1) + '...'
            else:
                continue
        for exp, sub in SUBSTITUTIONS:
            m = exp.match(line)
            if m:
                line = sub(m)
        stripped.append(line)
    assert not solution, 'BEGIN SOLUTION without END SOLUTION in ' + str(lines)
    return stripped


def strip_solutions(original_nb_path, stripped_nb_path):
    """Write a notebook with solutions stripped."""
    nb = nbformat.read(open(original_nb_path), NB_VERSION)
    for cell in nb['cells']:
        cell['source'] = '\n'.join(replace_solutions(get_source(cell)))
    with open(stripped_nb_path, 'w') as f:
        nbformat.write(nb, f, NB_VERSION)


def remove_output(nb):
    """Remove all outputs."""
    for cell in nb['cells']:
        if 'outputs' in cell:
            cell['outputs'] = []


def lock(cell):
    m = cell['metadata']
    m["editable"] = False
    m["deletable"] = False


def remove_hidden_tests(test_dir):
    """Rewrite test files to remove hidden tests."""
    for f in test_dir.iterdir():
        if f.name == '__init__.py' or f.suffix != '.py':
            continue
        locals = {}
        exec(open(f).read(), globals(), locals)
        test = locals['test']
        for suite in test['suites']:
            for i, case in list(enumerate(suite['cases']))[::-1]:
                if case['hidden']:
                    suite['cases'].pop(i)
        write_test(f, test)


def gen_otter_grader_views(master_nb, result_dir, args):
    """Generate student and autograder views.

    master_nb -- Dict of master notebook JSON
    result_dir -- Path to the result directory
    """
    autograder_dir = result_dir / 'autograder'
    student_dir = result_dir / 'student'
    os.makedirs(autograder_dir, exist_ok=True)
    ok_nb_path = convert_to_ok(master_nb, autograder_dir, args)
    shutil.copytree(autograder_dir, student_dir)
    student_nb_path = student_dir / ok_nb_path.name
    os.remove(student_nb_path)
    strip_solutions(ok_nb_path, student_nb_path)
    remove_hidden_tests(student_dir / 'tests')



