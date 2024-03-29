#!/usr/bin/env python3
"""'pmp' (Python music player, for lack of a better name) is a barebones 'music
player' strictly focused on album-based queue management, as well as Discogs
integration.

The primary aim of this program is to provide random, nearly continuous
playback, while keeping graphical navigation to an absolute minimum.

While designed to work with mpv, it is, in principle, compatible with any CLI
music player.

"""
import argparse
import os
import shlex
import sys
from random import choice
from random import sample
from random import shuffle
from typing import Iterator

import psutil
import yt_dlp
from pyfzf import FzfPrompt
from requests.exceptions import ConnectionError

from dita.config import CONFIG
from dita.config import PATH
from dita.config import QUEUE_FILE
from dita.config import TARGET_DIR
from dita.discogs.artist import Artist
from dita.discogs.artist import get_artist_id
from dita.discogs.core import search_with_relpath
from dita.discogs.rate import rate_release
from dita.tag.core import glob_full
from dita.tag.core import open_url

# import pandas as pd
# from discogs.browser import Browser

DEFAULT_VOL = 40

FZF_OPTS = [
    "--reverse",
    "--cycle",
    "--pointer='→'",
    "--preview-window='right,cycle,wrap,border-top,40%'",
    "--prompt='→ '",
]
prompt = FzfPrompt()

# LIB_DB = "~/.config/mpv/library"	# is this used?

# /tmp is discouraged as it is cleared on reboot
NP_LOG = PATH + "/" + CONFIG["play"]["nowplaying"]

# https://github.com/mpv-player/mpv/blob/master/DOCS/man/options.rst#watch-later
# https://github.com/mpv-player/mpv/commit/7c4c9bc86f55f4d1224814fbeafdee8f1c3c3108
# WATCH_DIR = f"{os.path.expanduser('~')}/.config/mpv/watch_later"
WATCH_DIR = f"{os.path.expanduser('~')}/.local/state/mpv/watch_later"

# discard resume timestamps
MPV_ARGS = "--mute=no --no-audio-display --pause=no --start=0%"
QUEUE_SYMBOL = "> "


def _writelines(
    file: str,
    lines: list[str],
) -> None:
    """Called before and after playback, and before quitting"""
    with open(file, "w+", encoding="utf-8") as fob:
        fob.writelines(l + "\n" for l in lines)


def read_queue_file() -> list[str]:
    if not os.path.isfile(QUEUE_FILE):
        queue_file = input("Path to queue file: ")
        assert os.path.isfile(queue_file)
        os.symlink(src=queue_file, dst=QUEUE_FILE)

    with open(QUEUE_FILE, "r", encoding="utf-8") as fobj:
        # sample works best with lists
        return fobj.read().splitlines()


class Queue:
    """Queue object for managing playback queue"""

    def __init__(
        self,
    ):
        self.queue = read_queue_file()

        if os.path.isfile(NP_LOG):
            with open(NP_LOG, "r", encoding="utf-8") as fobj:
                self.np_album = fobj.read().strip()  # contains year
                self.np_artist = self.np_album.split("/")[0]
        else:
            self.np_album = ""
            self.np_artist = ""

        self.resumes = list(self.check_watch())
        # print(self.resumes)
        self.will_resume = any(self.resumes)

        # mimic pid/pgrep
        self.playing = any(proc.name() == "mpv" for proc in psutil.process_iter())

    def __str__(self) -> str:
        return "\n".join(sorted(self.queue))

    def __len__(self) -> int:
        return len(self.queue)

    # files{{{

    def get_np_file(self) -> str:
        """Get path of first file opened by mpv"""
        for proc in psutil.process_iter():
            if proc.name() == "mpv":
                return next(f.path for f in proc.open_files() if f.path.endswith("mp3"))
        return ""

    def check_watch(self) -> Iterator[str]:
        """Check if any files/dirs can be resumed by mpv"""
        for file in glob_full(WATCH_DIR, dirs_only=False):
            with open(file, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
                for line in lines:
                    # watch_later format: '# /lib_root/[artist/album]/file'
                    if TARGET_DIR not in line:
                        continue
                    if not os.path.isfile(line.removeprefix("# ")):
                        continue
                    queued = line.removeprefix(f"# {TARGET_DIR}/")
                    # ['a/b/c', 'd', 'e']
                    if queued.count("/") == 2:
                        yield queued.rsplit("/", maxsplit=1)[0]

    # }}}

    # external {{{

    def open_rym(self):
        np_file = self.get_np_file()
        artist = np_file.split("/")[-3]
        album = np_file.split("/")[-2]
        open_url(
            "https://rateyourmusic.com/search?searchtype=l&searchterm=",
            [artist, album],
        )

    def open_spotify(self):
        """Uses filename, not tags"""
        np_file = self.get_np_file()
        artist = np_file.split("/")[-3]
        song = np_file.split("/")[-1].removesuffix(".mp3").split(maxsplit=1)[1]
        open_url("https://open.spotify.com/search/", [artist, song], suffix="tracks")

    def open_yt(self):
        """Uses filename, not tags"""
        np_file = self.get_np_file()
        artist = np_file.split("/")[-3]
        song = np_file.split("/")[-1].removesuffix(".mp3").split(maxsplit=1)[1]
        query = f"ytsearch1:'{artist} - {song}'"

        with yt_dlp.YoutubeDL() as ydl:
            result = ydl.extract_info(
                query,
                download=False,
            )

        if result and "entries" in result and result["entries"]:
            open_url(
                result["entries"][0]["webpage_url"].replace(
                    "www.youtube", "music.youtube"
                )
            )
        else:
            print("no results")

    def open_discogs(self):
        """Open in Discogs release page for currently playing album in
        browser"""
        url = search_with_relpath(self.np_album).get("uri")
        if url:
            print(url)
            open_url(url)
        else:
            open_url("https://www.discogs.com/search/?q=", self.np_album.split())
        sys.exit()

    # def open_rym(self):
    #     url = shlex.quote(
    #         "https://rateyourmusic.com/release/album/"
    #         f"{self.np_album.lower().replace(' ','-')}"
    #     )
    #     os.system(f"xdg-open {url}")
    #     # 	prefix="https://rateyourmusic.com/search?searchtype=l&searchterm="
    # }}}

    # play {{{

    def resume_from_np(self):
        """Use the static nowplaying log to resume playback. Can only ever be
        triggered when there is nothing to resume in mpv."""
        self.play(self.np_album)

    def play(
        self,
        album: str,
        loop: bool = True,
        log: bool = True,
        rate_others: bool = True,
    ):
        """Play album (relpath), select another when finished"""
        # pre
        # check files?

        assert album
        if log:
            _writelines(NP_LOG, [album])

        # # check pulseaudio; this is very much hardware dependent, and should not be handled here
        # os.system("pactl set-sink-mute @DEFAULT_SINK@ false")
        # os.system(f"pactl set-sink-volume @DEFAULT_SINK@ {DEFAULT_VOL}%")

        path = shlex.quote(f"{TARGET_DIR}/{album}")

        if not os.path.isdir(f"{TARGET_DIR}/{album}"):
            os.system("notify-send 'np was deleted'")
            if album in self.queue:
                self.queue.remove(album)
            self.play_from_sample()
            return

        # there is very rarely a need to check mpv exit status (subprocess)
        os.system(f"mpv {MPV_ARGS} {path}")

        os.system('xset -display "$DISPLAY" dpms force on')

        # during playback, any changes made externally (outside loop) will be
        # ignored, if a re-read of the queue file is not done
        self.queue = read_queue_file()

        # print(list(self.check_watch()))
        # raise ValueError

        # post
        if album in list(self.check_watch()):
            print("Will be resumed", album)
            sys.exit()

        # self.unqueue(album)
        if album in self.queue:
            self.queue.remove(album)
        assert self.queue

        _writelines(QUEUE_FILE, list(dict.fromkeys(self.queue)))

        # if not rate:
        #     return

        # os.system("notify-send " + shlex.quote(f"{LIB_ROOT}/{album}"))
        # while file_in_use(f"{LIB_ROOT}/{album}"):
        #     sleep(1)

        # playback was ended, and dir was deleted externally; may lead to a
        # race condition, if the next block is triggered before delete finishes
        if not os.path.isdir(f"{TARGET_DIR}/{album}"):
            os.system("notify-send 'np was deleted'")
            if loop:
                self.play_from_sample()

        # os.system(f"grep -l redirect {WATCH_DIR}/* | xargs -r rm -v")
        # raise ValueError

        # rate
        # print(album)
        artist, _ = album.split("/")

        try:
            release = search_with_relpath(album)
        except ConnectionError:
            release = {}

        if release:
            # raise ValueError
            rating = rate_release(release)
            if not rate_others or rating == 0:
                pass
            elif rating == 1:
                os.system("rm -rIv " + shlex.quote(f"{TARGET_DIR}/{artist}"))
            else:
                artist_id = get_artist_id(artist)
                try:
                    Artist(artist_id).rate_all()
                except ValueError:  # invalid search term
                    pass
                self.select_from_artist(artist)
        else:
            self.select_from_artist(artist)

        _writelines(QUEUE_FILE, list(dict.fromkeys(self.queue)))

        if loop:
            self.play_from_sample()

    def play_from_sample(
        self,
        num: int = 5,
    ) -> None:
        """Sample <num> albums from the queue, allow user to select one, and
        play it.

        If <num> == 1, the sampled item is played automatically.
        If <num> == -1, the entire queue is shown.
        """
        # assert num >= -1
        if num in [0, 1]:
            album = choice(self.queue)
            # album = set(self.queue).pop()
        else:
            if num == -1:
                sam = self.queue
            else:
                sam = sample(self.queue, num)
            album = self.browse_list(sam)
        self.play(album)

    # }}}

    # selection/navigation {{{

    def select_from_lib(
        self,
        sort_artists: bool = True,
    ) -> str:
        """Browse artists, then albums of artist, then add to queue"""
        artists = os.listdir(TARGET_DIR)

        if sort_artists:
            artists = sorted(artists)
        else:
            shuffle(artists)

        artist = self.browse_list(artists)
        return self.select_from_artist(artist)

    def select_from_artist(
        self,
        artist: str = "",
        # sort_last_word: bool = True,
    ) -> str:
        """Returns relpath, which is implicitly added to queue. Returns empty
        string if nothing selected, or if album is already queued.

        Albums of artist are assumed to have the following format:
            '<album> (<year>)'
        """
        if not artist:
            artist = self.np_artist

        _dir = os.path.join(TARGET_DIR, artist)
        if not os.path.isdir(_dir):
            return ""

        # albums = shallow_recurse(_dir, maxdepth=1)

        # df = pd.DataFrame({a: glob_full(a) for a in albums})
        # print(df)

        albums = os.listdir(_dir)

        assert all(alb.endswith(")") for alb in albums), artist
        albums = sorted(
            # relpaths are preferred for now
            # shallow_recurse(_dir, maxdepth=1),
            albums,
            key=lambda x: x.split()[-1] + x,
        )

        # if sort_last_word:
        #     albums = sorted(
        #         # shallow_recurse(_dir, maxdepth=1),
        #         albums,
        #         key=lambda x: x.split()[-1] + x,
        #     )
        # else:
        #     albums = sorted(albums)

        # i don't like this, but browse_list should not need any knowledge of artist
        # ideally, this would be a dict/df with 'queued' attribute
        albums = [
            (
                f"{QUEUE_SYMBOL}{alb}"
                if f"{artist}/{alb}" in self.queue
                #
                else alb
            )
            for alb in albums
        ]

        try:
            album = self.browse_list(
                albums,
                preview_prefix=f"{TARGET_DIR}/{artist}",
            ).removeprefix(QUEUE_SYMBOL)
        except IndexError:
            return ""

        album = f"{artist}/{album}"

        # somewhat redundant (can just check .startswith(QUEUE_SYMBOL))
        if album in self.queue:
            print("Already queued")
            # return ""
        else:
            # self.add(album)
            self.queue.append(album)
            assert self.queue
            print("Queued:", album)

        return album

    @staticmethod
    def browse_list(
        dirs: list[str],
        preview_prefix: str = TARGET_DIR,
    ) -> str:
        """fzf only. Not responsible for exception handling. This should be
        done outside."""
        # df = pd.DataFrame()
        # Browser(df)

        # least insane shell quoting
        # '> ' is to be kept in options, but removed for the preview cmd
        preview_opt = shlex.quote(
            "--preview=echo "
            + shlex.quote(preview_prefix)
            + "/{} | sed 's/> //' | xargs -d '\n' ls -A",
        )

        return prompt.prompt(
            choices=dirs,
            fzf_options=" ".join(FZF_OPTS + [preview_opt]),
        )[0]

    def menu(self):
        """An extremely simple action menu driven by FZF. Playback-related
        options are disabled during playback."""

        options = {
            "Queue album": self.select_from_lib,
        }

        if self.np_artist:
            options |= {
                f"Queue album by {self.np_artist}": self.select_from_artist,
            }

        options |= {
            # "List all queued albums": self.play_from_sample,
            "Open current track in YouTube": self.open_yt,
            "Open current track in Spotify": self.open_spotify,
            "Open current album in RYM": self.open_rym,
            "Open current album in Discogs": self.open_discogs,
            "Quit": self.quit,
            # "Open current artist in Last.fm"
        }

        playing_options = {
            "Play random queued album": self.play_from_sample,
            "Shuffle artist": self.shuffle_artist,
        }
        if self.np_album:
            playing_options |= {
                f"Resume: {self.np_album}": self.resume_from_np,
            }

        if not self.playing:
            options = playing_options | options

        try:
            while opt := prompt.prompt(options, "--reverse")[0]:
                # kwargs could probably be passed here, but i don't like the
                # complexity of it
                options[opt]()
                if self.playing:
                    self.quit()
        except IndexError:
            self.quit()

    # }}}

    def shuffle_artist(self):
        """Select an artist, then play all albums in random order."""
        artists = os.listdir(TARGET_DIR)
        artist = self.browse_list(artists)
        albums = [
            os.path.join(artist, alb) for alb in os.listdir(f"{TARGET_DIR}/{artist}")
        ]
        shuffle(albums)
        for alb in albums:
            try:
                self.play(alb, loop=False, rate_others=False)
            except KeyboardInterrupt:
                self.quit()

    def quit(self):
        """Save queue and exit"""
        assert self.queue
        _writelines(
            QUEUE_FILE,
            list(dict.fromkeys(self.queue)),  # remove dups but preserve order
        )
        sys.exit()


def main():
    parser = argparse.ArgumentParser(description="Play music")

    args = {
        "--queue": {
            "action": "store_true",
            "help": "queue album",
        },
        "--shuf-artist": {
            "action": "store_true",
            "help": "shuffle artist",
        },
        "--no-log": {
            "action": "store_false",
            "dest": "log",
            "help": "don't write np log",
        },
    }

    for arg, arg_opts in args.items():
        parser.add_argument(arg, **arg_opts)

    parser.add_argument(
        "--play",
        action="store_true",
        help="play album",
        # from <artist>",
    )
    parser.add_argument(
        "--artist",
        action="store",
        help="queue album by artist",
        # from <artist>",
    )
    args = parser.parse_args()

    # print(args)
    # sys.exit()

    queue = Queue()

    if args.shuf_artist:
        queue.shuffle_artist()

    if args.queue and not args.play:
        queue.select_from_lib()

    elif args.play:
        queue.play(queue.select_from_lib(), loop=False, log=args.log)

    elif args.artist:
        queue.select_from_artist(args.artist)

    elif queue.will_resume and not queue.playing:
        print("Found queued:", "\n".join(queue.resumes))
        queue.play(queue.resumes[0])

    else:
        queue.menu()

    queue.quit()


if __name__ == "__main__":
    main()
