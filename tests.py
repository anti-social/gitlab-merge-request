import sys
import tempfile
from contextlib import contextmanager
from io import StringIO
from unittest.mock import Mock, MagicMock, PropertyMock, call, patch

import pytest

from gitlab import GitlabGetError

import gitlab_mr

import colorama


@contextmanager
def capture_stdout():
    out = StringIO()
    try:
        stdout, sys.stdout = sys.stdout, out
        yield out
    finally:
        sys.stdout = stdout


@pytest.fixture
def merge_request():
    yield Mock(
        web_url='https://example.com/test/test/merge_requests/123',
        merge=Mock(return_value=Mock()),
    )


@pytest.fixture
def project(merge_request):
    proj = Mock(
        id=123,
        namespace=Mock(path='test'),
        path_with_namespace='test/test',
        default_branch='master',
        mergerequests=Mock(
            create=Mock(return_value=merge_request)
        ),
        commits=Mock(
            get=Mock(return_value=Mock(
                statuses=Mock(list=Mock(return_value=[Mock(status='success')]))
            ))
        ),
        branches=Mock(
            get=Mock(return_value=Mock(commit={'id': '1234567890abcdef'}))
        )
    )
    proj.name = 'test'
    yield proj


@pytest.fixture
def project_no_branch():
    proj = Mock(
        namespace=Mock(path='test'),
        path_with_namespace='test/test',
    )
    proj.name = 'test'
    proj.branches = Mock(
        get=Mock(side_effect=GitlabGetError('404 Branch not found', 404))
    )
    yield proj


@pytest.fixture
def gitlab(project):
    yield Mock(
        projects=Mock(
            get=Mock(return_value=project)
        ),
    )


@pytest.fixture
def gitlab_unknown_project():
    yield Mock(
        projects=Mock(
            get=Mock(side_effect=GitlabGetError('404 Project not found', 404))
        ),
    )


@pytest.fixture
def gitlab_no_branch(project_no_branch):
    yield Mock(
        projects=Mock(get=Mock(return_value=project_no_branch)),
    )


@pytest.fixture
def origin():
    master_ref = Mock()
    master_ref.name = 'origin/master'
    feature_ref = Mock()
    feature_ref.name = 'origin/feature'
    yield Mock(
        url='git@example.com:test/test.git',
        refs={'master': master_ref, 'feature': feature_ref},
    )


@pytest.fixture
def remote_master():
    branch = Mock()
    branch.name = 'origin/master'
    yield branch


@pytest.fixture
def master(remote_master):
    branch = Mock(
        tracking_branch=Mock(return_value=remote_master),
    )
    branch.name = 'master'
    yield branch


@pytest.fixture
def remote_feature():
    branch = Mock()
    branch.name = 'origin/feature'
    yield branch


@pytest.fixture
def feature(remote_feature):
    branch = Mock(
        tracking_branch=Mock(return_value=remote_feature),
    )
    branch.name = 'feature'
    yield branch


@pytest.fixture
def head():
    ref = Mock()
    ref.name = 'feature'
    yield Mock(ref=ref)


@pytest.fixture
def repo(head, origin, master, feature):
    yield Mock(
        head=head,
        remotes={'origin': origin},
        branches={'master': master, 'feature': feature},
        is_dirty=Mock(return_value=False),
        config_reader=Mock(return_value={'branch "master"': 'remote'})
    )


@pytest.fixture
def repo_unknown_remote():
    yield Mock(remotes=Mock(__getitem__=Mock(side_effect=IndexError())))


@pytest.fixture
def repo_dirty(origin):
    yield Mock(
        remotes={'origin': origin},
        is_dirty=Mock(return_value=True),
    )


@pytest.fixture
def repo_detached(origin):
    head = Mock()
    type(head).ref = PropertyMock(side_effect=TypeError)
    yield Mock(
        head=head,
        remotes={'origin': origin},
        is_dirty=Mock(return_value=False),
    )


@pytest.fixture
def conf_file():
    with tempfile.NamedTemporaryFile() as f:
        f.write(
            b'[gitlab]\n'
            b'url = https://example.com\n'
        )
        f.flush()
        yield f


@pytest.fixture
def private_conf_file():
    with tempfile.NamedTemporaryFile() as f:
        f.write(
            b'[gitlab]\n'
            b'private_token = test-token\n'
        )
        f.flush()
        yield f


@pytest.fixture
def empty_private_conf_file():
    with tempfile.NamedTemporaryFile() as f:
        yield f


def test_unknown_source_remote(gitlab, repo_unknown_remote):
    cli = gitlab_mr.Cli(gitlab, repo_unknown_remote)

    with pytest.raises(gitlab_mr._GitlabMRError) as excinfo:
        cli.run(['create'])
    assert str(excinfo.value) == 'Cannot find remote [origin]'


def test_dirty_repo_question(gitlab, repo_dirty):
    cli = gitlab_mr.Cli(gitlab, repo_dirty)

    with patch('builtins.input', return_value='n') as input:
        assert cli.run(['create']) == 1
    assert input.call_args[0][0].startswith('There are uncommited changes')


def test_detached(gitlab, repo_detached):
    cli = gitlab_mr.Cli(gitlab, repo_detached)

    with pytest.raises(gitlab_mr._GitlabMRError) as excinfo:
        cli.run(['create'])
    assert str(excinfo.value).startswith('The repo is in detached state')


def test_unknown_project(gitlab_unknown_project, repo):
    cli = gitlab_mr.Cli(gitlab_unknown_project, repo)

    with pytest.raises(gitlab_mr._GitlabMRError) as excinfo:
        cli.run(['create'])
    assert str(excinfo.value) == 'Project [test/test] not found'


def test_branch_not_found(gitlab_no_branch, repo):
    cli = gitlab_mr.Cli(gitlab_no_branch, repo)

    with pytest.raises(gitlab_mr._GitlabMRError) as excinfo:
        cli.run(['create'])
    assert str(excinfo.value) == \
        'Branch [feature] from project [test/test] not found'


def test_local_commits(gitlab, repo):
    cli = gitlab_mr.Cli(gitlab, repo)

    local_commits = [Mock(hash='123', message='Test', state='+')]
    with patch.object(cli, 'get_mr_commits', Mock(return_value=local_commits)) as get_mr_commits, \
         patch('builtins.input', return_value='n') as input:
        assert cli.run(['create']) == 1
    assert get_mr_commits.call_args[0] == ('feature', 'origin/feature')
    assert input.call_args[0][0].startswith('Found local commits')


def test_no_commits(gitlab, repo):
    cli = gitlab_mr.Cli(gitlab, repo)

    with patch.object(cli, 'get_mr_commits', Mock(side_effect=[[], []])):
        with pytest.raises(gitlab_mr._GitlabMRError) as excinfo:
            cli.run(['create'])
    assert str(excinfo.value) == (
        'Cannot found commits for merge request: '
        'test/test:feature -> test/test:master'
    )


def test_cancel_mr(gitlab, repo):
    cli = gitlab_mr.Cli(gitlab, repo)

    mr_commits = [Mock(hash='0123456789', message='Test', state='+')]
    with patch.object(cli, 'get_mr_commits', Mock(side_effect=[[], mr_commits])), \
         patch('builtins.input', return_value='n') as input:
        res = cli.run(['create'])
    prompt = input.call_args[0][0]
    assert (
        '# You are creating a merge request:\n'
        '#\t{}test/test:feature -> test/test:master{}'.format(
            colorama.Fore.WHITE + colorama.Style.BRIGHT,
            colorama.Style.RESET_ALL,
        )
    ) in prompt
    assert (
        '# Title:\n'
        '# Test\n'
    ) in prompt
    assert '+ {}01234567{} Test\n'.format(
        colorama.Fore.YELLOW, colorama.Style.RESET_ALL
    ) in prompt
    assert res == 1


def test_do_mr(gitlab, repo, project, merge_request):
    cli = gitlab_mr.Cli(gitlab, repo)

    mr_commits = [
        Mock(hash='0123456789', message='Test', state='+'),
        Mock(hash='abcdef0123', message='Test multiple commits', state='+'),
    ]
    with patch.object(cli, 'get_mr_commits', Mock(side_effect=[[], mr_commits])), \
         patch('builtins.input', return_value='y') as input, \
         capture_stdout() as stdout:
        res = cli.run(['create'])

    prompt = input.call_args[0][0]
    assert (
        '# You are creating a merge request:\n'
        '#\t{}test/test:feature -> test/test:master{}'.format(
            colorama.Fore.WHITE + colorama.Style.BRIGHT,
            colorama.Style.RESET_ALL,
        )
    ) in prompt
    assert (
        '# Title:\n'
        '# feature\n'
    ) in prompt
    assert '+ {}01234567{} Test\n'.format(
        colorama.Fore.YELLOW, colorama.Style.RESET_ALL,
    ) in prompt
    assert '+ {}abcdef01{} Test multiple commits\n'.format(
        colorama.Fore.YELLOW, colorama.Style.RESET_ALL,
    ) in prompt

    out = stdout.getvalue()
    assert 'Successfully created merge request:' in out
    assert (
        '\t{}Merge request URL:{} '
        'https://example.com/test/test/merge_requests/123'.format(
            colorama.Fore.WHITE + colorama.Style.BRIGHT,
            colorama.Style.RESET_ALL,
        )
    ) in out
    assert res == 0

    project.mergerequests.create.assert_called_with({
        'target_project_id': 123,
        'title': 'feature',
        'target_branch': 'master',
        'source_branch': 'feature',
        'remove_source_branch': True,
    })


def test_accept_mr(gitlab, repo, merge_request):
    cli = gitlab_mr.Cli(
        gitlab, repo, mr_accept_merge=True, mr_remove_branch=True
    )

    mr_commits = [
        Mock(hash='0123456789', message='Test', state='+'),
        Mock(hash='abcdef0123', message='Test multiple commits', state='+'),
    ]
    with patch.object(cli, 'get_mr_commits', Mock(side_effect=[[], mr_commits])), \
         patch('builtins.input', return_value='y') as input, \
         capture_stdout() as stdout:
        res = cli.run(['create'])

    merge_request.merge.assert_called_with(
        merge_when_pipeline_succeeds=True,
    )


def test_edit_mr(gitlab, repo):
    cli = gitlab_mr.Cli(gitlab, repo)

    parse_mr_file_orig = gitlab_mr.parse_mr_file
    def parse_mr_file(f):
        content = str(f.read(), 'utf-8')
        assert 'Title:\nTest\n' in content
        assert 'Assignee:\n' in content
        assert 'Description:\n' in content
        assert '# You are creating a merge request:\n' in content
        assert '+ 01234567 Test\n' in content
        f.seek(0)
        return parse_mr_file_orig(f)

    mr_commits = [Mock(hash='0123456789', message='Test', state='+')]
    with patch.object(cli, 'get_mr_commits', Mock(side_effect=[[], mr_commits])), \
         patch.object(gitlab_mr, 'subprocess') as subprocess, \
         patch.object(gitlab_mr, 'parse_mr_file', parse_mr_file):
        cli.run(['create', '--edit'])

    run_args = subprocess.run.call_args[0][0]
    assert run_args[0] == 'nano'


def test_main_ask_token(conf_file, empty_private_conf_file, gitlab, repo):
    with patch('gitlab_mr.sys.argv', ['gitlab-mr', 'create']), \
         patch('gitlab_mr.CONFIG_PATH', conf_file.name), \
         patch('gitlab_mr.PRIVATE_CONFIG_PATH', empty_private_conf_file.name), \
         patch('gitlab_mr.CONFIG_FILES',
               [conf_file.name, empty_private_conf_file.name]), \
         patch('gitlab_mr.Cli.get_mr_commits', return_value=[]), \
         patch('gitlab_mr.git.Repo', return_value=repo), \
         patch('gitlab_mr.Gitlab', return_value=gitlab), \
         patch('gitlab_mr.sys.exit') as sys_exit, \
         patch('builtins.input', return_value='aaaaAAAA') as input, \
         capture_stdout() as stdout:
        gitlab_mr.main()

    with open(empty_private_conf_file.name) as private_conf_file:
        assert private_conf_file.read() == (
            '[gitlab]\n'
            'private_token = aaaaAAAA\n'
        )

    # TODO: test colorized output
    prompt = input.call_args[0][0]
    assert (
        'Copy your private token from this page:\n'
        '\thttps://example.com/profile/account\n'
        '\n'
        'And paste it here: '
    ) in prompt

    out = stdout.getvalue()
    assert (
        'Config file {} was successfully written.\n'
        .format(empty_private_conf_file.name)
    ) in out
    assert (
        'ERROR: Cannot found commits for merge request: '
        'test/test:feature -> test/test:master\n'
    ) in out

    sys_exit.assert_called_with(1)


def test_main(conf_file, private_conf_file, gitlab, repo):
    mr_commits = [Mock(hash='0123456789', message='Test', state='+')]
    with patch('gitlab_mr.sys.argv', ['gitlab-mr', 'create']), \
         patch('gitlab_mr.CONFIG_PATH', conf_file.name), \
         patch('gitlab_mr.PRIVATE_CONFIG_PATH', private_conf_file.name), \
         patch('gitlab_mr.CONFIG_FILES',
               [conf_file.name, private_conf_file.name]), \
         patch('gitlab_mr.Cli.get_mr_commits', return_value=mr_commits), \
         patch('gitlab_mr.git.Repo', return_value=repo), \
         patch('gitlab_mr.Gitlab', return_value=gitlab), \
         patch('gitlab_mr.sys.exit') as sys_exit, \
         patch('builtins.input', return_value='y') as input, \
         capture_stdout() as stdout:
        gitlab_mr.main()

    # TODO: test colorized output
    prompt = input.call_args[0][0]
    assert (
        '# You are creating a merge request:\n'
        '#\ttest/test:feature -> test/test:master'
    ) in prompt

    out = stdout.getvalue()
    assert 'Successfully created merge request:' in out
    assert (
        '\tMerge request URL: '
        'https://example.com/test/test/merge_requests/123'
    ) in out

    sys_exit.assert_called_with(0)


# Utils

def test_get_project_path_from_url():
    assert gitlab_mr.get_project_path_from_url(
        'git@example.com:group/project.git') == 'group/project'
    assert gitlab_mr.get_project_path_from_url(
        'git@example.com:group/project.git.git') == 'group/project.git'
    assert gitlab_mr.get_project_path_from_url(
        'http://example.com/group/project.git') == 'group/project'
    assert gitlab_mr.get_project_path_from_url(
        'https://example.com/group/project.git') == 'group/project'
    assert gitlab_mr.get_project_path_from_url(
        'ssh://git@example.com:2222/var/git/group/project.git') == 'group/project'


def test_save_private_token():
    conf_path = tempfile.mktemp()
    gitlab_mr.save_private_token(conf_path, 'abcdef')
    with open(conf_path, 'r') as f:
        content = f.read()
        assert content == (
            '[gitlab]\n'
            'private_token = abcdef\n'
        )

    with tempfile.NamedTemporaryFile() as tf:
        tf.write('[gitlab]\n'
                 'target_remote = upstream\n'.encode('utf-8'))
        tf.flush()
        gitlab_mr.save_private_token(tf.name, 'abcdef')
        with open(tf.name, 'r') as f:
            content = f.read()
            assert content == (
                '[gitlab]\n'
                'private_token = abcdef\n'
                'target_remote = upstream\n'
            )
