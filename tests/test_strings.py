import dita.discogs.core as dc
from dita.discogs import release
from dita.tag.core import align_lists
from dita.tag.core import is_ascii
from dita.tag.core import open_url


# https://www.discogs.com/release/12168132
def test_is_ascii():
    assert not is_ascii("Тамара Гвердцители, Дмитрий Дюжев")


def test_release_print():
    rel = dc.d_get(15882406)

    assert (
        release.get_release_tracklist(rel).title.iloc[0]
        == "Die Kunst Der Fuge, BWV 1080 - Contrapunctus 1"
    )

    assert (
        release.get_discogs_tags(rel).title.iloc[0]
        == "Die Kunst Der Fuge, BWV 1080 - Contrapunctus 1"
    )

    assert "{" not in release.release_as_str(rel)
    assert (
        "\n".join(release.release_as_str(rel).split("\n")[:2])
        == """\
    index position  type_                                                                                                 title duration tracknumber  dur
1       0           index                                                        Die Kunst Der Fuge, BWV 1080 - Contrapunctus 1     2:57          01  177\
"""
    )


def test_list_diff():
    left = ["aaa", "bbb", "ccc", "ddd", "eee"]
    right = ["aaa", "ccc", "eee", "fff"]
    assert align_lists(left, right) == (
        ["aaa", "bbb", "ccc", "ddd", "eee", None],
        ["aaa", None, "ccc", None, "eee", "fff"],
    )

    left = ["aaa", "bbb", "ccc", "ddd", "eee"]
    right = ["aaa", "xxx", "ccc", "eee", "fff"]
    assert align_lists(left, right) == (
        ["aaa", "bbb", "ccc", "ddd", "eee", None],
        ["aaa", "xxx", "ccc", None, "eee", "fff"],
    )

    left = ["aaa", "bbb", "yyy", "ccc", "ddd", "eee"]
    right = ["aaa", "xxx", "ccc", "eee", "fff"]
    assert align_lists(left, right) == (
        ["aaa", "bbb", "yyy", "ccc", "ddd", "eee", None],
        ["aaa", "xxx", None, "ccc", None, "eee", "fff"],
    )

    left = ["aaa", "aaa", "ccc", "ddd", "eee"]
    right = ["aaa", "xxx", "ccc", "eee", "fff"]
    assert align_lists(left, right) == (
        ["aaa", "aaa", "ccc", "ddd", "eee", None],
        ["aaa", "xxx", "ccc", None, "eee", "fff"],
    )

    left = ["aaa", "aaa", "ccc", "ddd", "aaa"]
    right = ["aaa", "xxx", "ccc", "eee", "fff"]
    assert align_lists(left, right) == (
        ["aaa", "aaa", "ccc", "ddd", "aaa"],
        ["aaa", "xxx", "ccc", "eee", "fff"],
    )

    # left = ["aaa", "aaa", "ccc", "ddd", "yyy", "aaa"]
    # right = ["aaa", "xxx", "ccc", "eee", "aaa"]
    # assert align_lists(left, right) == (
    #     ["aaa", "aaa", "ccc", "ddd", "yyy", "aaa"],
    #     ["aaa", "xxx", "ccc", "eee", None, "aaa"],
    # )


def test_open_url():
    assert (
        open_url(
            "https://www.discogs.com/release/8502088-Uboa-Sometimes-Light",
            simulate=True,
        )
        == "https://www.discogs.com/release/8502088-Uboa-Sometimes-Light"
    )

    assert (
        open_url(
            "https://www.discogs.com/release",
            suffix="words that go after url",
            simulate=True,
        )
        == "https://www.discogs.com/release/words that go after url"
    )

    assert (
        open_url(
            "https://www.discogs.com/release",
            suffix="'words' that go after url",
            simulate=True,
        )
        # idk if real urls should look like this
        == "https://www.discogs.com/release/'words' that go after url"
    )

    assert (
        open_url(
            "https://www.discogs.com/release/",  # extra /
            suffix="words that go after url",
            simulate=True,
        )
        == "https://www.discogs.com/release/words that go after url"
    )

    # assert (
    #     open_url(
    #         "https://www.discogs.com/release//",
    #         suffix="words that go after url",
    #         simulate=True,
    #     )
    #     == "https://www.discogs.com/release/words that go after url"
    # )

    assert (
        open_url(
            "https://www.discogs.com/release/",
            ["search", "queries"],
            suffix="words that go after url",
            simulate=True,
        )
        == "https://www.discogs.com/release/search%20queries/words that go after url"
    )


def test_clean_artist():
    # TODO: https://www.discogs.com/release/10700391
    artists = {
        "Beatles, The": "The Beatles",
        "Morton Feldman - Turfan Ensemble, The, Philipp Vandré": (
            "Morton Feldman, The Turfan Ensemble, Philipp Vandré"
        ),
        # corner case: discogs artist name is "The Beach Boys", but a user
        # omits it for the release name, and adds an extra hardcoded "The"
        # https://www.discogs.com/release/1477527
        "Mike Love, Bruce Johnston, David Marks Of The Beach Boys, The": (
            "Mike Love, Bruce Johnston, David Marks of the Beach Boys"
        ),
        # i don't have a solution for this, there is no isolated 'The'
        "Oval Five, The Featuring Natacha Atlas": (
            "Oval Five, The Featuring Natacha Atlas"
        ),
    }
    for key, val in artists.items():
        assert dc.clean_artist(key) == val


def test_remove_words():
    for k, v in {
        "a ep": "a",
        "a lp ep": "a",
        "a ep lp": "a",
    }.items():
        assert dc.remove_words(k) == v


def test_parse_string_num_range():
    # https://www.discogs.com/release/18627367
    #
    # url = "https://www.dc.com/release/4646504"
    # rel = dc.d_get(url)
    # pprint([a["tracks"] for a in rel["extraartists"] if "ompos" in a["role"]])

    # # multi-disc requires disc 1 to be processed fully
    # # then len of disc 1
    # ranges = [
    #     "2-1",
    #     "1-12",
    #     "2-2 to 2-7",
    #     "1-1 to 1-11",
    # ]
    # print(x := sorted(ranges)[0])

    # track range, e.g. "3-7"
    # https://www.discogs.com/release/15047001
    assert dc.parse_string_num_range("3-7") == [3, 4, 5, 6, 7]
    assert dc.parse_string_num_range("3-7, 9-10") == [3, 4, 5, 6, 7, 9, 10]
    assert dc.parse_string_num_range("3 to 7") == [3, 4, 5, 6, 7]
    assert dc.parse_string_num_range("3 to 7, 9 to 10") == [
        3,
        4,
        5,
        6,
        7,
        9,
        10,
    ]

    # strip disc prefix
    assert dc.parse_string_num_range("1-1 to 1-4") == [1, 2, 3, 4]

    # dc.parse_string_num_range(x)

    # assert release.process_release(rel).tracknumber.apply(
    #     lambda x: x.lstrip("0")
    # ).to_list() == list(range(20))
    # assert dc.parse_string_num_range()

    # print(dc.parse_string_num_range("1-1 to 1-12"))

    # print(url, tl)

    # # TODO: "1-1 to 1-11" utter hell...
    # url = "https://www.dc.com/release/5177263"
    # print(url)
