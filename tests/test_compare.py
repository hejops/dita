# import unittest
from streamlit.testing.v1 import AppTest

# https://docs.streamlit.io/library/api-reference/app-testing?ref=blog.streamlit.io
# https://github.com/AnOctopus/st-testing-demo/tree/fa8bcfe8ebbc90343f3c538d7a26af790e52bdf1
# https://github.com/DrBenjamin/Car_Pool/blob/fa1f831af6e8c1329b8c2d7a905e2e22ce3d0329/files/Testing.py

# generally, "what actions do i take?" become commands (which tend to be
# implicit assertions), e.g.
#
# 	at.radio[0].set_value('...').run()
#
# and "what results do i expect to see?" become assertions, e.g.
#
# 	assert at.dataframe[1].value ...


# class TestSuite(unittest.TestCase):
def test_all():
    # path can be relative to this file
    # note: if the file to be tested (testee) involves sys.argv in some
    # way, this file will be passed as sys.argv[1], which might mess with
    # the testee
    at = AppTest.from_file("../discogs/compare.py").run()

    # for x in [
    #     "session_state",  # <class 'streamlit.runtime.state.safe_session_state.SafeSessionState'>
    #     "_tree",  # <class 'streamlit.testing.v1.element_tree.ElementTree'>
    # ]:
    #     print(at.__dict__[x])

    assert len(at.radio) == 1
    assert len(at.radio[0].options) == 14

    # tests are primarily invoked via widget type, via index or key
    at.radio[0].set_value("bach-wtc1.csv").run()
    assert not at.exception

    # Checkbox(label='~Asperen~')
    # Checkbox(key='Asperen', label='~Asperen~') , ...

    # if you expect to test something, always give it a unique key
    at.checkbox("Gould").check()
    at.checkbox("Pollini").check()

    assert at.dataframe[1].value.shape == (24, 22)
    assert at.dataframe[2].value.iloc[0].name == "Suzuki"

    # print(
    #     # at.data_editor,  # data_editor is not testable (yet?)
    # )
    # assert False

    # # You can also query a widget by key to modify it or check the value
    # assert at.number_input(key="sample_size").value == 500
    #
    # # Set the value of the first number input in the sidebar
    # # (palette size) to 2, and re-run the app
    # at.sidebar.number_input[0].set_value(2).run()
    #
    # # Two color pickers are rendered in the second run
    # assert len(at.color_picker) == 2
