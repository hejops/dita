#!/usr/bin/env python3
"""Module for converting audio files to MP3."""

import argparse
import multiprocessing
import os
import re
import shlex
import shutil
import sys
import zipfile
from pathlib import Path
from subprocess import PIPE
from subprocess import Popen
from typing import Any

from mutagen._file import File
from mutagen.aiff import AIFF
from mutagen.easymp4 import EasyMP4
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp3 import EasyMP3
from mutagen.mp4 import MP4StreamInfoError
from mutagen.oggopus import OggOpus
from tinytag import TinyTag
from tinytag.tinytag import TinyTagException

from dita.config import CONFIG
from dita.config import SOURCE_DIR
from dita.tag.core import fill_tracknum
from dita.tag.io import glob_full
from dita.tag.io import is_audio_file

BITRATE_TARGET = int(CONFIG["convert"]["bitrate"])
CONVERT_EXTENSIONS = [x.lower() for x in CONFIG["convert"]["filetypes"].split(",")]

if BITRATE_TARGET in {256, 320}:
    BITRATE_ARG = f"-b {BITRATE_TARGET}"
elif BITRATE_TARGET in {0, 1, 2, 3, 4}:
    BITRATE_ARG = f"-V {BITRATE_TARGET}"
else:
    print("Bitrate was not set in config; defaulting to V0")
    BITRATE_ARG = "-V 0"

DISC_REGEX = r"(cd|disco?|disk)[-_ ]?0?[1-9]{1,2}"

# TODO: reused as REQUIRED_FIELDS
TAG_FIELDS = [
    "artist",
    "genre",
    "tracknumber",
    "date",
    "title",
    "album",
]

# for aiff only
TAG_ABBREVS = {
    "TIT2": "title",
    "TPE1": "artist",
    "TRCK": "tracknumber",
    "TALB": "album",
    "TDRC": "date",
}


class Converter:
    """When initialised, `Converter` looks recursively for files to convert."""

    def __init__(
        self,
        root_dir: str,
    ):
        self.root_dir = root_dir

        self.files = glob_full(
            self.root_dir,
            recursive=True,
            deepest_only=False,
        )

        # from pprint import pprint
        # pprint(self.files)
        # raise ValueError

        # check for any 'stray' filetypes
        zips = (f for f in self.files if f.endswith(".zip"))

        for zipf in zips:
            # print(zipf)
            with zipfile.ZipFile(zipf, "r") as zip_ref:
                zip_ref.extractall(os.path.dirname(zipf))
            Path(zipf).unlink()

    def split_cue(self):
        # i hate this so much

        # cue = file.removesuffix(ext) + "cue"

        for cue in [f for f in self.files if f.endswith("cue")]:
            for ext in ["flac", "ape", "wav"]:
                lossless = cue.removesuffix("cue") + ext
                if not Path(lossless).is_file():
                    continue
                args = [
                    *"shnsplit -t %n -o flac".split(),
                    "-a",
                    Path(cue).name,
                    "-f",
                    cue,
                    "-d",
                    Path(cue).parent,
                    "--",
                    lossless,
                ]
                execute_chain(args)
                # Path(file).unlink()

                # execute_chain([["cuetag.sh", cue, os.path.dirname(cue) + "/0*.flac"]])

                # # not that important
                # # https://github.com/svend/cuetools/blob/master/src/tools/cuetag.sh
                # os.system(
                #     f"cuetag.sh {shlex.quote(cue)} {shlex.quote(os.path.dirname(cue))}/0*.flac"
                # )

                Path(lossless).unlink()

        # regen
        self.files = glob_full(
            self.root_dir,
            recursive=True,
            deepest_only=False,
        )

    def flatten_dirs(
        self,
        confirm: bool = False,
    ) -> None:
        """Flatten nested directories, to ease grouping/tagging of files."""
        nested = glob_full(
            self.root_dir,
            deepest_only=True,
            mindepth=2,
        )

        if not nested:
            return

        # print("\n".join(sorted(nested)))
        # raise NotImplementedError

        targets: dict[str, str] = {}
        for src in nested:
            _dir = get_merge_dest(src)
            # print(_dir)
            # raise ValueError

            try:
                tags = TinyTag.get(src)
            except (TinyTagException, IsADirectoryError):
                # print("skip", file)
                continue

            # in extremely rare cases, 2 dirs may get the same discnum

            # 1. discnum field
            # 2. disc num in dirname
            # 3. album field
            # 4. tags blank (use dirname, risky)

            if tags.disc:
                disc = tags.disc
            else:
                # 'CD01', 'CD 1 - BWV 9, 178, 187'
                matches = re.search(
                    DISC_REGEX,
                    src.split("/")[-2],
                    flags=re.IGNORECASE,
                )
                if not matches:
                    continue
                disc = int("".join(c for c in matches.group(0) if c.isnumeric()))
                # print(disc)
                # raise ValueError

            dest = f"{_dir}/{fill_tracknum(disc)}-{os.path.basename(src)}"

            # print(
            #     # src,
            #     dest,
            # )
            # raise ValueError

            # lprint(dest)

            if src == dest:
                continue

            assert "\n" not in dest

            targets[src] = dest

            # assert src in self.files, src

            # TODO: newly split flac(s) will not be in self.files
            idx = self.files.index(src)
            self.files[idx] = dest

        # raise Exception

        if not targets:
            return

        if confirm and sys.__stdin__ and sys.__stdin__.isatty():
            print("\n".join(targets.values()))
            print(len(targets))
            input("continue")

        for src, dest in targets.items():
            shutil.move(src, dest)

        # cleanup empty dirs
        os.system(f"find {shlex.quote(self.root_dir)} -type d -empty -delete")

    def convert_all(self) -> None:
        """Convert all files with a supported extension."""
        self.files = [f for f in self.files if is_audio_file(f, CONVERT_EXTENSIONS)]
        print(len(self.files), "files to convert")
        # lprint(self.files)
        # raise ValueError

        # 2 cores = 20% cpu
        # 3 = 25
        with multiprocessing.Pool(4) as pool:
            _ = pool.map(convert_file, self.files)

        # for file in self.files:
        #     print(file)
        #     convert_file(file)


def get_merge_dest(file: str) -> str:
    """Attempt to determine the correct 'parent' destination of a file in a
    nested dir. Driven entirely by a somewhat hacky regex.
    """
    d = Path(file).parent
    while True:
        if d == "/":
            raise ValueError
        if not re.search(
            DISC_REGEX,
            Path(d).name,
            flags=re.IGNORECASE,
        ):
            return d.as_posix()
            # return d
        d = Path(d).parent


def execute_chain(*cmds: list[str]):
    """Execute a sequence of piped commands, as in a shell.

    Memory safety not guaranteed.
    """
    # https://github.com/karamanolev/WhatManager2/blob/master/what_transcode/flac_lame.py
    processes = []
    # outs = []
    for cmd in cmds:
        # print(" ".join(cmd))
        if processes:
            # use stdout of last finished process as stdin
            p_stdin = processes[-1].stdout
            # p_stdin = outs[-1]
        else:
            p_stdin = None  # stdin specified in cmd str

        if cmd == cmds[-1]:
            p_stdout = None  # last cmd no need pipe (stdout in cmd)
        else:
            p_stdout = PIPE  # pipe to next cmd

        # # doesn't work?
        # # ValueError: I/O operation on closed file
        # with Popen(cmd, stdin=p_stdin, stdout=p_stdout) as subp:
        #     assert subp.returncode is None
        #     processes.append(subp)
        #     outs.append(subp.stdout)
        #     print("ok", cmd, outs[0])

        # print(cmd)
        # pylint: disable=consider-using-with
        subp = Popen(cmd, stdin=p_stdin, stdout=p_stdout)
        assert subp.returncode is None
        processes.append(subp)
        # subp.terminate()

    # https://docs.python.org/2/library/subprocess.html#subprocess.Popen.communicate
    for subp in reversed(processes):
        subp.communicate()


def copy_tags(
    old_tags: Any,
    new_file: str,
):
    """Copy tags from a lossless file (pre-conversion) into its lossy result.

    The result is usually mp3.
    """
    new_tags: EasyMP3 = File(new_file, easy=True)
    for field in TAG_FIELDS:
        if field not in old_tags:
            continue
        if field == "date":
            # date is a multi-spec field (allows multiple values)
            new_tags[field] = old_tags[field]
        else:
            new_tags[field] = old_tags[field][0]
    assert all(k in old_tags for k in new_tags)
    new_tags.save()


def convert_file(file: str):
    """Convert a single file to MP3.

    Tags are typically only preserved within the same filetype (e.g. MP3 ->
    MP3); in all other cases, it is necessary to extract tags from the input
    file and apply them to the output file after conversion.

    No logging is done, but it might be useful if conversion jobs are allowed
    to run repeatedly on the same set of files; this allows bitrate check to be
    skipped.
    """

    def parse_old_tags(file: str) -> dict[str, list[str]]:
        # note: ext is inherited from upper level
        if ext == "flac":
            # https://mutagen.readthedocs.io/en/latest/api/flac.html
            tags = FLAC(file).tags
            if tags:
                return {f: tags[f.upper()] for f in TAG_FIELDS if f.upper() in tags}

        if ext == "m4a":
            # https://mutagen.readthedocs.io/en/latest/api/mp4.html
            # return EasyMP4(file)  # .tags
            return dict(EasyMP4(file))  # .tags

        if ext == "aiff":
            tags = AIFF(file).tags
            if "APIC:cover" in tags:
                tags.pop("APIC:cover")
            return {
                field: [tags[ab].text[0]]
                for ab, field in TAG_ABBREVS.items()
                if ab in tags
            }

        if ext == "opus":
            return OggOpus(file)

        return {}

    # print(file)
    ext = file.rsplit(".", maxsplit=1)[-1]
    # print(ext)

    if ext.lower() == "mp3":
        tmp = file + ".tmp"

        src_br = MP3(file).info.bitrate // 1000  # pylint: disable=no-member

        # target vbr: src files lower than 320 will be ignored
        if BITRATE_TARGET < 10 and src_br < 320:
            return

        # target cbr: src files lower than target will be ignored
        if src_br < BITRATE_TARGET:
            return

        # weird listy constructions are a lesser evil (compared to shlexing)
        execute_chain([*f"lame --silent {BITRATE_ARG} --disptime 1".split(), file, tmp])

        if Path(tmp).is_file():
            shutil.move(tmp, file)
        return

    # lossless files will always be converted to the target bitrate

    # .replace() should never be used as 'flac' can occur >1 time in a string
    mp3 = file.removesuffix(ext) + "mp3"

    cue = file.removesuffix(ext) + "cue"
    if Path(cue).is_file():
        return

    if Path(mp3).is_file():
        Path(file).unlink()
        return

    if ext.lower() == "wav":
        wav = file + "_.wav"
    else:
        wav = file.removesuffix(ext) + "wav"

    try:
        tags = parse_old_tags(file)
    except MP4StreamInfoError:
        Path(file).unlink()  # we cannot possibly recover from this
        return

    if ext.lower() == "flac":
        execute_chain(
            [*"flac --decode --stdout --totally-silent".split(), file],
            [*f"lame --silent {BITRATE_ARG} -".split(), mp3],
        )

    elif ext in CONVERT_EXTENSIONS:
        # print(file)
        # raise ValueError
        # 2 separate commands (in shell, this would require process substitution)
        try:
            # caused by yt-dlp downloads
            execute_chain([*"ffmpeg -y -i".split(), file, wav])
            execute_chain(
                [*f"lame --silent {BITRATE_ARG} --disptime 1".split(), wav, mp3],
            )
        except PermissionError:
            Path(file).unlink()
            return

        if Path(wav).is_file():
            Path(wav).unlink()

    else:
        raise NotImplementedError(file)

    # failed to convert, usually failed to read input
    if not Path(mp3).is_file():
        return
    copy_tags(tags, mp3)
    Path(file).unlink()
    # print("Converted", file)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path",
        required=False,
        default=SOURCE_DIR,
    )
    args = parser.parse_args()

    con = Converter(os.path.realpath(args.path))
    con.split_cue()
    con.flatten_dirs()
    con.convert_all()


if __name__ == "__main__":
    main()
