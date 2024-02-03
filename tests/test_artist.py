import pytest

from dita.discogs.artist import Artist
from dita.discogs.artist import get_artist_id
from dita.discogs.artist import Label

# def get_transliterations(artist_id: str) -> list[str]:
#     # artist_id = 4126661


# def test_get_credits():
#     # roles
#     # https://www.discogs.com/artist/1412528
#     Artist(1412528)
#     assert False
#     # https://www.discogs.com/artist/2973076-Charli-Taft
#     Artist(2973076).get_credits()
#     assert False


def test_get_artist_id():
    # how to test in_collection?
    # https://www.discogs.com/artist/-Afterbirth-6
    artists = {
        # "Afterbirth": "3484414",  # has an exact match, but not in col
        # "Salem": 1,
        # "Jim O'Rourke": "3550",  # ' must not be removed
        "BoA": 112795,
        "Croatian Amor, Varg²™": 2488562,  # 2 main artists, forcibly get 1st one
        "Noise of Silence": 6413794,
        "Dearth": 7015816,
        # "Noise Of Silence": "6413794",
        # "Noise Of Silence": "6413794"
        # "Rock": "204457",
        # "Gospel": "206619",
        # "Flo": "1233913",
    }
    for a, a_id in artists.items():
        assert get_artist_id(a) == a_id


# @pytest.mark.skip()
def test_large_discog():
    # 18956,  # Stevie Wonder, 543 (3.7k incl appearances)

    mozart = Artist(95546)
    print(len(mozart))
    assert 1 <= mozart.page <= 436

    # smetana = Artist(833315)
    # assert 1 <= smetana.page <= 44

    zelenka = Artist(432500)
    assert zelenka.releases.id.iloc[0] == 7492957

    zelenka.navigate(1)
    assert zelenka.position == 1

    zelenka.navigate(1)
    assert zelenka.position == 2

    zelenka.navigate(1)
    assert zelenka.position == 3
    assert str(zelenka).startswith("Jan Dismas Zelenka [4/")

    zelenka.add_next_page()
    assert zelenka.position == 100
    assert str(zelenka).startswith("Jan Dismas Zelenka [101/")


def test_artist_chronology():
    # dearth = Artist(7015816)
    # dearth.filter_by_role(["Main"])
    # dearth.filter_by_format()

    # assert masuda.releases.title.iloc[0] == "ひとりが好き"
    # assert masuda.releases.year.iloc[0] == 1982

    cyls = Label(195387)
    print(cyls)
    assert str(cyls).startswith("Count Your Lucky Stars [1/")

    # https://www.discogs.com/artist/528726
    monarch = Artist(528726)
    assert monarch.releases.title.iloc[0] == "Monarch"
    assert monarch.releases.year.iloc[0] == 2004

    masuda = Artist(1202141)

    # filter_by_role is not applied by default
    assert masuda.releases.title.iloc[0] == "すずめ"
    assert masuda.releases.year.iloc[0] == 1981

    masuda.filter_by_role(["Main"])
    assert masuda.releases.title.iloc[0] == "すずめ"
    assert masuda.releases.year.iloc[0] == 1981

    masuda.filter_by_format()
    assert masuda.releases.title.iloc[0] == "ひとりが好き"
    assert masuda.releases.year.iloc[0] == 1982
