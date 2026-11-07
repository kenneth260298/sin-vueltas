import pytest

from sin_vueltas.ingest.extract import ExtractionError, clean, extract_text, text_fingerprint

REQUIREMENTS = [
    "Presentar la cédula de identidad vigente y en buen estado.",
    "Aportar el dictamen médico digital vigente, emitido por un médico autorizado.",
    "Pagar el derecho de licencia en cualquier oficina del Banco de Costa Rica.",
    "Presentar el comprobante de la prueba práctica aprobada para el tipo de licencia.",
]


def page(token: str) -> bytes:
    """A realistic page: menus, a form with a per-visit token, a script and the content."""
    items = "".join(f"<li>{requirement}</li>" for requirement in REQUIREMENTS)
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Renovación de licencia</title>
<script>window.nonce = "{token}";</script></head>
<body>
  <nav><a href="/">Inicio</a> <a href="/tramites">Trámites</a></nav>
  <main><article>
    <h1>Renovación de licencia de conducir</h1>
    <p>Para renovar la licencia de conducir debe cumplir con los siguientes requisitos,
    que aplican para todas las personas que realizan el trámite en el país.</p>
    <ul>{items}</ul>
  </article></main>
  <form><input type="hidden" name="__VIEWSTATE" value="{token}"></form>
</body></html>""".encode()


def faq_page() -> bytes:
    """A FAQ page whose answers sit in collapsed accordion sections."""
    return """<!doctype html>
<html><head><title>Marchamo</title><script>var token = "abc123";</script></head>
<body>
  <header><a href="/">Inicio</a></header>
  <nav><ul><li>Seguros</li><li>Contacto</li></ul></nav>
  <main>
    <h1>Marchamo</h1>
    <div class="accordion">
      <button>¿Cuáles son los requisitos para el pago?</button>
      <div class="collapse" style="display: none">
        <ul>
          <li>Cédula de identidad del pagador</li>
          <li>Inspección <b>vehicular</b> vigente</li>
        </ul>
      </div>
      <button>¿Cuáles son los requisitos para el pago de incapacidad?</button>
      <div class="collapse"><p>Certificación de la CCSS.</p></div>
    </div>
  </main>
  <footer>Tipo de cambio: compra 447,00</footer>
</body></html>""".encode()


def make_pdf(text: str) -> bytes:
    """Build a minimal one-page PDF with ASCII text, without extra dependencies."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return pdf


def test_html_keeps_the_content() -> None:
    text = extract_text("html", page(token="abc123"))

    for requirement in REQUIREMENTS:
        assert requirement in text


def test_html_ignores_scripts_and_form_tokens() -> None:
    text = extract_text("html", page(token="abc123"))

    assert "abc123" not in text
    assert "__VIEWSTATE" not in text


def test_per_visit_tokens_do_not_change_the_fingerprint() -> None:
    first = extract_text("html", page(token="first-visit"))
    second = extract_text("html", page(token="second-visit"))

    assert text_fingerprint(first) == text_fingerprint(second)


def test_full_mode_keeps_collapsed_sections() -> None:
    text = extract_text("html", faq_page(), mode="full")

    assert "¿Cuáles son los requisitos para el pago?" in text
    assert "- Cédula de identidad del pagador" in text
    assert "- Inspección vehicular vigente" in text
    assert "Certificación de la CCSS." in text


def test_full_mode_drops_page_chrome() -> None:
    text = extract_text("html", faq_page(), mode="full")

    for chrome in ["Inicio", "Contacto", "Tipo de cambio", "abc123"]:
        assert chrome not in text


def test_full_mode_reads_pages_that_declare_another_charset() -> None:
    page = '<html><head><meta charset="windows-1252"></head><body><p>Cédula</p></body></html>'

    assert extract_text("html", page.encode("cp1252"), mode="full") == "Cédula"


def test_html_without_content_is_an_error() -> None:
    with pytest.raises(ExtractionError, match="no main text"):
        extract_text("html", b"<html><body><script>var x = 1;</script></body></html>")


def test_pdf_text_is_extracted() -> None:
    text = extract_text("pdf", make_pdf("Requisitos: cedula de identidad vigente"))

    assert text == "Requisitos: cedula de identidad vigente"


def test_pdf_without_text_layer_is_an_error() -> None:
    with pytest.raises(ExtractionError, match="needs OCR"):
        extract_text("pdf", make_pdf(""))


def test_damaged_pdf_is_an_error() -> None:
    with pytest.raises(ExtractionError, match="unreadable PDF"):
        extract_text("pdf", b"%PDF-1.4 this is not really a pdf")


def test_clean_normalizes_spacing_but_keeps_paragraphs() -> None:
    messy = "  Requisitos  del\ttrámite \n\n\n\n Cédula  "

    assert clean(messy) == "Requisitos del trámite\n\nCédula"


def test_fingerprint_ignores_spacing_but_not_content() -> None:
    assert text_fingerprint("Cédula  vigente\n") == text_fingerprint("Cédula vigente")
    assert text_fingerprint("Cédula vigente") != text_fingerprint("Cédula vencida")
