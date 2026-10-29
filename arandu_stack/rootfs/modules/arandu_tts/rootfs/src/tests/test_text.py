from arandu_tts.text import SentenceBuffer, normalize_text


def test_clean_text():
    assert normalize_text(" Olá\n\n mundo \x00 ! ") == "Olá mundo !"
    assert normalize_text(" ... ") == ""


def test_sentence_stream():
    sb = SentenceBuffer(max_chars=30)
    assert sb.push("Aran") == []
    assert sb.push("du, olá! A luz está") == ["Arandu, olá!"]
    assert sb.push(" ligada. ") == ["A luz está ligada."]
    assert sb.finish() == ""


def test_chunk_prevents_stall():
    sb = SentenceBuffer(max_chars=30)
    result = sb.push("tudo " * 14)
    assert len(result) >= 2
    assert sb.finish() != ""


def test_partial_final():
    sb = SentenceBuffer()
    assert sb.push("A temperatura é vinte") == []
    assert sb.finish() == "A temperatura é vinte"
