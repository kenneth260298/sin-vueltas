from sin_vueltas.ingest.chunk import MAX_CHUNK_CHARS, split_into_chunks

FAQ = """# Licencias de conducir

Requisitos para la renovación de la licencia en las oficinas del BCR. Debe presentar
la cédula de identidad física, vigente y en buen estado, y el dictamen médico digital.
El pago del entero se hace en cualquier oficina o por medios electrónicos autorizados.

## Primera vez

- Aprobación del curso teórico y la prueba práctica en Educación Vial.
- Documento de identidad físico, vigente y en buen estado.

## Duplicado

Si la licencia vence en más de tres meses no puede renovarse; debe pedir un duplicado."""


def words(text: str) -> list[str]:
    return text.split()


def test_short_text_is_one_chunk() -> None:
    chunks = split_into_chunks("Cédula vigente.\n\nDictamen médico.")

    assert [c.content for c in chunks] == ["Cédula vigente.\n\nDictamen médico."]
    assert chunks[0].heading is None


def test_each_section_is_a_chunk_labeled_with_its_heading_path() -> None:
    chunks = split_into_chunks(FAQ)

    assert [c.heading for c in chunks] == [
        "Licencias de conducir",
        "Licencias de conducir > Primera vez",
        "Licencias de conducir > Duplicado",
    ]
    assert chunks[2].content.startswith("## Duplicado")


def test_headings_without_text_in_between_stay_with_the_next_text() -> None:
    chunks = split_into_chunks("# Pasaporte\n\n## Requisitos\n\nCédula vigente.")

    assert [(c.heading, c.content) for c in chunks] == [
        ("Pasaporte > Requisitos", "# Pasaporte\n\n## Requisitos\n\nCédula vigente.")
    ]


def test_a_long_section_keeps_its_label_in_every_chunk() -> None:
    section = "## Requisitos\n\n" + "\n\n".join(["Cédula vigente y en buen estado."] * 30)

    chunks = split_into_chunks(section, max_chars=200)

    assert len(chunks) > 1
    assert {c.heading for c in chunks} == {"Requisitos"}


def test_no_text_is_lost_or_duplicated() -> None:
    chunks = split_into_chunks(FAQ)

    assert words(" ".join(c.content for c in chunks)) == words(FAQ)


def test_positions_are_sequential() -> None:
    chunks = split_into_chunks(FAQ, max_chars=200)

    assert [c.position for c in chunks] == list(range(len(chunks)))


def test_long_blocks_are_split_under_the_limit() -> None:
    sentence = "La persona debe presentar el documento de identidad vigente y en buen estado. "
    long_paragraph = sentence * 60  # About 4,700 characters with no blank lines.

    chunks = split_into_chunks(long_paragraph)

    assert len(chunks) > 1
    assert all(len(c.content) <= MAX_CHUNK_CHARS for c in chunks)
    assert words(" ".join(c.content for c in chunks)) == words(long_paragraph)


def test_a_word_longer_than_the_limit_is_cut() -> None:
    chunks = split_into_chunks("x" * 2500, max_chars=1000)

    assert all(len(c.content) <= 1000 for c in chunks)
    assert "".join(c.content for c in chunks) == "x" * 2500


def test_numbered_faq_questions_start_their_own_chunks() -> None:
    text = (
        "Preguntas frecuentes\n1. ¿Qué es TRIBU-CR?\nEs la nueva plataforma.\n"
        "2. ¿Cómo me inscribo?\nEn la Oficina Virtual."
    )

    chunks = split_into_chunks(text)

    assert [(c.heading, c.content) for c in chunks] == [
        (None, "Preguntas frecuentes"),
        ("1. ¿Qué es TRIBU-CR?", "1. ¿Qué es TRIBU-CR?\n\nEs la nueva plataforma."),
        ("2. ¿Cómo me inscribo?", "2. ¿Cómo me inscribo?\n\nEn la Oficina Virtual."),
    ]


def test_a_question_that_wraps_over_lines_stays_whole() -> None:
    chunks = split_into_chunks(
        "3. ¿Qué datos se ponen sobre la actividad\neconómica?\nEl código CIIU."
    )

    assert [c.heading for c in chunks] == ["3. ¿Qué datos se ponen sobre la actividad económica?"]


def test_questions_nest_under_markdown_headings() -> None:
    chunks = split_into_chunks("## Hacienda\n\n1. ¿Qué es TRIBU-CR?\nEs la plataforma.")

    assert [c.heading for c in chunks] == ["Hacienda > 1. ¿Qué es TRIBU-CR?"]


def test_numbered_lists_are_not_questions() -> None:
    chunks = split_into_chunks("## Requisitos\n\n1. Tener 65 años.\n2. Haber cotizado.")

    assert [c.heading for c in chunks] == ["Requisitos"]


def test_emphasis_is_removed_from_headings() -> None:
    chunks = split_into_chunks("## **Pasaporte**\n\nCédula vigente.")

    assert chunks[0].heading == "Pasaporte"


def test_form_leaders_are_collapsed() -> None:
    chunks = split_into_chunks("Nombre: ..........\nFirma: __________")

    assert chunks[0].content == "Nombre: …\nFirma: …"


def test_table_of_contents_lines_are_dropped() -> None:
    text = (
        "Índice\n1. ¿Qué es TRIBU-CR? …9\nInscripción …17\n2. ¿Cómo me\ninscribo? . 18\n\n"
        "1. ¿Qué es TRIBU-CR?\nEs la nueva plataforma."
    )

    chunks = split_into_chunks(text)

    assert [(c.heading, c.content) for c in chunks] == [
        (None, "Índice"),
        ("1. ¿Qué es TRIBU-CR?", "1. ¿Qué es TRIBU-CR?\n\nEs la nueva plataforma."),
    ]


def test_a_question_with_two_parts_that_wraps_stays_whole() -> None:
    chunks = split_into_chunks(
        "10. ¿Qué pasa con la cuenta? ¿Se bloquea la emisión de\nfacturas electrónicas?\n"
        "No, se mantiene."
    )

    assert chunks[0].heading == (
        "10. ¿Qué pasa con la cuenta? ¿Se bloquea la emisión de facturas electrónicas?"
    )


def test_many_headings_in_a_row_respect_the_size_limit() -> None:
    text = "\n".join(f"{i}. ¿Pregunta número {i}?" for i in range(1, 80))

    chunks = split_into_chunks(text)

    assert all(len(c.content) <= MAX_CHUNK_CHARS for c in chunks)
