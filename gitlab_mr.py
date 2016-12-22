import os
import re
import sys
import tempfile
import subprocess
import collections
import logging.config
from argparse import ArgumentParser
from configparser import ConfigParser

import git
from gitlab import Gitlab, GitlabError, GitlabGetError, GitlabConnectionError


log = logging.getLogger('gitlab-cli')


CONFIG_PATH = 'gitlab.ini'
PRIVATE_CONFIG_PATH = '.git/gitlab.ini'
CONFIG_FILES = [CONFIG_PATH, PRIVATE_CONFIG_PATH]

PRIVATE_CONFIG_TEMPLATE = '''[gitlab]
private_token = {private_token}
'''

CONFIG_TEMPLATE = '''
[gitlab]
url = {gitlab_url}
mr_edit = false
mr_accept_merge = false
mr_remove_branch = true

# Logging configuration
[loggers]
keys = root,gitlab,gitlab-cli

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = INFO
handlers = console
qualname =

[logger_gitlab]
level = WARNING
handlers = console
qualname =

[logger_gitlab-cli]
level = INFO
handlers = console
qualname =

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)s [%(name)s] %(message)s
'''

DEFAULT_TIMEOUT = 5


MRCommit = collections.namedtuple('MRCommit', ['hash', 'message', 'state'])


class _GitlabMRError(Exception):
    def __init__(self, msg, *args, exc=None, exit_code=1):
        self.msg = msg
        self.args = args
        self.exc = exc
        self.exit_code = exit_code

    def __str__(self):
        if len(self.args) == 1 and isinstance(self.args[0], dict):
            return self.msg % self.args[0]
        return self.msg % self.args


def err(msg, *args, exc=None, code=1):
    raise _GitlabMRError(msg, *args, exc=exc, exit_code=code)


class Cli(object):
    def __init__(self, gitlab, repo,
                 source_remote=None,
                 target_remote=None,
                 mr_edit=False,
                 mr_accept_merge=False,
                 mr_remove_branch=False):
        self.gitlab = gitlab
        self.repo = repo
        self.source_remote = source_remote or 'origin'
        self.target_remote = target_remote or 'origin'
        self.mr_edit = mr_edit
        self.mr_accept_merge = mr_accept_merge
        self.mr_remove_branch = mr_remove_branch
        self.parser = self.get_parser()

    def get_parser(self):
        parser = ArgumentParser(
            description='Simple stupid gitlab cli for merge requests.'
        )
        subparsers = parser.add_subparsers(help='Subcommands')
        mr_parser = subparsers.add_parser(
            'create', help='Create merge request'
        )
        # TODO: Should we add subcommands for merge-request: create, edit, apply
        mr_parser.set_defaults(action=self.merge_request)
        mr_parser.add_argument(
            '--source-branch', '-s', dest='source_branch',
            help='Source branch for merge'
        )
        mr_parser.add_argument(
            '--source-remote', dest='source_remote',
            help='Source remote for merge'
        )
        mr_parser.add_argument(
            '--target-branch', '-t', dest='target_branch',
            help='Target branch for merge'
        )
        mr_parser.add_argument(
            '--target-remote', dest='target_remote',
            help='Target remote for merge'
        )
        mr_parser.add_argument(
            '--message', '--title', '-m', dest='message',
            help='Message for merge'
        )
        mr_parser.add_argument(
            '--edit', '-e', dest='edit',
            action='store_const', const=True, default=self.mr_edit,
            help='Run editor to edit merge request data ({} by default)'.format(
                'enabled' if self.mr_edit else 'disabled'
            )
        )
        mr_parser.add_argument(
            '--assignee', '-A', dest='assignee',
            help='Assign merge request to the reviewer'
        )
        mr_parser.add_argument(
            '--accept-merge', '--auto-merge', '-a', dest='accept_merge',
            action='store_const', const=True, default=self.mr_accept_merge,
            help='Auto merge on succeed ({} by default)'.format(
                'enabled' if self.mr_accept_merge else 'disabled'
            )
        )
        mr_parser.add_argument(
            '--no-accept-merge', '--no-auto-merge', dest='accept_merge',
            action='store_const', const=False, default=self.mr_accept_merge,
            help='Disable auto merge on succeed'
        )
        mr_parser.add_argument(
            '--remove-branch', '-R', dest='remove_branch',
            action='store_const', const=True, default=self.mr_remove_branch,
            help='Delete source branche after merge ({} by default)'.format(
                'enabled' if self.mr_remove_branch else 'disabled'
            )
        )
        mr_parser.add_argument(
            '--no-remove-branch', dest='remove_branch',
            action='store_const', const=False, default=self.mr_remove_branch,
            help='Disable removing source branche after merge'
        )
        return parser

    def git_cmd(self, args):
        git_args = ['git'] + args
        try:
            res = subprocess.run(
                git_args, stdout=subprocess.PIPE,
            )
            if res.returncode != 0:
                err('%s command exited with error: %s',
                    ' '.join(git_args), res.returncode)
            return str(res.stdout, 'utf-8').strip()
        except FileNotFoundError as e:
            err('Cannot find git command: %s', e)
        except subprocess.SubprocessError as e:
            err('Error running git command: %s', e)

    def get_mr_commits(self, source_branch, target_branch):
        # TODO: find out merge request commits using self.repo
        commits = []
        res = self.git_cmd(['cherry', '-v', target_branch, source_branch])
        if not res.strip():
            return commits
        for line in res.split('\n'):
            state, hash, msg = line.split(maxsplit=2)
            commits.append(MRCommit(hash, msg, state))
        return commits

    def get_project_by_path(self, path):
        namespace_path, _, project_name = path.partition('/')
        try:
            projects = self.gitlab.projects.search(project_name)
        except GitlabError as e:
            err('Error when getting project [%s]: %s' % (project_name, e))
        except GitlabConnectionError as e:
            err('%s', e)
        for p in projects:
            if p.name == project_name and p.namespace.path == namespace_path:
                return p
        err('Cannot find project [%s]', path)

    def get_user_by_username(self, username):
        for u in self.gitlab.users.search(username):
            if u.username == username:
                return u
        err('Cannot find user [%s]', username)

    def get_project_path_by_remote(self, remote_name):
        try:
            remote = self.repo.remotes[remote_name]
        except IndexError:
            err('Cannot find remote [%s]', remote_name)
        # TODO: also parse http
        return remote.url.partition(':')[2].partition('.git')[0]

    def get_remote_branch_name(self, project, local_branch, remote):
        # check if there is upstream for local branch
        tracking_branch = self.repo.branches[local_branch].tracking_branch()
        if tracking_branch:
            remote_branch = tracking_branch.name.partition('/')[2]
        else:
            remote_branch = local_branch
        try:
            print(project.branches)
            project.branches.get(remote_branch)
        except GitlabGetError:
            err('Branch [%s] not found on the gitlab server', remote_branch)
        except GitlabConnectionError as e:
            err('%s', e)
        return remote_branch

    def run(self, args):
        options = self.parser.parse_args(args)
        if not hasattr(options, 'action'):
            self.parser.print_help()
            return
        return self.merge_request(options)

    def merge_request(self, opts):
        source_remote = opts.source_remote or self.source_remote
        source_project_path = self.get_project_path_by_remote(source_remote)
        target_remote = opts.target_remote or self.target_remote
        target_project_path = self.get_project_path_by_remote(target_remote)

        if self.repo.is_dirty():
            answer = input('There are uncommited changes. '
                           'Do you want to continue? [y/N]: ')
            if not is_yes(answer):
                return 1

        try:
            source_branch = opts.source_branch or self.repo.head.ref.name
        except TypeError:
            err("The repo is in detached state. Cannot find out source branch.")
        source_project = self.get_project_by_path(source_project_path)
        remote_source_branch = self.get_remote_branch_name(
            source_project, source_branch, source_remote
        )
        print(remote_source_branch, self.repo.remotes[source_remote].refs)
        if remote_source_branch not in self.repo.remotes[source_remote].refs:
            err('You must push [%(branch)s] branch before creating merge request:\n'
                '\tgit push %(remote)s %(branch)s',
                {'remote': source_remote, 'branch': remote_source_branch})
        print(remote_source_branch)
        local_commits = self.get_mr_commits(
            source_branch,
            self.repo.remotes[source_remote].refs[remote_source_branch].name
        )
        if local_commits:
            answer = input(
                'Found local commits:\n'
                '{}\n'
                'Possibly you want to push them.\n'
                'Do you want to continue? [y/N]: '.format(
                    '\n'.join('\t{} {}'.format(
                        c.hash[:8], c.message) for c in local_commits
                    )
                )
            )
            if not is_yes(answer):
                return 1

        target_project = self.get_project_by_path(target_project_path)
        target_branch = opts.target_branch or target_project.default_branch

        data = {
            'source_branch': remote_source_branch,
            'target_project_id': target_project.id,
            'target_branch': target_branch,
            'assignee': opts.assignee,
        }

        commits = self.get_mr_commits(
            '{}/{}'.format(source_remote, remote_source_branch),
            target_branch
        )
        if not commits:
            err('Cannot found commits for merge request: %s',
                get_mr_outline(data, source_project, target_project))

        title = None
        if opts.message:
            title = opts.message
        if not title and len(commits) == 1:
            title = commits[0].message
        if not title:
            title = remote_source_branch
        data['title'] = title

        if opts.edit:
            data = edit_mr(data, source_project, target_project, commits)
        else:
            answer = show_preview_and_confirm(data, source_project, target_project, commits)
            if is_edit(answer):
                data = edit_mr(data, source_project, target_project, commits)
            elif not is_yes(answer):
                return 1

        if data['assignee']:
            data['assignee_id'] = self.get_user_by_username(data['assignee']).id
        data.pop('assignee', None)
        validate_mr_data(source_project, target_project, data)

        log.info(
            'Creating merge request: %s',
            get_mr_outline(data, source_project, target_project)
        )
        try:
            mr = source_project.mergerequests.create(data)
            print('Successfully created merge request:\n'
                  '\tMerge request URL: {}\n'.format(mr.web_url))
        except GitlabError as e:
            err('Error creating merge request: %s' % e)
        except GitlabConnectionError as e:
            err('%s', e)
        if opts.accept_merge:
            try:
                commit = source_project.commits.get(
                    source_project.branches.get(remote_source_branch).commit['id']
                )
                # TODO: check pipeline instead builds
                if any(map(lambda s: not s.allow_failure and s.status == 'failed',
                           commit.statuses.list())):
                    print('Cannot accept merge request because of '
                          'there are failed builds.')
                    return
                mr.merge(
                    merge_when_build_succeeds=opts.accept_merge,
                    # FIXME: removing source branch doesn't work
                    should_remove_source_branch=opts.remove_branch,
                )
                print('Merge request was successfully updated:\n'
                      '\tAutomatic merge: {}\n'
                      '\tRemove source branch: {}'.format(
                          opts.accept_merge, opts.remove_branch))
            except GitlabError as e:
                err('Error updating merge request: %s' % e)
            except GitlabConnectionError as e:
                err('%s', e)


def is_yes(ans):
    return ans.lower() in ('y', 'yes')


def is_edit(ans):
    return ans.lower() in ('e', 'edit')


def check_branch(project, branch):
    try:
        project.branches.get(branch)
    except GitlabGetError as e:
        err(
            'Cannot find branch [%(branch)s] for project [%(project)s]',
            {'branch': branch, 'project': project.path_with_namespace},
        )
    except GitlabError as e:
        err('Gitlab error: %s', e)
    except GitlabConnectionError as e:
        err('%s', e)


def validate_mr_data(source_project, target_project, data):
    check_branch(source_project, data['source_branch'])
    check_branch(target_project, data['target_branch'])
    if not data.get('title'):
        err('Empty [title]. Specify title of the merge request.')


def edit_mr(data, source_project, target_project, commits):
    editor = os.environ.get('EDITOR', 'nano')
    title = data.get('title')
    assignee = data.get('assignee')
    description = data.get('description')
    content = (
        'Title:\n'
        '{title}\n'
        'Assignee:\n'
        '{assignee}\n'
        'Description:\n'
        '\n'
        '# You are creating a merge request:\n'
        '#\t{outline}\n'
        '#\n'
        '# Next commits will be included in the merge request:\n'
        '#\n'
        '{commits}\n'
        '#\n'
        '# Empty title will cancel the merge request.'
    ).format(
        title='{}\n'.format(title) if title else '',
        assignee='{}\n'.format(assignee) if assignee else '',
        description='{}\n'.format(description) if description else '',
        outline=get_mr_outline(data, source_project, target_project),
        commits=format_mr_commits(commits, prefix='#\t'),
    )
    with tempfile.NamedTemporaryFile() as tf:
        tf.write(content.encode('utf-8'))
        tf.flush()
        res = subprocess.run([editor, tf.name])
        tf.seek(0)
        new_data = data.copy()
        new_data.update(parse_mr_file(tf))
        return new_data


def show_preview_and_confirm(data, source_project, target_project, commits):
    title = (
        '# Title:\n'
        '# {}\n'
        '#\n'.format(data['title'])
    )
    assignee = (
        '# Assignee:\n'
        '# {}\n'
        '# \n'.format(data['assignee'])
    ) if data.get('assignee') else ''
    description = (
        '# Description:\n'
        '# {}\n'
        '#\n'.format(data['description'])
    ) if data.get('description') else ''
    answer = input(
        '\n'
        '# You are creating a merge request:\n'
        '#\t{outline}\n'
        '#\n'
        '{title}'
        '{assignee}'
        '{description}'
        '# Next commits will be included in the merge request:\n'
        '#\n'
        '{commits}\n'
        '#\n\n'
        'Do you really want to create the merge request? [Y/n/e]: '.format(
            title=title,
            assignee=assignee,
            description=description,
            outline=get_mr_outline(data, source_project, target_project),
            commits=format_mr_commits(commits, prefix='#\t'),
        )
    )
    return answer or 'Y'


def get_mr_outline(data, source_project, target_project):
    return (
        '{source_project}:{source_branch} -> {target_project}:{target_branch}'
    ).format(
        source_project=source_project.path_with_namespace,
        source_branch=data['source_branch'],
        target_project=target_project.path_with_namespace,
        target_branch=data['target_branch'],
    )


def format_mr_commits(commits, prefix=''):
    return '\n'.join(
        '{}{} {} {}'.format(prefix, c.state, c.hash[:8], c.message)
        for c in commits
    )


def parse_mr_file(f):
    def maybe_save_lines(key, lines):
        if key:
            data[key] = '\n'.join(lines)

    data = {}
    keys = ['title', 'assignee', 'description']
    keys_map = {'{}:'.format(k.capitalize()): k for k in keys}
    current_key = None
    current_lines = []
    for line in f.readlines():
        line = str(line, 'utf-8').strip()
        if not line or line.startswith('#'):
            continue
        # TODO: make more universal
        if line in keys_map:
            maybe_save_lines(current_key, current_lines)
            current_key = keys_map[line]
            current_lines = []
            continue
        current_lines.append(line)
    maybe_save_lines(current_key, current_lines)
    return data


def save_private_token(conf_path, token):
    if os.path.exists(conf_path):
        with open(conf_path) as conf_file:
            section_ix = token_ix = -1
            lines = []
            for i, line in enumerate(conf_file):
                lines.append(line)
                if line.strip() == '[gitlab]':
                    section_ix = i
                elif re.match('[.*]', line.strip()):
                    section_ix = -1
                if section_ix >= 0 and re.match('\s*private_token\s*=', line):
                    token_ix = i
            if section_ix >= 0:
                token_line = 'private_token = {}\n'.format(token)
                if token_ix >= 0:
                    lines[token_ix] = token_line
                else:
                    lines.insert(section_ix + 1, token_line)
            content = ''.join(lines)
    else:
        content = PRIVATE_CONFIG_TEMPLATE.format(private_token=token)
    with tempfile.NamedTemporaryFile(
            dir=os.path.dirname(conf_path), delete=False) as tf:
        tf.write(content.encode('utf-8'))
        os.rename(tf.name, conf_path)


def create_main_config(conf_path, url):
    content = CONFIG_TEMPLATE.format(gitlab_url=url)
    with tempfile.NamedTemporaryFile(
            dir=os.path.dirname(conf_path), delete=False) as tf:
        tf.write(content.encode('utf-8'))
        os.rename(tf.name, conf_path)


def main():
    if not os.path.exists(CONFIG_PATH):
        gitlab_url = input('Enter gitlab server url: ')
        create_main_config(CONFIG_PATH, gitlab_url)
        print('Config was successfully saved into {} file\n'
              'Do not forget to include it into git index.'.format(CONFIG_PATH))

    config = ConfigParser()
    config.read(CONFIG_FILES)
    try:
        token_url = '{}/profile'.format(config['gitlab']['url'])
    except KeyError:
        log.error('Create gitlab.ini file with ')
        sys.exit(1)

    if not config['gitlab'].get('private_token'):
        token = input('Enter your private token ({}): '.format(token_url))
        save_private_token(PRIVATE_CONFIG_PATH, token)
        print('Config file {} was successfully written.'.format(PRIVATE_CONFIG_PATH))
        config.read(PRIVATE_CONFIG_PATH)

    if 'loggers' in config:
        logging.config.fileConfig(config, disable_existing_loggers=False)
    else:
        logging.basicConfig()

    settings = config['gitlab']
    source_remote = settings.get(
        'source_remote', fallback='origin',
    )
    target_remote = settings.get(
        'target_remote', fallback='origin',
    )
    timeout = settings.getint(
        'timeout', fallback=DEFAULT_TIMEOUT,
    )
    cli = Cli(
        Gitlab(settings['url'],
               private_token=settings['private_token'],
               timeout=timeout),
        git.Repo(),
        source_remote=source_remote,
        target_remote=target_remote,
        mr_edit=settings.getboolean('mr_edit', fallback=False),
        mr_accept_merge=settings.getboolean('mr_accept_merge', fallback=False),
        mr_remove_branch=settings.getboolean('mr_remove_branch', fallback=True),
    )
    try:
        exit_code = cli.run(sys.argv[1:])
        sys.exit(exit_code)
    except _GitlabMRError as e:
        log.error(e.msg, *e.args)
        sys.exit(e.exit_code)


if __name__ == '__main__':
    main()