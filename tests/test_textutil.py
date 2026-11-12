from rsvp.textutil import is_garbage, is_trustworthy, join_lines, quality

PROSE = "The tie-up on the table wasn't a typical corporate combination. Hershey makes Reese's peanut butter cups."
# Real samples from the Oreo PDF's broken text layer.
GARBAGE_SYMBOLS = '¡¢£¤¥¦¢§¨©¥ª¢¢«¬¦¥§«­¢¢®«¯°£±¯«£°²³«´¤°£®µ¥£±¢©¢¶ «©¤¨¨¢±¤£®¥°'
GARBAGE_CIPHER = "TUVWVXYZU[\\WU]^X_`a[XbX[^WcX^Wcde_ZdX]fV^ghWc^iX_`]c__c[^VXXj`cU[]"


def test_prose_is_clean():
    assert quality(PROSE) > 0.9
    assert not is_garbage(PROSE)
    assert is_trustworthy(PROSE)


def test_broken_encodings_are_garbage():
    assert is_garbage(GARBAGE_SYMBOLS)
    assert is_garbage(GARBAGE_CIPHER)
    assert is_garbage("-.� 01221� 314567� 8� 9:;<;=>6?:2� @.� ABCD6@1<:� E172<>;5� F;>� GH0 IJ=K")


def test_accented_nonsense_is_not_trusted():
    assert not is_trustworthy("ÃÄÅÆÈÉÊÉËÌ ÃÄÅÆÈ ÉÊÉËÌ ÄÅÆÈÉ")


def test_short_caption_not_misjudged():
    assert not is_garbage("Angus cattle in Nebraska last month.")


def test_number_tables_are_not_garbage():
    assert not is_garbage("431 483 520 542 569 591 628 647 666 681 695 713 726 720")
    assert not is_garbage("1,400")
    assert not is_garbage("“Peanut sauce is sticky business,” Chaclin said. (Really...)")


def test_hyphen_repair():
    assert join_lines(["crop disease was exacer-", "bated by rain"]) == "crop disease was exacerbated by rain"
    assert join_lines(["ample market-", "ready cattle"]) == "ample market-ready cattle"
    assert join_lines(["the American subsidiary of Anheuser-", "Busch InBev"]) == "the American subsidiary of Anheuser-Busch InBev"
    assert join_lines(["first line", "second line"]) == "first line second line"
    assert join_lines(["decades-old stalwarts—", "carefully managed"]) == "decades-old stalwarts—carefully managed"
