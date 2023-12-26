# dita

A command-line interface for automated mp3 file tagging using data from
[Discogs](http://www.discogs.com/), inspired by the
[foo_discogs](https://bitbucket.org/zoomorph/foo_discogs/src/master/)
component for [foobar2000](http://www.foobar2000.org/).

# About

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

# Installation

First, generate a [Discogs API token](https://www.discogs.com/settings/developers)

```sh
git clone https://github.com/hejops/dita
cd dita
pip install -r requirements.txt
```

# Usage

Scripts that you will probably want to use include:

1. [`tagfix.py`](./tagfix.py) -- tag MP3 files
1. [`mover.py`](./file/mover.py) -- move files to a central location
1. [`convert.py`](./file/convert.py) -- convert most audio codecs to MP3
1. [`play.py`](./play/pmp.py) -- play music

Exploring the rest of the codebase is an exercise left to the reader.
