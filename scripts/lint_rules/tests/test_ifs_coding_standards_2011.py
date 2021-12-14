import importlib
from pathlib import Path
import pytest

from loki import FP, HAVE_FP, Sourcefile
from loki.lint import Reporter, Linter, DefaultHandler


pytestmark = pytest.mark.skipif(not HAVE_FP,
                                reason='Fparser frontend not available')


@pytest.fixture(scope='module', name='rules')
def fixture_rules():
    rules = importlib.import_module('lint_rules.ifs_coding_standards_2011')
    return rules


@pytest.fixture(scope='module', name='frontend')
def fixture_frontend():
    """Choose frontend to use (Linter currently relies exclusively on Fparser)"""
    return FP


def run_linter(sourcefile, rule_list, config=None, handlers=None):
    """
    Run the linter for the given source file with the specified list of rules.
    """
    reporter = Reporter(handlers)
    linter = Linter(reporter, rules=rule_list, config=config)
    linter.check(sourcefile)
    return linter


@pytest.mark.parametrize('frontend, nesting_depth, lines', [
    (FP, 3, []),
    (FP, 2, [6, 12, 16, 22, 28, 35]),
    (FP, 1, [5, 6, 10, 12, 16, 22, 27, 28, 34, 35])])
def test_code_body_messages(rules, frontend, nesting_depth, lines):
    '''
    Test the number and content of messages generated by CodeBodyRule
    for different nesting depths.
    '''
    fcode = """
subroutine routine_nesting(a, b, c, d, e)
integer, intent(in) :: a, b, c, d, e

if (a > 3) then
    if (b > 2) then
        if (c > 1) then
            print *, 'if-if-if'
        end if
    end if
    select case (d)
        case (0)
            if (e == 0) then
                print *, 'if-case-if'
            endif
        case (1:3)
            if (e == 0) then
                print *, 'if-range-if'
            else
                print *, 'if-range-else'
            endif
        case default
            if (e == 0) then
                print *, 'if-default-if'
            endif
    end select
elseif (a == 3) then
    if (b > 2) then
        if (c > 1) then
            print *, 'elseif-if-if'
        end if
    end if
else
    if (e == 0) print *, 'else-inlineif'
    if (b > 2) then
        if (c > 1) then
            print *, 'else-if-if'
        end if
    end if
end if
end subroutine routine_nesting
    """.strip()
    source = Sourcefile.from_source(fcode, frontend=frontend)
    messages = []
    handler = DefaultHandler(target=messages.append)
    config = {'CodeBodyRule': {'max_nesting_depth': nesting_depth}}
    _ = run_linter(source, [rules.CodeBodyRule], config=config, handlers=[handler])

    assert len(messages) == len(lines)
    keywords = ('CodeBodyRule', '[1.3]')
    assert all(all(keyword in msg for keyword in keywords) for msg in messages)

    for msg, ref_line in zip(messages, lines):
        assert 'limit of {}'.format(nesting_depth) in msg
        assert 'l. {}'.format(ref_line) in msg


def test_module_naming(rules, frontend):
    '''Test file and modules for checking that naming is correct and matches each other.'''
    fcode = """
! This is ok
module module_naming_mod
integer foo
contains
subroutine bar
integer foobar
end subroutine bar
end module module_naming_mod

! This should complain about wrong file name
module MODULE_NAMING_UPPERCASE_MOD
integer foo
contains
subroutine bar
integer foobar
end subroutine bar
end module MODULE_NAMING_UPPERCASE_MOD

! This should complain about wrong module and file name
module module_naming
integer baz
end module module_naming
    """.strip()
    source = Sourcefile.from_source(fcode, frontend=frontend)
    # We don't actually write the file but simply set the filename to something sensible
    for m in source.modules:
        m.source.file = str(Path(__file__).parent / 'module_naming_mod.f90')
    messages = []
    handler = DefaultHandler(target=messages.append)
    _ = run_linter(source, [rules.ModuleNamingRule], handlers=[handler])

    assert len(messages) == 3
    keywords = ('ModuleNamingRule', '[1.5]')
    assert all(all(keyword in msg for keyword in keywords) for msg in messages)

    assert all('"module_naming' in msg.lower() for msg in messages)
    assert all(keyword in messages[0] for keyword in ('module_naming_mod.f90', 'filename'))
    assert all(keyword in messages[1] for keyword in ('"_mod"', 'Name of module'))
    assert all(keyword in messages[2] for keyword in ('module_naming_mod.f90', 'filename'))


def test_dr_hook_okay(rules, frontend):
    fcode = """
subroutine routine_okay
use yomhook, only: lhook, dr_hook
real(kind=jprb) :: zhook_handle

! Comments are non-executable statements

if (lhook) then
#define foobar
  call dr_hook('routine_okay', 0, zhook_handle)
end if

print *, "Foo bar"

if (lhook) call dr_hook('routine_okay', 1, zhook_handle)

! Comments are non-executable statements

contains

subroutine routine_contained_okay
real(kind=jprb) :: zhook_handle

! CPP directives should be ignored
#ifndef _some_macro

if (lhook) call dr_hook('routine_okay%routine_contained_okay', 0, zhook_handle)

print *, "Foo bar"

if (lhook) call dr_hook('routine_okay%routine_contained_okay', 1, zhook_handle)

! CPP directives should be ignored
#endif
end subroutine routine_contained_okay
end subroutine routine_okay
    """.strip()
    source = Sourcefile.from_source(fcode, frontend=frontend)
    messages = []
    handler = DefaultHandler(target=messages.append)
    _ = run_linter(source, [rules.DrHookRule], handlers=[handler])
    assert len(messages) == 0


def test_dr_hook_routine(rules, frontend):
    fcode = """
subroutine routine_not_okay_a
use yomhook, only: lhook, dr_hook
real(kind=jprb) :: zhook_handle

! Error: no conditional IF(LHOOK)
! Error: no zhook_handle (Not detected because call not found)
call dr_hook('routine_not_okay_a', 0)

print *, "Foo bar"

! Error: subroutine name not in string argument
if (lhook) call dr_hook('foobar', 1, zhook_handle)
end subroutine routine_not_okay_a


subroutine routine_not_okay_b
use yomhook, only: lhook, dr_hook
real(kind=jprb) :: zhook_handle

! Error: second argument is not 0 or 1
if (lhook) call dr_hook('routine_not_okay_b', 2, zhook_handle)

print *, "Foo bar"

! Error: third argument is not zhook_handle
if (lhook) call dr_hook('routine_not_okay_b', 1)
end subroutine routine_not_okay_b


subroutine routine_not_okay_c
use yomhook, only: lhook, dr_hook
real(kind=jprb) :: zhook_handle
real(kind=jprb) :: red_herring

red_herring = 1.0

! Error: Executable statement before call to dr_hook
if (lhook) call dr_hook('routine_not_okay_c', 2, zhook_handle)

print *, "Foo bar"

! Error: Executable statement after call to dr_hook
if (lhook) then
  call dr_hook('routine_not_okay_c', 1, zhook_handle)
  red_herring = 2.0
end if

end subroutine routine_not_okay_c


subroutine routine_not_okay_d
use yomhook, only: lhook, dr_hook
real(kind=jprb) :: zhook_handle
real(kind=jprb) :: red_herring

! Error: First call to dr_hook is missing

red_herring = 1.0
print *, "Foo bar"

if (lhook) call dr_hook('routine_not_okay_d', 1, zhook_handle)

end subroutine routine_not_okay_d


subroutine routine_not_okay_e
use yomhook, only: lhook, dr_hook
real(kind=jprb) :: zhook_handle
real(kind=jprb) :: red_herring

if (lhook) call dr_hook('routine_not_okay_e', 0, zhook_handle)

red_herring = 1.0
print *, "Foo bar"

! Error: Last call to dr_hook is missing

contains

subroutine routine_contained_not_okay
use yomhook, only: lhook, dr_hook
real(kind=jprb) :: zhook_handle
real(kind=jprb) :: red_herring

if (lhook) call dr_hook('routine_not_okay_e%routine_contained_not_okay', 0, zhook_handle)

red_herring = 1.0
print *, "Foo bar"

! Error: String argument is not "<parent routine>%<contained routine>"
if (lhook) call dr_hook('routine_contained_not_okay', 1, zhook_handle)
end subroutine routine_contained_not_okay
end subroutine routine_not_okay_e
    """.strip()
    source = Sourcefile.from_source(fcode, frontend=frontend)
    messages = []
    handler = DefaultHandler(target=messages.append)
    _ = run_linter(source, [rules.DrHookRule], handlers=[handler])

    assert len(messages) == 9
    keywords = ('DrHookRule', 'DR_HOOK', '[1.9]')
    assert all(all(keyword in msg for keyword in keywords) for msg in messages)

    assert all('First executable statement must be call to DR_HOOK' in messages[i] for i in [0, 4, 6])
    assert all('Last executable statement must be call to DR_HOOK' in messages[i] for i in [5, 7])
    assert all('String argument to DR_HOOK call should be "' in messages[i] for i in [1, 8])
    assert 'Second argument to DR_HOOK call should be "0"' in messages[2]
    assert 'Third argument to DR_HOOK call should be "ZHOOK_HANDLE"' in messages[3]

    # Later lines come first as modules are checked before subroutines
    assert '(l. 12)' in messages[1]
    assert '(l. 21)' in messages[2]
    assert '(l. 26)' in messages[3]
    assert '(l. 91)' in messages[8]

    assert all('routine_not_okay_{}'.format(letter) in messages[i]
               for letter, i in (('a', 0), ('c', 4), ('c', 5), ('d', 6), ('e', 7)))


def test_dr_hook_module(rules, frontend):
    fcode = """
module some_mod

contains

subroutine mod_routine_okay
use yomhook, only: lhook, dr_hook
real(kind=jprb) :: zhook_handle

if (lhook) call dr_hook('some_mod:mod_routine_okay', 0, zhook_handle)
print *, "Foo bar"
if (lhook) call dr_hook('some_mod:mod_routine_okay', 1, zhook_handle)

contains

subroutine mod_contained_routine_okay
use yomhook, only: lhook, dr_hook
real(kind=jprb) :: zhook_handle

if (lhook) call dr_hook('some_mod:mod_routine_okay%mod_contained_routine_okay', 0, zhook_handle)
print *, "Foo bar"
if (lhook) call dr_hook('some_mod:mod_routine_okay%mod_contained_routine_okay', 1, zhook_handle)
end subroutine mod_contained_routine_okay
end subroutine mod_routine_okay

subroutine mod_routine_not_okay
use yomhook, only: lhook, dr_hook
real(kind=jprb) :: zhook_handle

! Error: String argument does not contain module name
if (lhook) call dr_hook('mod_routine_okay', 0, zhook_handle)
print *, "Foo bar"
if (lhook) call dr_hook('some_mod:mod_routine_not_okay', 1, zhook_handle)

contains

subroutine mod_contained_routine_not_okay
use yomhook, only: lhook, dr_hook
real(kind=jprb) :: zhook_handle

! Error: String argument does not contain module name
if (lhook) call dr_hook('mod_routine_not_okay%mod_contained_routine_not_okay', 0, zhook_handle)
print *, "Foo bar"
! Error: String argument does not contain parent routine name
! Error: Second argument is not 0 or 1
if (lhook) call dr_hook('some_mod:mod_contained_routine_not_okay', 8, zhook_handle)
end subroutine mod_contained_routine_not_okay
end subroutine mod_routine_not_okay
end module some_mod
    """.strip()
    source = Sourcefile.from_source(fcode, frontend=frontend)
    messages = []
    handler = DefaultHandler(target=messages.append)
    _ = run_linter(source, [rules.DrHookRule], handlers=[handler])

    assert len(messages) == 4
    keywords = ('DrHookRule', 'DR_HOOK', '[1.9]')
    assert all(all(keyword in msg for keyword in keywords) for msg in messages)

    assert all('String argument to DR_HOOK call should be "' in messages[i] for i in [0, 1, 2])
    assert 'Second argument to DR_HOOK call should be "1"' in messages[3]

    # Later lines come first as modules are checked before subroutines
    assert '(l. 30)' in messages[0]
    assert '(l. 41)' in messages[1]
    assert '(l. 45)' in messages[2]
    assert '(l. 45)' in messages[3]


@pytest.mark.parametrize('frontend, max_num_statements, passes', [
    (FP, 10, True),
    (FP, 4, True),
    (FP, 3, False)])
def test_limit_subroutine_stmts(rules, frontend, max_num_statements, passes):
    '''Test for different maximum allowed number of executable statements and
    content of messages generated by LimitSubroutineStatementsRule.'''
    fcode = """
subroutine routine_limit_statements()
integer :: a, b, c, d, e

! Non-exec statements
#define some_macro
print *, 'Hello world!'

associate (aa=>a)
    aa = 1
    b = 2
    call some_routine(c, e)
    d = 4
end associate

end subroutine routine_limit_statements
    """.strip()
    source = Sourcefile.from_source(fcode, frontend=frontend)
    messages = []
    handler = DefaultHandler(target=messages.append)
    config = {'LimitSubroutineStatementsRule': {'max_num_statements': max_num_statements}}
    _ = run_linter(source, [rules.LimitSubroutineStatementsRule], config=config, handlers=[handler])

    assert len(messages) == (0 if passes else 1)
    keywords = ('LimitSubroutineStatementsRule', '[2.2]', '4', str(max_num_statements),
                'routine_limit_statements')
    assert all(all(keyword in msg for keyword in keywords) for msg in messages)


@pytest.mark.parametrize('frontend, max_num_arguments, passes', [
    (FP, 10, True),
    (FP, 8, True),
    (FP, 7, False),
    (FP, 1, False)])
def test_max_dummy_args(rules, frontend, max_num_arguments, passes):
    '''Test for different maximum allowed number of dummy arguments and
    content of messages generated by MaxDummyArgsRule.'''
    fcode = """
subroutine routine_max_dummy_args(a, b, c, d, e, f, g, h)
integer, intent(in) :: a, b, c, d, e, f, g, h

print *, a, b, c, d, e, f, g, h
end subroutine routine_max_dummy_args
    """.strip()
    source = Sourcefile.from_source(fcode, frontend=frontend)
    messages = []
    handler = DefaultHandler(target=messages.append)
    config = {'MaxDummyArgsRule': {'max_num_arguments': max_num_arguments}}
    _ = run_linter(source, [rules.MaxDummyArgsRule], config=config, handlers=[handler])

    assert len(messages) == (0 if passes else 1)
    keywords = ('MaxDummyArgsRule', '[3.6]', '8', str(max_num_arguments), 'routine_max_dummy_args')
    assert all(all(keyword in msg for keyword in keywords) for msg in messages)


def test_mpl_cdstring(rules, frontend):
    fcode = """
subroutine routine_okay
use mpl_module
call mpl_init(cdstring='routine_okay')
end subroutine routine_okay

subroutine routine_also_okay
use MPL_MODULE
call MPL_INIT(KPROCS=5, CDSTRING='routine_also_okay')
end subroutine routine_also_okay

subroutine routine_not_okay
use mpl_module
call mpl_init
end subroutine routine_not_okay

subroutine routine_also_not_okay
use MPL_INIT
call MPL_INIT(kprocs=5)
end subroutine routine_also_not_okay
    """.strip()
    source = Sourcefile.from_source(fcode, frontend=frontend)
    messages = []
    handler = DefaultHandler(target=messages.append)
    _ = run_linter(source, [rules.MplCdstringRule], handlers=[handler])
    assert len(messages) == 2
    assert all('[3.12]' in msg for msg in messages)
    assert all('MplCdstringRule' in msg for msg in messages)
    assert all('"CDSTRING"' in msg for msg in messages)
    assert all('MPL_INIT' in msg.upper() for msg in messages)
    assert sum('(l. 13)' in msg for msg in messages) == 1
    assert sum('(l. 18)' in msg for msg in messages) == 1


def test_implicit_none(rules, frontend):
    fcode = """
subroutine routine_okay
implicit none
integer :: a
a = 5
contains
subroutine contained_routine_okay
integer :: b
b = 5
end subroutine contained_routine_okay
end subroutine routine_okay

module mod_okay
implicit none
contains
subroutine contained_mod_routine_okay
integer :: a
a = 5
end subroutine contained_mod_routine_okay
end module mod_okay

subroutine routine_not_okay
! This should report
integer :: a
a = 5
contains
subroutine contained_not_okay_routine_okay
implicit none
integer :: b
b = 5
end subroutine contained_not_okay_routine_okay
end subroutine routine_not_okay

module mod_not_okay
contains
subroutine contained_mod_not_okay_routine_okay
implicit none
integer :: a
a = 5
end subroutine contained_mod_not_okay_routine_okay
end module mod_not_okay

subroutine routine_also_not_okay
! This should report
integer :: a
a = 5
contains
subroutine contained_routine_not_okay
! This should report
integer :: b
b = 5
end subroutine contained_routine_not_okay
end subroutine routine_also_not_okay

module mod_also_not_okay
contains
subroutine contained_mod_routine_not_okay
! This should report
integer :: a
a = 5
contains
subroutine contained_contained_routine_not_okay
! This should report
integer :: b
b = 5
end subroutine contained_contained_routine_not_okay
end subroutine contained_mod_routine_not_okay
end module mod_also_not_okay
    """
    source = Sourcefile.from_source(fcode, frontend=frontend)
    messages = []
    handler = DefaultHandler(target=messages.append)
    _ = run_linter(source, [rules.ImplicitNoneRule], handlers=[handler])

    assert len(messages) == 5
    assert all('"IMPLICIT NONE"' in msg for msg in messages)
    assert all('[4.4]' in msg for msg in messages)
    assert sum('"routine_not_okay"' in msg for msg in messages) == 1
    assert sum('"routine_also_not_okay"' in msg for msg in messages) == 1
    assert sum('"contained_routine_not_okay"' in msg for msg in messages) == 1
    assert sum('"contained_mod_routine_not_okay"' in msg for msg in messages) == 1
    assert sum('"contained_contained_routine_not_okay"' in msg for msg in messages) == 1


def test_explicit_kind(rules, frontend):
    fcode = """
subroutine routine_okay
use some_type_module, only : jpim, jprb
integer(kind=jpim) :: i, j
real(kind=jprb) :: a(3), b

i = 1_JPIM + 7_JPIM
j = 2_JPIM
a(1:3) = 3._JPRB
b = 4.0_JPRB
do j=1,3
    a(j) = real(j)
end do
end subroutine routine_okay

subroutine routine_not_okay
integer :: i
integer(kind=1) :: j
real :: a(3)
real(kind=8) :: b

i = 1 + 7
j = 2
a(1:3) = 3e0
b = 4.0 + 5d0 + 6._4
end subroutine routine_not_okay
    """.strip()
    source = Sourcefile.from_source(fcode, frontend=frontend)
    messages = []
    handler = DefaultHandler(target=messages.append)
    # Need to include INTEGER constants in config as (temporarily) removed from defaults
    config = {'ExplicitKindRule': {'constant_types': ['REAL', 'INTEGER']}}
    _ = run_linter(source, [rules.ExplicitKindRule], config=config, handlers=[handler])

    # Note: This creates one message too many, namely the literal '4' in the constant
    # 6._4. This is because we represent the kind parameter as an expression (which can be
    # an imported name, for example). Since '4' (or any other literals) are not allowed kind
    # values in IFS this should not be a problem in practice: it will simply create an
    # additional spurious error in that case
    assert len(messages) == 12
    assert all('[4.7]' in msg for msg in messages)
    assert all('ExplicitKindRule' in msg for msg in messages)

    # Keywords to search for in the messages as tuples:
    # ('var name' or 'literal', 'line number', 'invalid kind value' or None)
    keywords = (
        # Declarations
        ('i', '16', None), ('j', '17', '1'), ('a(3)', '18', None), ('b', '19', '8'),
        # Literals
        ('1', '21', None), ('7', '21', None), ('2', '22', None), ('3e0', '23', None),
        ('4.0', '24', None), ('5d0', '24', None), ('4', '24', None), ('6._4', '24', '4')
    )
    for keys, msg in zip(keywords, messages):
        assert all(kw in msg for kw in keys if kw is not None)


def test_banned_statements_default(rules, frontend):
    '''Test for banned statements with default.'''
    fcode = """
subroutine banned_statements()
integer :: dummy

dummy = 5
call foobar(dummy)
go to 100
print *, dummy
100 continue
end subroutine banned_statements
    """
    source = Sourcefile.from_source(fcode, frontend=frontend)
    messages = []
    handler = DefaultHandler(target=messages.append)
    _ = run_linter(source, [rules.BannedStatementsRule], handlers=[handler])

    assert len(messages) == 3
    keywords = ('BannedStatementsRule', '[4.11]')
    assert all(all(keyword in msg for keyword in keywords) for msg in messages)
    banned_statements = ('GO TO', 'PRINT', 'CONTINUE')
    assert all(any(keyword in msg for keyword in banned_statements) for msg in messages)


@pytest.mark.parametrize('frontend, banned_statements, passes', [
    (FP, [], True),
    (FP, ['GO TO'], False),
    (FP, ['GO TO', 'RETURN'], False),
    (FP, ['RETURN'], True)])
def test_banned_statements_config(rules, frontend, banned_statements, passes):
    '''Test for banned statements with custom config.'''
    fcode = """
subroutine banned_statements()
integer :: dummy

dummy = 5
call foobar(dummy)
go to 100
print *, dummy
100 continue
end subroutine banned_statements
    """
    source = Sourcefile.from_source(fcode, frontend=frontend)
    messages = []
    handler = DefaultHandler(target=messages.append)
    config = {'BannedStatementsRule': {'banned': banned_statements}}
    _ = run_linter(source, [rules.BannedStatementsRule], config=config, handlers=[handler])

    assert len(messages) == (0 if passes else 1)
    keywords = ('BannedStatementsRule', 'GO TO', '[4.11]')
    assert all(all(keyword in msg for keyword in keywords) for msg in messages)


def test_fortran_90_operators(rules, frontend):
    '''Test for existence of non Fortran 90 comparison operators.'''
    fcode = """
subroutine test_routine(ia, ib, ic)
integer, intent(in) :: ia, ib, ic

! This should produce 6 problems (one for each operator)
do while (ia .ge. 3 .or. ia .le. -7)
  if (ib .gt. 5 .or. ib .lt. -1) then
    if (ic .eq. 4 .and. ib .ne. -2) then
      print *, 'Foo'
    end if
  end if
end do

! This should produce no problems
do while (ia >= 3 .or. ia <= -7)
  if (ib > 5 .or. ib < -1) then
    if (ic == 4 .and. ib /= -2) then
      print *, 'Foo'
    end if
  end if
end do

! This should report 5 problems
do while (ia >= 3 .or. & ! This <= should not cause confusion
          ia .le. -7)
  if (ib .gt. 5 .or. ib <= -1) then
    if (ic .gt. 4 .and. ib == -2) then
      print *, 'Foo'
    end if
  elseif (ib .eq. 5) then
    print *, 'Bar'
  else
    if (ic .gt. 2) print *, 'Baz'
  end if
end do
end subroutine test_routine
    """.strip()
    source = Sourcefile.from_source(fcode, frontend=frontend)
    messages = []
    handler = DefaultHandler(target=messages.append)
    _ = run_linter(source, [rules.Fortran90OperatorsRule], handlers=[handler])

    assert len(messages) == 11
    keywords = ('Fortran90OperatorsRule', '[4.15]', 'Use Fortran 90 comparison operator')
    assert all(all(keyword in msg for keyword in keywords) for msg in messages)

    f77_f90_line = (('.ne.', '/=', '7'), ('.eq.', '==', '7'),
                    ('.lt.', '<', '6'), ('.gt.', '>', '6'),
                    ('.le.', '<=', '5'), ('.ge.', '>=', '5'),
                    ('.gt.', '>', '26'), ('.gt.', '>', '32'),
                    ('.eq.', '==', '29'), ('.gt.', '>', '25'),
                    ('.le.', '<=', '23-34'))

    for keywords, message in zip(f77_f90_line, messages):
        assert all(str(keyword) in message for keyword in keywords)
