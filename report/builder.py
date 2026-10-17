"""
Report Builder module for converting session reportStructure JSON into an IEEE LaTeX object.
Follows IEEE conference style requirements and calls only public methods on ieee().
"""
import re
from django.core.exceptions import ValidationError, PermissionDenied
from ieee import ieee
from .models import UploadedImage, get_image_for_session

# Characters to escape in LaTeX prose and metadata
LATEX_ESCAPE_MAP = {
    '\\': r'\textbackslash{}',
    '{': r'\{',
    '}': r'\}',
    '&': r'\&',
    '%': r'\%',
    '$': r'\$',
    '#': r'\#',
    '_': r'\_',
    '~': r'\textasciitilde{}',
    '^': r'\textasciicircum{}',
}
LATEX_ESCAPE_PATTERN = re.compile(r'[\\{}&%$#_~^]')

FORBIDDEN_LATEX_COMMANDS = (
    "input", "include", "write", "immediate", "openin", "openout",
    "read", "catcode", "def", "csname", "usepackage", "documentclass"
)
FORBIDDEN_LATEX_PATTERN = re.compile(
    r'\\(' + '|'.join(FORBIDDEN_LATEX_COMMANDS) + r')(?![a-zA-Z])'
)


def escape_latex(text: str) -> str:
    """
    Escape LaTeX special characters (& % $ # _ { } ~ ^ \\).
    Must not be applied to equations, URLs, or bibitem keys.
    """
    if text is None:
        return ""
    return LATEX_ESCAPE_PATTERN.sub(lambda m: LATEX_ESCAPE_MAP[m.group(0)], str(text))


def strip_numbering(text: str) -> str:
    """
    Strip leading default numbering from titles and captions before use:
    e.g. '1.', '1.1', 'Section 2:', 'Figure 1:', 'Table 1:', 'Equation 1:'.
    Preserves bare years such as '2026 Outlook'.
    """
    if not text:
        return ""
    s = str(text).strip()
    # 1. Section/Figure/Table/Equation prefix
    s = re.sub(
        r'^(?:Section|Figure|Table|Equation|Fig\.|Tab\.|Eq\.)\s*\d+(?:\.\d+)*\s*[:.-]?\s*',
        '',
        s,
        flags=re.IGNORECASE
    )
    # 2. Multi-level numbering like 1.1, 1.2.3
    s = re.sub(r'^\d+(?:\.\d+)+\s*[:.-]?\s*', '', s)
    # 3. Single-level numbering with dot or colon, e.g. '1. ', '2: '
    # (Restricted to 1-2 digits followed by dot/colon so bare 4-digit years like '2026 Outlook' are preserved)
    s = re.sub(r'^\d{1,2}\s*[:.]\s*', '', s)
    return s.strip()


def convert_citations(text: str, num_refs: int) -> str:
    """
    After escaping prose, convert [N] to \\cite{refN}.
    Adjacent runs like [2][3] become one \\cite{ref2,ref3}.
    Numbers outside 1..num_refs and non-numeric brackets like [citation needed]
    are left unchanged.
    Applied ONLY to prose (section context, subsection content).
    """
    if not text or num_refs <= 0:
        return text

    pattern = re.compile(r'(?:\[\d+\])+')

    def replace_run(match):
        run = match.group(0)
        items = re.findall(r'\[(\d+)\]', run)
        result_parts = []
        current_valid_run = []

        def flush_valid():
            if current_valid_run:
                refs = [f"ref{n}" for n in current_valid_run]
                result_parts.append(r"\cite{" + ",".join(refs) + "}")
                current_valid_run.clear()

        for item in items:
            n = int(item)
            if 1 <= n <= num_refs:
                current_valid_run.append(n)
            else:
                flush_valid()
                result_parts.append(f"[{n}]")
        flush_valid()

        return "".join(result_parts)

    return pattern.sub(replace_run, text)


def validate_equation(eq_str: str) -> None:
    """
    Validate untrusted raw LaTeX equations:
    - Balanced curly braces (ignoring escaped \\{ and \\}).
    - Rejects dangerous commands (\\input, \\include, \\write, etc.).
    Raises ValidationError on failure.
    """
    if not eq_str or not isinstance(eq_str, str):
        raise ValidationError("Equation content cannot be empty.")

    match = FORBIDDEN_LATEX_PATTERN.search(eq_str)
    if match:
        raise ValidationError(f"Equation contains forbidden LaTeX command: \\{match.group(1)}")

    # Check balanced braces
    cleaned = eq_str.replace(r'\{', '').replace(r'\}', '')
    depth = 0
    for ch in cleaned:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth < 0:
                raise ValidationError("Equation has unbalanced curly braces (closing brace without opening brace).")
    if depth != 0:
        raise ValidationError("Equation has unbalanced curly braces (unclosed opening brace).")


def build_report(data: dict, images=None, session_key: str = None) -> ieee:
    """
    Build a fresh IEEE LaTeX document object from plain session JSON data.
    
    Parameters:
      data: Dictionary containing reportStructure:
            - title (str)
            - authors (list of {name, affiliation})
            - abstract (str)
            - keywords (str)
            - references (list of {title, link})
            - sections (list of {title, context, elements})
      images: Optional dict of {image_id: UploadedImage or bytes} or queryset.
      session_key: Optional session key for verifying image ownership.

    Returns:
      A freshly instantiated `ieee` object.
    """
    if not isinstance(data, dict):
        raise ValidationError("Report data must be a dictionary.")

    doc = ieee()
    table_count = 0

    # 1. Title
    raw_title = data.get("title", "")
    clean_title = strip_numbering(raw_title)
    doc.add_title(escape_latex(clean_title))

    # 2. Authors
    raw_authors = data.get("authors", [])
    author_names = []
    author_affiliations = []
    for author in raw_authors:
        if isinstance(author, dict):
            name = author.get("name", "")
            aff = author.get("affiliation", "")
        else:
            name = str(author)
            aff = ""
        author_names.append(escape_latex(name))
        author_affiliations.append(escape_latex(aff))

    num_authors = len(author_names)
    if num_authors > 0:
        doc.add_author(num_authors, author_names, author_affiliations)

    # 3. References metadata (used for citation bounds checking)
    references = data.get("references", [])
    num_refs = len(references)

    # 4. Abstract and Keywords
    raw_abstract = data.get("abstract", "")
    raw_keywords = data.get("keywords", "")

    clean_abstract = escape_latex(raw_abstract)
    clean_keywords = escape_latex(raw_keywords) if raw_keywords else None

    # Note: pass keywords only if non-empty, but if None pass "" to prevent ieee.py TypeError
    if clean_keywords:
        doc.add_abstract(contain=clean_abstract, keyword=clean_keywords)
    else:
        # ieee.py has a bug: if keyword is None, `self.abstract + sam1` crashes because sam1 is None.
        # Passing "" allows ieee.py to succeed when keywords are empty.
        doc.add_abstract(contain=clean_abstract, keyword="")

    # 5. Sections and elements
    sections = data.get("sections", [])
    images_dict = images if isinstance(images, dict) else {}

    for sec in sections:
        raw_sec_title = sec.get("title", "")
        sec_title = escape_latex(strip_numbering(raw_sec_title))

        raw_sec_context = sec.get("context", "")
        sec_context = convert_citations(escape_latex(raw_sec_context), num_refs=num_refs)

        doc.add_section(sec_title, sec_context)

        # Process ordered elements
        elements = sec.get("elements", [])
        for elem in elements:
            elem_type = elem.get("type")

            if elem_type == "subsection":
                sub_title = escape_latex(strip_numbering(elem.get("title", "")))
                sub_content = convert_citations(
                    escape_latex(elem.get("content", "")),
                    num_refs=num_refs
                )
                doc.add_subsection([sub_content], [sub_title])

            elif elem_type == "image":
                image_id = elem.get("image_id") or elem.get("id")
                if not image_id:
                    raise ValidationError("Image element is missing required 'image_id'.")

                # Retrieve image bytes and fmt
                img_bytes = None
                img_fmt = elem.get("imageType") or elem.get("fmt", "png")
                img_fmt = str(img_fmt).lower().lstrip(".").replace("jpeg", "jpg")

                if image_id in images_dict:
                    item = images_dict[image_id]
                    if isinstance(item, bytes):
                        img_bytes = item
                    elif isinstance(item, UploadedImage):
                        if item.is_expired:
                            raise ValidationError(f"Image ID '{image_id}' has expired.")
                        if session_key and item.session_key != session_key:
                            raise PermissionDenied(f"Image ID '{image_id}' belongs to another session.")
                        img_fmt = item.fmt
                        item.file.open('rb')
                        img_bytes = item.file.read()
                        item.file.close()
                else:
                    # Query DB with session ownership validation
                    if session_key:
                        uploaded_img = get_image_for_session(image_id, session_key)
                    else:
                        try:
                            uploaded_img = UploadedImage.objects.get(pk=image_id)
                        except (UploadedImage.DoesNotExist, ValueError):
                            raise ValidationError(f"Image ID '{image_id}' does not exist.")
                        if uploaded_img.is_expired:
                            raise ValidationError(f"Image ID '{image_id}' has expired.")

                    img_fmt = uploaded_img.fmt
                    uploaded_img.file.open('rb')
                    img_bytes = uploaded_img.file.read()
                    uploaded_img.file.close()

                if not img_bytes:
                    raise ValidationError(f"Unable to read image data for image ID '{image_id}'.")

                caption = escape_latex(strip_numbering(elem.get("caption", "")))
                raw_width = str(elem.get("width", "8cm") or "8cm").strip()
                width = re.sub(r'[\r\n{}]', '', raw_width).strip() or "8cm"
                doc.add_uploaded_image(data=img_bytes, caption=caption, fmt=img_fmt, width=width)

            elif elem_type == "table":
                table_count += 1
                caption = escape_latex(strip_numbering(elem.get("caption", "")))
                label = f"table{table_count}"

                raw_grid = elem.get("gridData", [])
                cols_count = int(elem.get("columnsCount") or (len(raw_grid[0]) if raw_grid else 1))
                rows_count = int(elem.get("rowsCount") or len(raw_grid))

                # Header is row 0
                raw_head = raw_grid[0] if raw_grid else []
                # Slice and pad header
                head = [escape_latex(str(cell)) for cell in raw_head[:cols_count]]
                head += [""] * max(0, cols_count - len(head))

                # Data rows are rows 1 to rows_count - 1
                data_rows = []
                for row_idx in range(1, rows_count):
                    raw_row = raw_grid[row_idx] if row_idx < len(raw_grid) else []
                    escaped_row = [escape_latex(str(cell)) for cell in raw_row[:cols_count]]
                    escaped_row += [""] * max(0, cols_count - len(escaped_row))
                    data_rows.append(escaped_row)

                doc.add_table(
                    columns=cols_count,
                    head=head,
                    data=data_rows,
                    caption=caption,
                    label=label
                )

            elif elem_type == "equation":
                eq_raw = elem.get("equation", "")
                validate_equation(eq_raw)
                doc.add_equation([eq_raw])

            else:
                raise ValidationError(f"Unsupported element type: '{elem_type}'.")

    # 6. References
    if num_refs > 0:
        ref_names = [f"ref{i + 1}" for i in range(num_refs)]
        ref_data = [
            escape_latex(ref.get("title", ""))
            for ref in references
        ]
        ref_links = [
            ref.get("link") if ref.get("link") else None
            for ref in references
        ]
        doc.add_reference(
            num=num_refs,
            name=ref_names,
            data=ref_data,
            link=ref_links
        )

    return doc
