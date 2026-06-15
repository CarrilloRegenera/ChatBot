import logging
import re


NOISE_MAX_ALPHA_RATIO = 0.3
NOISE_PAGE_NUMBER_PATTERN = re.compile(r"^[\d\s\-â€“/.,:;()]+$")
NOISE_TOC_LINE_PATTERN = re.compile(r"\.{3,}\s*\d+\s*$")

# Deteccion de corrupcion +31 en PDFs con fuentes mal embebidas.
LINE_CORRUPT_PATTERN = re.compile(r"^[-0-9][A-Z\[\]]{3,}")
TRAILING_CODES_RE = re.compile(r"(\s+[A-Z0-9]{1,3}){1,4}\s*$")


def ocr_page_text(
    page,
    *,
    enabled: bool,
    pytesseract_module,
    pil_image_module,
    render_dpi: int,
    languages: str,
) -> str:
    if not (enabled and pytesseract_module is not None and pil_image_module is not None):
        return ""
    try:
        import io

        pix = page.get_pixmap(dpi=render_dpi, alpha=False)
        img = pil_image_module.open(io.BytesIO(pix.tobytes("png")))
        return pytesseract_module.image_to_string(img, lang=languages) or ""
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "OCR fallo en pagina %s: %s", getattr(page, "number", "?"), exc
        )
        return ""


def is_noise_chunk(text: str) -> bool:
    clean = text.strip()
    if len(clean) < 40:
        return True
    if NOISE_PAGE_NUMBER_PATTERN.match(clean):
        return True
    alpha_count = sum(1 for c in clean if c.isalpha())
    if alpha_count / max(len(clean), 1) < NOISE_MAX_ALPHA_RATIO:
        return True
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    if lines:
        toc_lines = sum(1 for line in lines if NOISE_TOC_LINE_PATTERN.search(line))
        if toc_lines / len(lines) > 0.5:
            return True
    words = clean.split()
    if len(words) >= 4 and len(set(w.lower() for w in words)) <= 2:
        return True
    return False


def normalize_rite_table31_text(text: str) -> str:
    if not text:
        return text
    normalized = text
    replacements = {
        "Tabla?Operacionesdemantenimientopreventivoysuperiodicidad": (
            "Tabla 3.1. Operaciones de mantenimiento preventivo y su periodicidad"
        ),
        "Limpiezadelosevaporadores": "Limpieza de los evaporadores",
        "Limpiezadeloscondensadores": "Limpieza de los condensadores",
        "RevisiÃ“ngeneraldecalderasdegas": "Revision general de calderas de gas",
        "RevisiÃ“ngeneraldecalderasdegasÃ“leo": "Revision general de calderas de gasoleo",
        "3FWJTJÃ“O\u0001HFOFSBM\u0001EF\u0001DBMEFSBT\u0001EF\u0001HBT": "Revision general de calderas de gas",
        "3FWJTJÃ“O HFOFSBM EF DBMEFSBT EF HBT": "Revision general de calderas de gas",
        "3FWJTJÃ“O\u0001HFOFSBM\u0001EF\u0001DBMEFSBT\u0001EF\u0001HBTÃ“MFP": "Revision general de calderas de gasoleo",
        "0QFSBDJÃ“O": "Operacion",
        "1FSJPEJDJEBE": "Periodicidad",
        "RevisiÃ“ndeloselementosdeseguridad": "Revision de los elementos de seguridad",
        "T VOBWF[DBEBTFNBOB": "S = una vez cada semana",
        "N VOBWF[BMNFT": "M = una vez al mes",
        "U VOBWF[QPSUFNQPSBEB": "U = una vez por temporada",
        "BÃ’P": "(aÃ±o)",
    }
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)

    compact = normalized.replace(" ", "")
    if (
        "Tabla3.1" in compact
        or "Tabla?Operacionesdemantenimientopreventivoysuperiodicidad" in text
        or "Limpiezadelosevaporadores" in compact
        or "Limpiezadeloscondensadores" in compact
        or "Revisiongeneraldecalderasdegas" in compact
        or "Revision general de calderas de gas" in normalized
        or "RevisiÃ³n general de calderas de gas" in normalized
    ):
        legend = (
            "Leyenda Tabla 3.1 RITE: en el texto extraido, U corresponde a "
            "una vez por temporada (aÃ±o). Si una fila aparece como U U, la "
            "periodicidad es una vez por temporada para ambas columnas de potencia."
        )
        if legend not in normalized:
            normalized = f"{normalized}\n{legend}"
    return normalized


def decode_chunk_corruption(
    text: str,
    source: str,
    *,
    shift31_source_tokens: frozenset[str],
    shift31_fulltext_source_tokens: frozenset[str],
) -> str:
    src = source.lower().replace("\\", "/")
    if not any(token in src for token in shift31_source_tokens):
        return text

    if any(token in src for token in shift31_fulltext_source_tokens):

        def _decode_fulltext_line(line: str) -> str:
            out: list[str] = []
            i = 0
            while i < len(line):
                c = line[i]
                if "A" <= c <= "Z":
                    j = i
                    while j < len(line) and "A" <= line[j] <= "Z":
                        j += 1
                    run = line[i:j]
                    if len(run) >= 4:
                        out.append(
                            "".join(
                                chr(ord(ch) + 31) if 32 <= ord(ch) + 31 <= 126 else ch
                                for ch in run
                            )
                        )
                    else:
                        out.append(run)
                    i = j
                else:
                    out.append(c)
                    i += 1
            return "".join(out)

        return "\n".join(_decode_fulltext_line(line) for line in text.split("\n"))

    lines = text.split("\n")
    changed = False
    result = []
    for line in lines:
        stripped = line.strip()
        if LINE_CORRUPT_PATTERN.match(stripped):
            trail = TRAILING_CODES_RE.search(stripped)
            if trail:
                name_part = stripped[: trail.start()]
                code_part = stripped[trail.start():]
            else:
                name_part = stripped
                code_part = ""
            decoded_name = "".join(
                chr(ord(c) + 31) if 32 <= ord(c) + 31 <= 126 else c for c in name_part
            )
            result.append(decoded_name + code_part)
            changed = True
        else:
            result.append(line)
    decoded = "\n".join(result) if changed else text
    return normalize_rite_table31_text(decoded)
