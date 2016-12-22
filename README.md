[![Build Status](https://travis-ci.org/anti-social/gitlab-merge-request.svg?branch=master)](https://travis-ci.org/anti-social/gitlab-merge-request)

# Gitlab merge request command line tool

To create a merge request just run:

```
gitlab-mr
```

The first time you will be asked about your gitlab server url and private token. Then 2 files will be created: 
- `gitlab.ini` - you should add it under git versioning
- `.git/gitlab.ini` - for your private settings

If you prefer to work with your own fork (for example remote is called `fork`) you can set it in the private config. Just add next line into `[gitlab]` section of the `.git/gitlab.ini` file:

```
source_remote = fork
```

Also whenever you want you can specify source and target remotes with the next options: `--source-remote`, `--target-remote`.
By default source and target remotes are `origin`.

To override branches you can use `--source-branch` (`-s`) or `--target-branch` (`-t`) options. Source branch by default is your current branch and target branch is default branch of the gitlab project.
