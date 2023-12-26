from json.decoder import JSONDecodeError

import discogs.core as dc


# get_primary_url()
# # TODO: reject if primary newer than master?
# # https://www.discogs.com/release/1843975
# # TODO: primary has DVD
# # https://www.discogs.com/release/827219


def test_search_release():
    # https://www.discogs.com/artist/4337911-TVXQ!
    # https://www.discogs.com/artist/4983176-IOI

    # MC Eiht - Official

    for artist, album in {
        "yourboyfriendsucks!": "episode 01",
        "Yuju": "Rec",
        "MC Eiht": "Official",
        # "Elysiüm": "monarch elysiüm",  # unicode ok
        "Hopesfall": "The Satellite Years",
        "Monarch!": "Speak of the Sea",  # ! is ok here
        "Monarch": "Speak of the Sea",  # can also be removed
        # # "Gospel": "The Loser",
        # "Flo": "The Lead",  # short artist ok
        # "Godspeed You Black": "Yanqui",  # words with ! may be removed entirely
        # # "IOI": "Chrysalis",  # omitting .s is ok
        # # "IOI ": "Chrysalis",  # extra (non-intervening) spaces are ok
        # "..I..O..I..": "Chrysalis",  # any number of intervening .s are ok
    }.items():
        res = dc.search_release(
            artist,
            album,
            interactive=False,
            primary=True,
        )
        # print(res)
        assert res, artist

    for artist, album in {
        "": "Speak of the Sea",
        # main problem is when some artist names too short
        # "Ni": "Vorhees",  # artist name too short
        "TVXQ!": "Tense",  # ! not ok in artist
        # "Elysium": "monarch! elysium",  # ! not ok in album
        # "Godspeed You Black Emperor": "Yanqui",  # removing only the ! not ok
    }.items():
        res = dc.search_release(
            artist,
            album,
            interactive=False,
        )


def test_search_with_relpath():
    # Kayo Dot/Champions of Sound 2008 (2009)
    # 2181844

    # Louis Couperin/Suites De Clavecin - Tombeau De M. De Chambonnières [Kenneth Gilbert] (1992)

    # rel = dc.search_with_relpath("Uboa/Sometimes Light (2010)")
    # # pprint(rel)
    # assert rel["id"] == 12615179

    for rp in [
        # reject Draft
        "Attack Attack!/If Guns Are Outlawed, Can We Use Swords- (2008)",
        "Hopesfall/The Satellite Years (2002)",
        "Homeskin/Subverse Siphoning of Suburbia (2021)",
    ]:
        release = dc.search_with_relpath(rp)
        # print(release)
        # assert False
        # assert release["id"] == 810275
        assert "artists" in release
        assert release["status"] != "Draft"


def test_blocked_from_sale():
    # 'blocked_from_sale': can only be rated via API, not website
    # (this field is only present in release, not in collection)
    # what is the significance this? well, idk really...

    # https://www.discogs.com/release/6168289

    # from pprint import pprint

    assert dc.d_get("6168289")["blocked_from_sale"]


# def test_timeout():
#     for a in [
#         18956,  # Stevie Wonder, 543 (3.7k incl appearances)
#         95546,  # Mozart, 42k
#     ]:
#         for pg in [1, 2, 5, 10]:
#             for per in [100, 10, 500]:
#                 # try:
#                 assert dc.d_get(
#                     f"/artists/{a}/releases?sort=year&per_page={per}&page={pg}"
#                 )
#                 # print(p, per, "OK")
#                 # except JSONDecodeError:
#                 #     print(p, per, "Not OK")

# elif response.status_code != 200:
#     # e.g. 502 -- https://www.discogs.com/artist/95546-Wolfgang-Amadeus-Mozart
#     eprint(response.status_code)
#     os.system("notify-send " + str(response.status_code))
#     raise Exception


# crash?
# from search import search_discogs_release, display_releases
# artist = "mozart"
# album = "figaro"
# data = search_discogs_release(artist, album)
# ids = [str(x["id"]) for x in data]
# display_releases(ids)
# assert res == []
