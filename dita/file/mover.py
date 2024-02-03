#!/usr/bin/env python3
"""Module for moving tagged files to library

"""
# from pprint import pprint
import os
import shlex
import shutil
import sys

import pandas as pd
from mutagen.easyid3 import EasyID3
from pyfzf.pyfzf import FzfPrompt

import tagfix
from config import QUEUE_FILE
from config import SOURCE_DIR
from config import TARGET_DIR
from tagfuncs import file_to_tags
from tagfuncs import front_int
from tagfuncs import get_audio_files
from tagfuncs import get_files_tags
from tagfuncs import is_ascii
from tagfuncs import select_from_list
from taggenre import GENRES
from taggenre import save_db

assert TARGET_DIR and SOURCE_DIR

MPV_DIR = f"{os.environ.get('XDG_CONFIG_HOME')}/mpv"

# https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file#naming-conventions
NTFS_ILLEGALS = r'<>:"/\|?*'

# will be replaced by self.targets.moved (int/bool)
MOVED_FILES: list[str] = []

# all must be non-empty
TAG_FIELDS = [
    "album",
    "artist",
    "date",
    "genre",
    "title",
    "tracknumber",
]


class Mover:  # {{{
    """Mover object. Moves all tagged files in source directory to
    destination, after performing checks."""

    def __init__(
        self,
        src_dir: str,
    ):
        self.src_dir = os.path.realpath(src_dir)
        self.files = get_audio_files(src_dir)
        self.targets = pd.DataFrame()

        # TODO: remove top level files (not in any dir)
        # pprint(files)
        # sys.exit()

        if not self.files:
            print("Nothing to move")
            return

        if self.src_dir == SOURCE_DIR:
            # restrict to staged.txt
            self.files = [
                f for f in self.files if os.path.dirname(f) in tagfix.STAGED_DIRS
            ]

        print(len(self.files), "files to move")

        self.targets = pd.DataFrame(
            [
                {"file": f, "tags": file_to_tags(f)}
                for f in self.files
                if os.path.isfile(f)
            ],
        )
        self.regen_tag_columns()

        self.targets.dropna(
            subset=list(tagfix.REQUIRED_FIELDS),
            how="any",
            inplace=True,
        )

        # print(self.targets.iloc[0])
        # raise ValueError

        # src column will eventually be deprecated
        self.targets["src"] = self.targets.file

        self.links = {}

    def validate(self):
        """A relatively 'safe' operation; files that don't fulfill the
        necessary conditions will simply be ignored, for subsequent review"""

        if self.targets.empty:
            return

        # print(self.targets.columns)
        conditions = {
            # https://stackoverflow.com/a/14247708
            # https://stackoverflow.com/a/29530601
            "complete fields": ~self.targets[TAG_FIELDS].isna().any(axis=1),
            "valid genre": self.targets.genre.isin(GENRES),
            "correct tracknum len": self.targets.tracknumber.str.len() >= 2,
            # "correct date len": self.targets.date.str.len() == 4,
            "ascii artist": self.targets.artist.apply(is_ascii),
            # "valid filename": self.targets.dest != "",
        }
        for name, cond in conditions.items():
            fail = self.targets[~cond]
            if not fail.empty:
                print("Removed", len(fail), "files that do not have", name)
                print(fail.src.to_list())
            self.targets = self.targets[cond]
            if self.targets.empty:
                print("emptied")
                return
        # raise ValueError

        # 1. determine full abspath
        self.targets = self.targets.join(
            self.targets.apply(self.get_dest_filename, axis=1)
        )

        # print(self.targets)
        # raise ValueError

        # 2. truncate fnames (before ext)
        # TODO: apply() to affected rows only
        too_long = self.targets[self.targets.dest.str.len() > 255]
        # longest = self.targets.sort_values("dest", key=lambda x: x.str.len()).iloc[-1]
        # trunc = truncate_filename(longest)
        # print(trunc)
        # raise ValueError
        if not too_long.empty:
            # if len(sorted(self.targets.dest.to_list(), key=len)[-1]) > 255:
            # print(too_long)
            # raise Exception
            self.targets.dest = self.targets.apply(
                truncate_filename,
                axis=1,
            )

        # if dir has >99 files, all files must have tracknumber of len 3
        # TODO: tags are not updated (not strictly necessary)

        self.targets.tracknumber = self.targets.tracknumber.apply(front_int)

        hundred = self.targets.album.isin(
            self.targets[self.targets.tracknumber.str.len() > 2].album
        )
        if not hundred.empty:
            self.targets.loc[hundred, "tracknumber"] = self.targets[
                hundred
            ].tracknumber.apply(lambda x: str(int(x)).zfill(3))

        # 3. src_to_dest.compilation == 1 -> prepare make_va_symlinks
        # don't actually make them yet
        if "compilation" in self.targets:
            various: pd.DataFrame = self.targets[self.targets.compilation == "1"]
            print(len(various), "va")
            self.links = generate_symlinks(
                various.dest.to_list(),
                # allow_overwrite=False,
            )

            # pprint(links)
            # # concat to various? (add new column)
            # raise ValueError

        assert all(self.targets.dest), set(
            self.targets[self.targets.dest == ""].src.apply(
                lambda x: os.path.dirname(x)
            )
        )
        # self.targets.dropna(subset="dest", inplace=True)

        # 4. img
        # get_imgs()

    def move(
        self,
        move: bool = True,
        # preview: bool = True,
    ) -> None:
        """Move files to their appropriate location in the destination

        Full paths are required for both source and destination. Destination (from
        tags_to_path) must be determined in advance; it is not done here.

        validate -> preview -> move -> symlink va -> cleanup

        Args:
            src_file: [TODO:description]

        Returns:
            str: new path. empty if nothing is to be done (i.e. source path ==
            destination).
        """

        if self.targets.empty:
            return

        self.validate()

        if self.targets.empty:
            return

        if self.src_dir == SOURCE_DIR:
            self.dry_run()
            print("\nTarget:", TARGET_DIR)
            input("Press enter to continue")

        for _, row in self.targets.iterrows():
            dest_dir = os.path.dirname(row.dest)

            os.makedirs(dest_dir, exist_ok=True)  # mkdir -p

            # https://python.omics.wiki/file-operations/file-commands/os-rename-vs-shutil-move
            # copy2() attempts to preserve file metadata as well
            # in case of error, add more replacement rules to sanitize()

            if row.src == row.dest:
                print("Source matches destination:", row.src)
                continue

            if move:
                # overwrite can only be done when full dest path is provided
                shutil.move(row.src, row.dest)
            else:
                # shutil.copy(row.src, row.dest)
                print(row.src)

            print(row.dest)

        if not move:
            raise ValueError

        if self.links:
            for src, dests in self.links.items():
                for dest in dests:
                    relative_symlink(src, dest)

        self.cleanup()

    # @staticmethod
    # def guess_date_from_dirname(
    #     file: str,
    #     _tags: EasyID3,
    # ):
    #     _dir = os.path.dirname(file)
    #     guess = re.search(r"(19|20)\d{2}", os.path.basename(_dir))
    #     # list(guess)
    #     if guess and year_is_valid(int(guess.group(0))):
    #         date = guess.group(0)
    #         print("Guessing date:", date)
    #     else:
    #         open_url(
    #             "https://duckduckgo.com/?t=ffab&ia=web&q=",
    #             [_tags["artist"][0], _tags["album"][0]],
    #         )
    #         date = input("Date: ")
    #     files = get_audio_files(_dir)
    #     for _tags in get_files_tags(files):
    #         set_tag(_tags, "date", date)

    @staticmethod
    def get_dest_filename(row: pd.Series) -> pd.Series:
        """Construct destination filename from metadata in a row, then
        add/update 'dest' field to/in the row. Format is fixed to:

        <root>/<artist>/<album> (<date>)/<tracknumber> <title>.<ext>
        """

        def sanitize_filename(parts: list[str]) -> list[str]:
            """Remove characters illegal in NTFS filenames"""
            path = []
            for part in parts:
                # catch blank values
                assert isinstance(part, str), row.src
                for char in part:
                    if char in '"`':
                        part = part.replace(char, "'")
                    elif char in NTFS_ILLEGALS:
                        part = part.replace(char, "-")
                # print(fname)
                # sys.exit()
                path.append(part.strip())
            return path

        # print(row)
        ext = row.src.rsplit(".", maxsplit=1)[-1]
        dest = [
            row.artist.strip("."),
            f"{row.album} ({row.date[:4]})",  # TODO: trunc date tag (outside)
            f"{row.tracknumber} {row.title}.{ext}",
        ]
        assert all(dest)

        # print(self.targets.dest.iloc[0])
        # raise ValueError

        row = pd.Series(
            os.path.join(TARGET_DIR, *sanitize_filename(dest)),
            index=["dest"],
        )

        return row

    def queue_new_albums(self) -> None:
        """Add new relpaths to library and queue files"""

        if self.targets.empty:
            return

        paths = set(self.targets.dest.apply(os.path.dirname))

        # sort by year only
        paths = sorted(
            paths,
            key=lambda x: (x.split()[-1]),
            # reverse=True
        )

        print(f"{len(paths)} newly moved dirs")

        added_artists = set()
        added_albums = set()
        new_queues = set()

        for path in paths:
            # 'artist/album (date)'
            relpath = path.removeprefix(TARGET_DIR + "/")
            assert relpath.count("/") == 1, relpath
            artist, album = relpath.split("/")

            if artist in added_artists or album in added_albums:  # skip if artist added
                continue

            added_artists.add(artist)
            added_albums.add(album)

            new_queues.add(relpath)

        with open(QUEUE_FILE, "a+", encoding="utf-8") as f:
            f.writelines(x + "\n" for x in new_queues)

        with open(f"{MPV_DIR}/library", "a+", encoding="utf-8") as f:
            f.writelines(d.removeprefix(TARGET_DIR + "/") + "\n" for d in paths)

        print(len(added_artists), "dirs queued")

    def cleanup(self) -> None:
        """Remove empty directories, and directories with size <5 MB. Since no
        destructive actions are taken, it can always be called at the end of
        .move()."""

        # https://stackoverflow.com/a/12480543
        # because of how os.walk works, root is not a "fixed" str, but instead
        # gets increasingly deeper
        for root, dirs, files in os.walk(self.src_dir):
            # print(root)
            # print(dirs)
            # print(files)
            if (
                # root != base and
                not dirs
                and not files
            ):
                # print("empty", root)
                # print("Removed", root)
                shutil.rmtree(root)

            if files and not dirs:
                size = sum(os.path.getsize(os.path.join(root, f)) for f in files)
                if size < 5 * 10e5:
                    # print("Removed", root)
                    # might fail for no reason ("dir not empty")
                    shutil.rmtree(root)

        if not os.path.exists(self.src_dir):
            return

        if self.src_dir == SOURCE_DIR:
            os.system(f"ncdu '{self.src_dir}'")
            # remove images etc
            os.system(
                rf"find '{SOURCE_DIR}' -type f -regextype gnu-awk "
                r"-iregex '.*\.(jpg|png|tif|cue|pdf|log|txt)$' -exec rm -v {} \;"
            )
            os.system(rf"find '{SOURCE_DIR}' -type d -empty -delete")
        else:
            # this is just "source matches dest", probably no need to do anything
            os.system(f"ls -1 {shlex.quote(self.src_dir)}")

        # if all files from the parent dir are moved, the parent dir should get
        # removed too
        parent = os.path.dirname(self.src_dir)
        if not os.listdir(parent):
            os.rmdir(parent)

    def regen_tag_columns(self) -> None:
        right = self.targets.tags.apply(pd.Series)
        self.targets = self.targets[self.targets.columns.difference(right.columns)]
        self.targets = self.targets.merge(
            right, left_index=True, right_index=True
        ).applymap(tagfix.tags_to_columns)

    def dry_run(self):
        """Perform a dry-run of the move, then show the resulting relpaths.
        Selecting a relpath calls Tagger.menu() on the source dir.
        """

        def preview() -> pd.Series:  # [str, list[str]]
            print("Grouping...\n")
            reverse = self.targets.copy()
            reverse["dest"] = reverse.dest.apply(
                lambda x: os.path.dirname(x.removeprefix(TARGET_DIR + "/"))
            )
            group: pd.Series = reverse.groupby("dest")["src"].apply(list)
            return group

        # src dest
        # abc ['...', '...', '...']

        group = preview()
        while dest_dir_to_fix := FzfPrompt().prompt(group.index.to_list(), "--reverse"):
            files_to_fix: list[str] = group.loc[dest_dir_to_fix][0]
            mask = self.targets.src.isin(files_to_fix)
            src_dir_to_fix = os.path.dirname(files_to_fix[0])

            print(self.targets[self.targets.src == files_to_fix[0]].iloc[0].tags)

            tagfix.Tagger(src_dir_to_fix).repl()

            # TODO: tags modified (in file and Tagger), file_to_tags is rerun
            # (in Mover), and then columns regen'd but -still- not reflected in
            # Mover!
            #
            # self.targets[mask, "tags"] = [file_to_tags(f) for f in files_to_fix]
            # X self.targets[mask, "tags"] = self.targets[mask].src.apply(file_to_tags)
            self.targets["tags"] = self.targets.src.apply(file_to_tags)
            self.regen_tag_columns()

            print(self.targets[self.targets.src == files_to_fix[0]].iloc[0].tags)

            group = preview()

            # recalc targets.dest only for affected rows

            # for file in files_to_fix:
            #     # self.targets.at[] =...
            #     self.targets = self.targets.join(
            #         self.targets.apply(self.get_dest_filename, axis=1)
            #     )
            #     raise ValueError


# }}}


# @staticmethod
def relative_symlink(
    src: str,
    dest: str,
):
    """Creates relative symlink, does nothing if target already exists.
    Assuming the following directory structure:
                     <root>/<artistB>/<album>/<linkA>
        -> points to <root>/<artistA>/<album>/<fileA>

    The relative symlink simply traverses 2 directories up to reach the
    root:
                      ../../<artistB>/<album>/<linkA>
        -> points to <root>/<artistA>/<album>/<fileA>

    """
    try:
        os.symlink(
            # the actual symlink, can be relative
            src=src.replace(TARGET_DIR, "../.."),
            dst=dest,  # must be absolute
        )
    # except FileNotFoundError as e:  # probably NOT harmless?
    #     print("Not found:", f)
    except FileExistsError:  # can be ignored
        print("Already exists:", dest)


def generate_symlinks(
    files: list[str],
    # allow_overwrite: bool = True,
) -> dict[str, set[str]]:  # best return type for testing
    """Determine symlink 'network' of V/A albums based solely on fullpaths.
    Requires unique album names.

    Consider the following example:

        "{root}/Artist1/Album/01"
        "{root}/Artist1/Album/02"
        "{root}/Artist1/Album/03"
        "{root}/Artist2/Album/04"
        "{root}/Artist2/Album/05"
        "{root}/Artist2/Album/06"

    This function ensures that all six files are contained in both paths to
    Album.

    """

    # could probably use something from itertools (combinations?)
    # assume list is received after a groupby operation
    # self.va.groupby('album').src
    # files = self.va.src.to_list()

    # files: pd.Series = self.va.src
    # .../artist/<album>/file.ext

    # albums = set(files.apply(lambda x: x.split("/")[-2]))
    albums = {f.split("/")[-2] for f in files}
    # artists = {f.split("/")[-3] for f in files}

    # links = []
    links = {f: set() for f in files}

    # print(albums)
    # raise ValueError

    for album in sorted(albums):
        # actual ("orphan") files, before symlinking
        # album_files: list[str] = [f for f in files if f"/{album}/" in f]

        album_files: list[str] = [f for f in files if f"/{album}/" in f]

        # use tracknum to determine 'uniqueness' of an album. this prevents the
        # following bad case from succeeding:

        # [
        #    f"{test_root}/Artist1/[Album/01] a.mp3",
        #    f"{test_root}/Artist1/[Album/02] b.mp3",
        #    f"{test_root}/Artist2/[Album/03] c.mp3",
        #    #
        #    f"{test_root}/Artist3/[Album/01] d.mp3",
        #    f"{test_root}/Artist4/[Album/02] e.mp3",
        #    f"{test_root}/Artist4/[Album/03] f.mp3",
        # ]

        # for 2 va albums with same name (?)
        if len({os.path.basename(f).split()[0] for f in album_files}) != len(
            album_files
        ):
            raise ValueError(
                f"Multiple albums named '{album}' detected. "
                "Manual resolution is required."
            )
            # return []

        # # none of these dest files should exist yet
        # if not all(os.path.exists(f) for f in album_files):
        #     print("Malformed (pre-link):", album)
        #     pprint({f: os.path.exists(f) for f in album_files})
        #     raise ValueError

        # if not allow_overwrite and any(os.path.exists(f) for f in album_files):
        #     input("Press enter to remove...")
        #     for file in album_files:
        #         if os.path.isfile(file):
        #             os.remove(file)
        #             print("removed", file)

        #     return False
        # return True
        # raise ValueError
        # print(123890)
        # continue

        # artists of album
        album_artists = {f.split("/")[-3] for f in album_files}
        # artists = set(files.apply(lambda x: x.split("/")[-3]))

        # album_links = []
        for file in sorted(album_files):
            for art in album_artists:
                # i doubt symlink 'paths' are subject to the same length limit;
                # after all, nearly the whole library root is stripped

                curr_artist = file.split("/")[-3]
                if art == curr_artist:
                    continue
                # make a symlink to every other artist

                # only artist should be replaced, otherwise, you end up with
                # Artist1/Artist1 - Artist2 -> Artist1/Artist1 - Artist1
                dest = file.replace(f"/{curr_artist}/", f"/{art}/")

                if os.path.isfile(dest):  # symlink already made
                    continue

                # lprint(file, curr_artist, src, dest)

                # links.append([src, dest])
                links[file].add(dest)
                # print(src, dest)

        # pprint(album_links)
        # if (len(album_artists) - 1) * len(album_files) != len(album_links):
        #     return []

        # links += album_links

        print("OK:", album)

        # lprint(album_files, artists)
        # raise ValueError

    print(len(files), "files +", len(links), "links")

    # if (len(artists) - len(albums)) * len(files) // len(albums) != len(links):
    #     print("asdjasasd")
    #     return []

    return links

    # from collections import OrderedDict
    # return OrderedDict(sorted(links.items()))

    # dirs = self.va.src.apply(os.path.dirname).to_list()


def truncate_filename(
    row: pd.Series,
    max_artist_len: int = 160,  # https://www.discogs.com/master/2152342
    maxlen: int = 255,
) -> str:
    """It is not entirely unclear what the maximum filename length allowed by
    NTFS is (257?, 260?), so 255 should be a safe value. Although this involves
    re-splitting paths (which were constructed from tags), this is done for
    unit testing.
    """

    dest_filename = row.dest
    excess = len(dest_filename) - maxlen

    # print(excess)

    assert "." in dest_filename, row
    fullpath, ext = dest_filename.rsplit(".", maxsplit=1)

    root, artist, album, fname = fullpath.rsplit("/", maxsplit=3)

    # artist name should never be truncated; artist names that exceed this
    # (extreme) length are a sign that artist tag should be manually fixed.
    if len(artist) > max_artist_len:
        # print(111)
        return ""

    if excess < 0:
        return dest_filename

    track, title = fname.split(" ", maxsplit=1)

    # fname can be truncated without ellipsis, as it is not important for
    # indexing: abcdef.mp3 -> abcde.mp3
    # but tracknumber must be preserved for sorting
    if len(title) >= excess:
        fname = " ".join([track, title[0:-excess]])
        dest_filename = "/".join([root, artist, album, fname]) + "." + ext
        # print("title trunc", dest_filename)
        return dest_filename

    # truncate to tracknumber
    fname = ".".join([track, ext])
    excess -= len(title) + 1

    # print(
    #     fname,
    #     excess,
    # )

    # album must be truncated with ellipsis. year must be preserved for
    # indexing:
    # abcdef (YYYY) -> ab... (YYYY)
    # abcdef [jklmnop] (YYYY) -> ab [jklm... (YYYY)
    # subtract 4 (before year), add '...'

    if excess > 0:
        if album.endswith(")"):
            album, year = album.rsplit(" ", maxsplit=1)
            album = album[0 : -(excess + 3)] + "..."
            album = " ".join([album, year])
        else:
            album = album[0 : -(excess + 3)] + "..."

    return "/".join([root, artist, album, fname])


# def get_imgs(
#     src_dir: str,
# ) -> None:
#     # find covers
#     imgs = []
#     for img in glob(
#         # "*.jpg",
#         "*/folder.jpg",
#         root_dir=src_dir,
#         recursive=True,
#     ):
#         img = f"{src_dir}/{img}"
#         img_dest = (
#             src_to_dest[src_to_dest.src.str.startswith(os.path.dirname(img))]
#             .iloc[0]
#             .dest
#         )
#         img_dest = os.path.dirname(img_dest) + "/folder.jpg"
#         imgs.append({"src": img, "dest": img_dest})
#     src_to_dest = pd.concat(
#         [src_to_dest, pd.DataFrame(imgs)],
#     )


def multi_move(dirs: list[str]):
    """Given a list of dirs already in library, edit artist or genre tag, then
    move."""

    assert all(d.startswith(TARGET_DIR) for d in dirs)

    # fix dirs that were not symlinked together
    if len({os.path.basename(d) for d in dirs}) == 1:
        files = [t for d in dirs for t in get_audio_files(d)]
        links = generate_symlinks(files)
        # print(files)
        # print(links)
        for src, dests in links.items():
            for dest in dests:
                relative_symlink(src, dest)
        return

    tracks = [t for d in dirs for t in get_files_tags(get_audio_files(d))]

    print(f"Moving {len(dirs)} dirs in", TARGET_DIR)

    field: str = select_from_list(["artist", "genre"], "Field")
    new_val = input("Value: ")
    tagfix.edit_tag(tracks, field=field, new_val=new_val)

    if field == "artist":
        for _dir in dirs:
            Mover(_dir).move()


if __name__ == "__main__":
    mvr = Mover(SOURCE_DIR)
    mvr.move()
    mvr.queue_new_albums()

    if "genre" in mvr.targets:
        save_db(mvr.targets[["artist", "genre"]].set_index("artist"))

    if os.path.exists(tagfix.STAGED_FILE):
        os.remove(tagfix.STAGED_FILE)

    print("Done")

    sys.exit(0)
