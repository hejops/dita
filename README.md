# dita

A command-line interface for automated mp3 file tagging using data from
[Discogs](http://www.discogs.com/), inspired by the
[foo_discogs](https://bitbucket.org/zoomorph/foo_discogs/src/master/) component
for [foobar2000](http://www.foobar2000.org/).

## About

Goals set out in the development of the project include:

- Retrieval of data through the Discogs REST API
- Wrangling and validation of data, often of varying quality
- Semi-automated writing of audio metadata
- A minimal REPL for manual metadata fixes
- Data visualization

Since these goals have largely been met and I am reasonably satisfied with its
present scope, this project is effectively in maintenance mode.

While there is an underlying Python interface to the Discogs API, which
probably has similarities to that of [the joalla team's
client](https://github.com/joalla/discogs_client), exposing it to the user has
not been the primary objective of the project.

## Installation

- Install [poetry](https://python-poetry.org/docs/#installation)
- Generate a [Discogs API token](https://www.discogs.com/settings/developers)

```sh
git clone https://github.com/hejops/dita
cd dita
# in case you want to remove existing installation
# ~/.cache/pypoetry/virtualenvs/dita-*
poetry install
poetry run fix_tags
```

Configuration will be initialised through a few simple prompts. The
[configuration file](./dita/config) can be edited subsequently.

## Usage

Some convenient entry points are defined as follows:

- [`poetry run convert_files`](./dita/file/convert.py)
- [`poetry run discogs_rate`](./dita/discogs/rate.py)
- [`poetry run discogs_release`](./dita/discogs/release.py)
- [`poetry run fix_tags`](./dita/tag/fix.py)
- [`poetry run move_files`](./dita/file/mover.py)
- [`poetry run pmp`](./dita/play/pmp.py)

To invoke the scripts from any location, add the following to your `.bashrc`:

```sh
export PATH=$PATH:~/.cache/pypoetry/virtualenvs/dita-*/bin
```

## Contributing

If you find any issues or need any help, please file an
[issue](https://github.com/hejops/dita/issues). [Pull
requests](https://github.com/hejops/dita/pulls) are also welcome.
