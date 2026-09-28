from piclstats.web.mail import mask_email


def test_mask_email_keeps_first_letter_and_domain():
    assert mask_email("chris@example.com") == "c***@example.com"
    assert mask_email("a@b.c") == "a***@b.c"
    assert mask_email("nonsense") == "***"
