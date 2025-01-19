# import os
import pytest

from dita.file.mover import generate_symlinks
from dita.file.mover import truncate_filename


def test_truncate():
    assert truncate_filename("/short/path/file.xyz", max_artist_len=3) == ""
    assert truncate_filename("/short/path/file.xyz") == "/short/path/file.xyz"

    for k, v in {
        "/short/path/01 file.xyz": "/short/path/01.xyz",
        "/short/path123456789/01 file.xyz": "/short/path123.../01.xyz",
        "/short/path123456789 (1234)/01 file.xyz": "/short/path1234... (1234)/01.xyz",
        # "/short/path123456789 (1234)/01 file.xyz": "/short/... (1234)/01.xyz",
        "/short/path123456789 (1234)/100 file.xyz": "/short/... (1234)/100.xyz",
    }.items():
        assert truncate_filename(k, maxlen=len(v)) == v

    assert (
        truncate_filename(
            "/aaa/bbbbb/cccccc/dddddddd/music/John Dwyer, Ryan Sawyer, Peter Kerlin,"
            " Brad Caulkins, Kyp Malone, Tom Dolas, Marcos Rodriguez, Andres Renteria,"
            " Ben Boye, Laena Myers-Ionita, Joce Soubrian/Moon-Drenched (2021)/05 Get"
            " Thee To The Rookery.mp3",
        )
        == "/aaa/bbbbb/cccccc/dddddddd/music/John Dwyer, Ryan Sawyer, Peter Kerlin,"
        " Brad Caulkins, Kyp Malone, Tom Dolas, Marcos Rodriguez, Andres Renteria,"
        " Ben Boye, Laena Myers-Ionita, Joce Soubrian/Moon-Drenched (2021)/05 Get"
        " Thee To The Rookery.mp3"
    )


def test_va():
    test_root = "/foo/bar/baz"

    # base case (2 artists)
    va_paths_6 = [
        f"{test_root}/Artist1/Album/01.mp3",
        f"{test_root}/Artist1/Album/02.mp3",
        f"{test_root}/Artist1/Album/03.mp3",
        f"{test_root}/Artist2/Album/04.mp3",
        f"{test_root}/Artist2/Album/05.mp3",
        f"{test_root}/Artist2/Album/06.mp3",
    ]

    # len(results) = (artists - 1) * albums * tracks
    # (2-1) * 1 * 6 = 6
    # source <- link
    assert generate_symlinks(va_paths_6) == {
        "/foo/bar/baz/Artist1/Album/01.mp3": {"/foo/bar/baz/Artist2/Album/01.mp3"},
        "/foo/bar/baz/Artist1/Album/02.mp3": {"/foo/bar/baz/Artist2/Album/02.mp3"},
        "/foo/bar/baz/Artist1/Album/03.mp3": {"/foo/bar/baz/Artist2/Album/03.mp3"},
        "/foo/bar/baz/Artist2/Album/04.mp3": {"/foo/bar/baz/Artist1/Album/04.mp3"},
        "/foo/bar/baz/Artist2/Album/05.mp3": {"/foo/bar/baz/Artist1/Album/05.mp3"},
        "/foo/bar/baz/Artist2/Album/06.mp3": {"/foo/bar/baz/Artist1/Album/06.mp3"},
    }

    # 3 artists
    # (3-1) * 1 * 6 = 12
    va_paths_12 = [
        f"{test_root}/Artist1/Album/01.mp3",
        f"{test_root}/Artist1/Album/02.mp3",
        f"{test_root}/Artist2/Album/03.mp3",
        f"{test_root}/Artist2/Album/04.mp3",
        f"{test_root}/Artist3/Album/05.mp3",
        f"{test_root}/Artist3/Album/06.mp3",
    ]
    assert generate_symlinks(va_paths_12) == {
        "/foo/bar/baz/Artist1/Album/01.mp3": {
            "/foo/bar/baz/Artist3/Album/01.mp3",
            "/foo/bar/baz/Artist2/Album/01.mp3",
        },
        "/foo/bar/baz/Artist1/Album/02.mp3": {
            "/foo/bar/baz/Artist3/Album/02.mp3",
            "/foo/bar/baz/Artist2/Album/02.mp3",
        },
        "/foo/bar/baz/Artist2/Album/03.mp3": {
            "/foo/bar/baz/Artist1/Album/03.mp3",
            "/foo/bar/baz/Artist3/Album/03.mp3",
        },
        "/foo/bar/baz/Artist2/Album/04.mp3": {
            "/foo/bar/baz/Artist1/Album/04.mp3",
            "/foo/bar/baz/Artist3/Album/04.mp3",
        },
        "/foo/bar/baz/Artist3/Album/05.mp3": {
            "/foo/bar/baz/Artist1/Album/05.mp3",
            "/foo/bar/baz/Artist2/Album/05.mp3",
        },
        "/foo/bar/baz/Artist3/Album/06.mp3": {
            "/foo/bar/baz/Artist1/Album/06.mp3",
            "/foo/bar/baz/Artist2/Album/06.mp3",
        },
    }

    # true va
    # (6-1) * 1 * 6 = 30
    va_paths_30 = [
        f"{test_root}/Artist1/Album/01.mp3",
        f"{test_root}/Artist2/Album/02.mp3",
        f"{test_root}/Artist3/Album/03.mp3",
        f"{test_root}/Artist4/Album/04.mp3",
        f"{test_root}/Artist5/Album/05.mp3",
        f"{test_root}/Artist6/Album/06.mp3",
    ]

    assert generate_symlinks(va_paths_30) == {
        "/foo/bar/baz/Artist1/Album/01.mp3": {
            "/foo/bar/baz/Artist6/Album/01.mp3",
            "/foo/bar/baz/Artist2/Album/01.mp3",
            "/foo/bar/baz/Artist5/Album/01.mp3",
            "/foo/bar/baz/Artist3/Album/01.mp3",
            "/foo/bar/baz/Artist4/Album/01.mp3",
        },
        "/foo/bar/baz/Artist2/Album/02.mp3": {
            "/foo/bar/baz/Artist1/Album/02.mp3",
            "/foo/bar/baz/Artist3/Album/02.mp3",
            "/foo/bar/baz/Artist5/Album/02.mp3",
            "/foo/bar/baz/Artist6/Album/02.mp3",
            "/foo/bar/baz/Artist4/Album/02.mp3",
        },
        "/foo/bar/baz/Artist3/Album/03.mp3": {
            "/foo/bar/baz/Artist4/Album/03.mp3",
            "/foo/bar/baz/Artist5/Album/03.mp3",
            "/foo/bar/baz/Artist1/Album/03.mp3",
            "/foo/bar/baz/Artist6/Album/03.mp3",
            "/foo/bar/baz/Artist2/Album/03.mp3",
        },
        "/foo/bar/baz/Artist4/Album/04.mp3": {
            "/foo/bar/baz/Artist1/Album/04.mp3",
            "/foo/bar/baz/Artist5/Album/04.mp3",
            "/foo/bar/baz/Artist6/Album/04.mp3",
            "/foo/bar/baz/Artist3/Album/04.mp3",
            "/foo/bar/baz/Artist2/Album/04.mp3",
        },
        "/foo/bar/baz/Artist5/Album/05.mp3": {
            "/foo/bar/baz/Artist1/Album/05.mp3",
            "/foo/bar/baz/Artist4/Album/05.mp3",
            "/foo/bar/baz/Artist2/Album/05.mp3",
            "/foo/bar/baz/Artist3/Album/05.mp3",
            "/foo/bar/baz/Artist6/Album/05.mp3",
        },
        "/foo/bar/baz/Artist6/Album/06.mp3": {
            "/foo/bar/baz/Artist2/Album/06.mp3",
            "/foo/bar/baz/Artist5/Album/06.mp3",
            "/foo/bar/baz/Artist4/Album/06.mp3",
            "/foo/bar/baz/Artist1/Album/06.mp3",
            "/foo/bar/baz/Artist3/Album/06.mp3",
        },
    }

    # return

    # (4-1) * 2 * 6 = "36"
    # in reality, (2-1)*1*3 + (2-1)*1*3 = 6
    va_paths_2_albums = [
        f"{test_root}/Artist1/Album1/01.mp3",
        f"{test_root}/Artist1/Album1/02.mp3",
        f"{test_root}/Artist2/Album1/03.mp3",
        f"{test_root}/Artist3/Album2/01.mp3",
        f"{test_root}/Artist4/Album2/02.mp3",
        f"{test_root}/Artist4/Album2/03.mp3",
    ]

    assert generate_symlinks(va_paths_2_albums) == {
        "/foo/bar/baz/Artist1/Album1/01.mp3": {"/foo/bar/baz/Artist2/Album1/01.mp3"},
        "/foo/bar/baz/Artist1/Album1/02.mp3": {"/foo/bar/baz/Artist2/Album1/02.mp3"},
        "/foo/bar/baz/Artist2/Album1/03.mp3": {"/foo/bar/baz/Artist1/Album1/03.mp3"},
        "/foo/bar/baz/Artist3/Album2/01.mp3": {"/foo/bar/baz/Artist4/Album2/01.mp3"},
        "/foo/bar/baz/Artist4/Album2/02.mp3": {"/foo/bar/baz/Artist3/Album2/02.mp3"},
        "/foo/bar/baz/Artist4/Album2/03.mp3": {"/foo/bar/baz/Artist3/Album2/03.mp3"},
    }

    # return

    test_root = "/tmp"

    # (4-1) * 1 * 6 = "18"
    # in reality, (2-1)*1*3 + (2-1)*1*3 = 6
    # this must fail
    va_paths_2_albums_same = [
        f"{test_root}/Artist1/AlbumA/01 a.mp3",
        f"{test_root}/Artist1/AlbumA/02 b.mp3",
        f"{test_root}/Artist2/AlbumA/03 c.mp3",
        f"{test_root}/Artist3/AlbumA/01 d.mp3",
        f"{test_root}/Artist4/AlbumA/02 e.mp3",
        f"{test_root}/Artist4/AlbumA/03 f.mp3",
    ]
    with pytest.raises(ValueError) as e_info:
        generate_symlinks(va_paths_2_albums_same)
    assert (
        str(e_info.value)
        == "Multiple albums named 'AlbumA' detected. Manual resolution is required."
    )

    va_paths_2_albums_same = [
        f"{test_root}/Artist1/AlbumB/01 a.mp3",
        f"{test_root}/Artist1/AlbumB/02 b.mp3",
        f"{test_root}/Artist2/AlbumB/03 c.mp3",
        f"{test_root}/Artist2/AlbumB/04 d.mp3",
        f"{test_root}/Artist3/AlbumB/01 e.mp3",
        f"{test_root}/Artist4/AlbumB/02 f.mp3",
    ]
    with pytest.raises(ValueError) as e_info:
        generate_symlinks(va_paths_2_albums_same)
    assert (
        str(e_info.value)
        == "Multiple albums named 'AlbumB' detected. Manual resolution is required."
    )

    # x = 1 / 1
    # assert len(result) == 0
    # assert result == []

    # def make_dummy_files(files):
    #     from pathlib import Path
    #     for f in files:
    #         Path(os.path.dirname(f)).mkdir(parents=True, exist_ok=True)
    #         Path(f).touch()
    # make_dummy_files(va_paths_2x2)
