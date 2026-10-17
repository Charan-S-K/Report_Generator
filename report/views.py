"""
Views for the report generator application.
"""
import json
import time
import re
import logging
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie
from django.core.exceptions import ValidationError, PermissionDenied
from report.models import UploadedImage, get_image_for_session
from report.builder import build_report, validate_equation
from report.dynamicdocs import (
    compile_pdf,
    DynamicDocsCompileError,
    DynamicDocsTimeoutError,
    DynamicDocsConfigError,
    DynamicDocsError,
)

logger = logging.getLogger(__name__)


@ensure_csrf_cookie
def index(request):
    """
    Renders the report generator frontend single-page application.
    Supports named SPA routes: index, reference, abstract, context, generate.
    """
    url_name = getattr(request.resolver_match, 'url_name', 'reference')
    if url_name in ('index', 'reference'):
        initial_view = 'reference'
    elif url_name in ('abstract', 'context', 'generate'):
        initial_view = url_name
    else:
        path = request.path.lower()
        if 'abstract' in path:
            initial_view = 'abstract'
        elif 'context' in path:
            initial_view = 'context'
        elif 'generate' in path:
            initial_view = 'generate'
        else:
            initial_view = 'reference'

    context = {
        'initial_view': initial_view,
        'report_data_json': 'null',
    }
    return render(request, 'index.html', context)


@require_GET
def health_check(request):
    """
    Health check endpoint returning system status and confirming sessions are active.
    """
    if not request.session.session_key:
        request.session.save()

    return JsonResponse({
        "status": "ok",
        "session_key": request.session.session_key,
    })


@require_http_methods(["GET", "POST"])
def references_view(request):
    """
    Save and load report references in request.session.
    GET: returns references currently stored in the session.
    POST: accepts two lists (titles, links) of equal length.
          Missing links are stored as "".
          Keys are generated as ref1, ref2, ... in order.
          Stored in request.session["references"] as a plain JSON list of dicts.
    """
    if request.method == "GET":
        refs = request.session.get("references", [])
        return JsonResponse({
            "status": "success",
            "references": refs,
            "titles": [r.get("title", "") for r in refs],
            "links": [r.get("link", "") for r in refs],
            "count": len(refs),
        })

    # POST: Save references
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    # Support both {titles: [...], links: [...]} and {references: [{title, link}, ...]}
    if "titles" not in payload and "references" in payload and isinstance(payload["references"], list):
        raw_list = payload["references"]
        titles = [r.get("title", "") if isinstance(r, dict) else str(r) for r in raw_list]
        links = [r.get("link", "") if isinstance(r, dict) else "" for r in raw_list]
    else:
        titles = payload.get("titles")
        links = payload.get("links")

    if not isinstance(titles, list) or not isinstance(links, list):
        return JsonResponse(
            {"error": "'titles' and 'links' must both be lists."},
            status=400,
        )

    if len(titles) != len(links):
        return JsonResponse(
            {
                "error": (
                    f"Length mismatch: 'titles' has {len(titles)} items "
                    f"but 'links' has {len(links)} items."
                )
            },
            status=400,
        )

    stored_refs = []
    for idx, (title, link) in enumerate(zip(titles, links), start=1):
        key = f"ref{idx}"
        clean_title = str(title).strip() if title is not None else ""
        clean_link = str(link).strip() if link is not None else ""
        stored_refs.append({
            "id": key,
            "key": key,
            "title": clean_title,
            "link": clean_link,
        })

    request.session["references"] = stored_refs
    request.session.modified = True

    return JsonResponse({
        "status": "success",
        "references": stored_refs,
        "count": len(stored_refs),
    })


@require_http_methods(["GET", "POST"])
def title_authors_view(request):
    """
    Save and load title (string) and authors (list of {name, affiliation}) in request.session.
    GET: returns title and authors currently stored in the session.
    POST: accepts title (string) and authors (list of {name, affiliation}).
          Stores in request.session["title"] and request.session["authors"].
    """
    if request.method == "GET":
        title = request.session.get("title", "")
        authors = request.session.get("authors", [])
        return JsonResponse({
            "status": "success",
            "title": title,
            "authors": authors,
            "count": len(authors),
        })

    # POST: Save title and authors
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    # Validate title
    raw_title = payload.get("title", "")
    if raw_title is None:
        title = ""
    elif not isinstance(raw_title, str):
        return JsonResponse({"error": "'title' must be a string."}, status=400)
    else:
        title = raw_title.strip()

    # Validate authors
    stored_authors = []
    if "authors" in payload:
        raw_authors = payload.get("authors")
        if not isinstance(raw_authors, list):
            return JsonResponse({"error": "'authors' must be a list."}, status=400)

        for idx, item in enumerate(raw_authors, start=1):
            if not isinstance(item, dict):
                return JsonResponse(
                    {"error": f"Author at index {idx - 1} must be an object with 'name' and 'affiliation'."},
                    status=400,
                )
            name = str(item.get("name", "") or "").strip()
            affiliation = str(item.get("affiliation", "") or "").strip()
            stored_authors.append({
                "name": name,
                "affiliation": affiliation,
            })
    elif "names" in payload and "affiliations" in payload:
        names = payload.get("names")
        affiliations = payload.get("affiliations")
        if not isinstance(names, list) or not isinstance(affiliations, list):
            return JsonResponse({"error": "'names' and 'affiliations' must both be lists."}, status=400)
        if len(names) != len(affiliations):
            return JsonResponse(
                {"error": f"Length mismatch: 'names' has {len(names)} items but 'affiliations' has {len(affiliations)} items."},
                status=400,
            )
        for name, aff in zip(names, affiliations):
            stored_authors.append({
                "name": str(name or "").strip(),
                "affiliation": str(aff or "").strip(),
            })
    else:
        stored_authors = []

    request.session["title"] = title
    request.session["authors"] = stored_authors
    request.session.modified = True

    return JsonResponse({
        "status": "success",
        "title": title,
        "authors": stored_authors,
        "count": len(stored_authors),
    })


@require_http_methods(["GET", "POST"])
def abstract_keywords_view(request):
    """
    Save and load abstract (string) and keywords (string) in request.session.
    GET: returns abstract and keywords currently stored in the session.
    POST: accepts abstract (string) and keywords (string or list of strings).
          Stores raw in request.session["abstract"] and request.session["keywords"].
    """
    if request.method == "GET":
        abstract = request.session.get("abstract", "")
        keywords = request.session.get("keywords", "")
        return JsonResponse({
            "status": "success",
            "abstract": abstract,
            "keywords": keywords,
        })

    # POST: Save abstract and keywords
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    # Validate abstract
    raw_abstract = payload.get("abstract", "")
    if raw_abstract is None:
        abstract = ""
    elif not isinstance(raw_abstract, str):
        return JsonResponse({"error": "'abstract' must be a string."}, status=400)
    else:
        abstract = raw_abstract.strip()

    # Validate keywords
    raw_keywords = payload.get("keywords", "")
    if raw_keywords is None:
        keywords = ""
    elif isinstance(raw_keywords, list):
        keywords = ", ".join(str(k).strip() for k in raw_keywords if str(k).strip())
    elif isinstance(raw_keywords, str):
        keywords = raw_keywords.strip()
    else:
        return JsonResponse({"error": "'keywords' must be a string or list of strings."}, status=400)

    request.session["abstract"] = abstract
    request.session["keywords"] = keywords
    request.session.modified = True

    return JsonResponse({
        "status": "success",
        "abstract": abstract,
        "keywords": keywords,
    })


VALID_ELEMENT_TYPES = {"subsection", "image", "table", "equation"}


@require_http_methods(["GET", "POST"])
def sections_view(request):
    """
    Save and load sections list with ordered elements in request.session.
    GET: returns sections currently stored in the session.
    POST: accepts sections list, validates element types, and preserves element order exactly.
          Stored in request.session["sections"].
    """
    if request.method == "GET":
        sections = request.session.get("sections", [])
        return JsonResponse({
            "status": "success",
            "sections": sections,
            "count": len(sections),
        })

    # POST: Save sections
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    # Support {sections: [...]} or directly a list of sections
    if isinstance(payload, list):
        raw_sections = payload
    elif isinstance(payload, dict) and "sections" in payload:
        raw_sections = payload.get("sections")
    else:
        return JsonResponse({"error": "Payload must include a 'sections' list."}, status=400)

    if not isinstance(raw_sections, list):
        return JsonResponse({"error": "'sections' must be a list."}, status=400)

    stored_sections = []
    for sec_idx, sec in enumerate(raw_sections, start=1):
        if not isinstance(sec, dict):
            return JsonResponse(
                {"error": f"Section at index {sec_idx - 1} must be an object."},
                status=400,
            )

        sec_title = str(sec.get("title", "") or "").strip()
        sec_context = str(sec.get("context", "") or "").strip()
        raw_elements = sec.get("elements", [])

        if not isinstance(raw_elements, list):
            return JsonResponse(
                {"error": f"Section {sec_idx} ('{sec_title}'): 'elements' must be a list."},
                status=400,
            )

        stored_elements = []
        for elem_idx, elem in enumerate(raw_elements, start=1):
            if not isinstance(elem, dict):
                return JsonResponse(
                    {"error": f"Section {sec_idx}, element {elem_idx} must be an object."},
                    status=400,
                )

            elem_type = elem.get("type")
            if not elem_type or elem_type not in VALID_ELEMENT_TYPES:
                valid_types_str = ", ".join(sorted(VALID_ELEMENT_TYPES))
                return JsonResponse(
                    {
                        "error": (
                            f"Section {sec_idx}, element {elem_idx}: invalid or missing type "
                            f"'{elem_type}'. Must be one of: {valid_types_str}."
                        )
                    },
                    status=400,
                )

            if elem_type == "subsection":
                sub_title = str(elem.get("title", "") or "").strip()
                sub_content = str(elem.get("content", "") or "").strip()
                stored_elements.append({
                    "type": "subsection",
                    "title": sub_title,
                    "content": sub_content,
                })

            elif elem_type == "image":
                caption = str(elem.get("caption", "") or "").strip()
                raw_width = str(elem.get("width", "8cm") or "8cm").strip()
                width = re.sub(r'[\r\n{}]', '', raw_width).strip() or "8cm"
                raw_fmt = elem.get("imageType") or elem.get("fmt", "png")
                fmt = str(raw_fmt).lower().lstrip(".").replace("jpeg", "jpg")
                if fmt not in ("jpg", "png"):
                    return JsonResponse(
                        {
                            "error": (
                                f"Section {sec_idx}, image element {elem_idx}: "
                                f"imageType must be 'jpg' or 'png', got '{raw_fmt}'."
                            )
                        },
                        status=400,
                    )
                image_id = elem.get("image_id") or elem.get("id")
                stored_elements.append({
                    "type": "image",
                    "caption": caption,
                    "width": width,
                    "imageType": fmt,
                    "image_id": image_id,
                })

            elif elem_type == "table":
                caption = str(elem.get("caption", "") or "").strip()
                raw_grid = elem.get("gridData", [])
                if not isinstance(raw_grid, list):
                    return JsonResponse(
                        {
                            "error": (
                                f"Section {sec_idx}, table element {elem_idx}: "
                                "'gridData' must be a list of rows."
                            )
                        },
                        status=400,
                    )
                for row_idx, row in enumerate(raw_grid, start=1):
                    if not isinstance(row, list):
                        return JsonResponse(
                            {
                                "error": (
                                    f"Section {sec_idx}, table element {elem_idx}: "
                                    f"row {row_idx} of 'gridData' must be a list."
                                )
                            },
                            status=400,
                        )

                cols_count = elem.get("columnsCount")
                rows_count = elem.get("rowsCount")
                if cols_count is None:
                    cols_count = max((len(r) for r in raw_grid), default=1) if raw_grid else 1
                if rows_count is None:
                    rows_count = len(raw_grid)

                stored_elements.append({
                    "type": "table",
                    "caption": caption,
                    "columnsCount": int(cols_count),
                    "rowsCount": int(rows_count),
                    "gridData": raw_grid,
                })

            elif elem_type == "equation":
                caption = str(elem.get("caption", "") or "").strip()
                equation_str = str(elem.get("equation", "") or "")
                stored_elements.append({
                    "type": "equation",
                    "caption": caption,
                    "equation": equation_str,
                })

        stored_sections.append({
            "title": sec_title,
            "context": sec_context,
            "elements": stored_elements,
        })

    request.session["sections"] = stored_sections
    request.session.modified = True

    return JsonResponse({
        "status": "success",
        "sections": stored_sections,
        "count": len(stored_sections),
    })


MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
PNG_MAGIC_BYTES = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC_BYTES = b"\xff\xd8\xff"


@require_POST
def upload_image_view(request):
    """
    Handle multipart image upload.
    Validates:
      - 'image' file present in request.FILES.
      - 'fmt' field present and in ('jpg', 'png').
      - file size <= 10 MB.
      - magic bytes match the specified fmt.
    Associates image with current session_key.
    Returns JSON with image id, format, size, and expiration.
    """
    if "image" not in request.FILES:
        return JsonResponse({"error": "No image file provided in request.FILES['image']."}, status=400)

    image_file = request.FILES["image"]

    raw_fmt = request.POST.get("fmt", "")
    if not raw_fmt:
        ext = image_file.name.rsplit(".", 1)[-1] if "." in image_file.name else ""
        raw_fmt = ext

    fmt = str(raw_fmt).lower().lstrip(".").replace("jpeg", "jpg").strip()
    if fmt not in ("jpg", "png"):
        return JsonResponse(
            {"error": f"Invalid format '{raw_fmt}'. Format 'fmt' must be 'jpg' or 'png'."},
            status=400,
        )

    # Validate size <= 10 MB
    if image_file.size > MAX_IMAGE_SIZE_BYTES:
        return JsonResponse(
            {
                "error": (
                    f"File size ({image_file.size} bytes) exceeds the maximum allowed "
                    f"limit of 10 MB ({MAX_IMAGE_SIZE_BYTES} bytes)."
                )
            },
            status=400,
        )

    # Validate magic bytes
    image_file.seek(0)
    header = image_file.read(8)
    image_file.seek(0)

    if fmt == "png":
        if header != PNG_MAGIC_BYTES:
            return JsonResponse(
                {"error": "File does not match valid PNG magic bytes (89 50 4E 47 0D 0A 1A 0A)."},
                status=400,
            )
    elif fmt == "jpg":
        if header[:3] != JPEG_MAGIC_BYTES:
            return JsonResponse(
                {"error": "File does not match valid JPEG magic bytes (FF D8 FF)."},
                status=400,
            )

    # Ensure session key exists
    if not request.session.session_key:
        request.session.save()

    session_key = request.session.session_key

    # Save to database and filesystem
    uploaded_image = UploadedImage.objects.create(
        session_key=session_key,
        file=image_file,
        fmt=fmt,
        size=image_file.size,
    )

    return JsonResponse({
        "status": "success",
        "image_id": uploaded_image.id,
        "fmt": uploaded_image.fmt,
        "size": uploaded_image.size,
        "created_at": uploaded_image.created_at.isoformat(),
        "expires_at": uploaded_image.expires_at.isoformat(),
    }, status=201)


RATE_LIMIT_COOLDOWN_SECONDS = 10  # At most 1 request per 10 seconds
RATE_LIMIT_MAX_PER_HOUR = 10      # At most 10 requests per hour


def check_rate_limit(session):
    """
    Check per-session rate limits:
      - At most 1 request per 10 seconds.
      - At most 10 requests per hour.
    Returns (error_message, status_code) tuple if rate-limited, otherwise None.
    """
    now = time.time()
    history = session.get("pdf_generation_history", [])

    # 1. Check 10-second cooldown
    if history:
        last_request_time = history[-1]
        elapsed = now - last_request_time
        if elapsed < RATE_LIMIT_COOLDOWN_SECONDS:
            remaining = int(RATE_LIMIT_COOLDOWN_SECONDS - elapsed) + 1
            return (
                f"Rate limit exceeded: Please wait {remaining} second(s) before requesting another PDF generation.",
                429
            )

    # 2. Check 10 requests per hour limit
    recent_history = [t for t in history if (now - t) < 3600]
    if len(recent_history) >= RATE_LIMIT_MAX_PER_HOUR:
        return (
            "Rate limit exceeded: At most 10 PDF generations allowed per hour. Please try again later.",
            429
        )

    return None


def record_rate_limit_request(session):
    """Record current timestamp in session PDF generation history."""
    now = time.time()
    history = session.get("pdf_generation_history", [])
    recent_history = [t for t in history if (now - t) < 3600]
    recent_history.append(now)
    session["pdf_generation_history"] = recent_history
    session.modified = True


def validate_report_data(session_data, session_key):
    """
    Validate all session report data before generating the document:
      - Title present and non-empty.
      - At least one section present.
      - Image IDs valid, belong to this session, and not expired.
      - Table sizes consistent.
      - Equations valid (balanced braces, no forbidden LaTeX commands).

    Returns (errors_list, valid_images_dict).
    """
    errors = []
    valid_images = {}

    # 1. Title validation
    title = str(session_data.get("title", "") or "").strip()
    if not title:
        errors.append("Document title is required and cannot be empty.")

    # 2. Sections validation
    sections = session_data.get("sections", [])
    if not isinstance(sections, list) or len(sections) == 0:
        errors.append("Document must contain at least one section.")
    else:
        for sec_idx, sec in enumerate(sections, start=1):
            if not isinstance(sec, dict):
                errors.append(f"Section {sec_idx} must be an object.")
                continue

            sec_title = str(sec.get("title", "") or "").strip()
            sec_context = str(sec.get("context", "") or "").strip()
            elements = sec.get("elements", [])

            if not sec_title and not sec_context and not elements:
                errors.append(f"Section {sec_idx} has no title, context, or elements.")

            if not isinstance(elements, list):
                errors.append(f"Section {sec_idx}: 'elements' must be a list.")
                continue

            for elem_idx, elem in enumerate(elements, start=1):
                if not isinstance(elem, dict):
                    errors.append(f"Section {sec_idx}, element {elem_idx} must be an object.")
                    continue

                elem_type = elem.get("type")

                if elem_type == "image":
                    image_id = elem.get("image_id") or elem.get("id")
                    if not image_id:
                        errors.append(
                            f"Section {sec_idx}, image element {elem_idx}: missing required 'image_id'."
                        )
                    else:
                        try:
                            img = get_image_for_session(image_id, session_key)
                            valid_images[image_id] = img
                        except (ValidationError, PermissionDenied) as e:
                            errors.append(f"Section {sec_idx}, image element {elem_idx}: {e}")

                elif elem_type == "table":
                    cols_count = elem.get("columnsCount")
                    rows_count = elem.get("rowsCount")
                    grid_data = elem.get("gridData", [])

                    if not isinstance(grid_data, list) or len(grid_data) == 0:
                        errors.append(
                            f"Section {sec_idx}, table element {elem_idx}: table grid data cannot be empty."
                        )
                    else:
                        if cols_count is not None and int(cols_count) <= 0:
                            errors.append(
                                f"Section {sec_idx}, table element {elem_idx}: 'columnsCount' must be greater than 0."
                            )
                        if rows_count is not None and int(rows_count) <= 0:
                            errors.append(
                                f"Section {sec_idx}, table element {elem_idx}: 'rowsCount' must be greater than 0."
                            )
                        for r_idx, row in enumerate(grid_data):
                            if not isinstance(row, list):
                                errors.append(
                                    f"Section {sec_idx}, table element {elem_idx}: row {r_idx + 1} of 'gridData' must be a list."
                                )
                                break

                elif elem_type == "equation":
                    eq_str = str(elem.get("equation", "") or "").strip()
                    if not eq_str:
                        errors.append(
                            f"Section {sec_idx}, equation element {elem_idx}: equation content cannot be empty."
                        )
                    else:
                        try:
                            validate_equation(eq_str)
                        except ValidationError as e:
                            msg = e.message if hasattr(e, "message") else str(e)
                            errors.append(f"Section {sec_idx}, equation element {elem_idx}: {msg}")

    return errors, valid_images


def sanitize_filename(title: str, max_length: int = 60, fallback: str = "report") -> str:
    """
    Build a safe filename from the report title:
    - Keep only letters, digits, dash and underscore (spaces become dashes).
    - Maximum 60 characters.
    - Fallback 'report' if result is empty.
    """
    if not title:
        return fallback
    # Replace all whitespace sequences (spaces, newlines, tabs) with dashes
    s = re.sub(r'\s+', '-', str(title).strip())
    # Keep only ASCII letters, digits, dash and underscore
    s = re.sub(r'[^A-Za-z0-9_-]', '', s)
    # Collapse multiple consecutive dashes
    s = re.sub(r'-+', '-', s)
    # Strip leading/trailing dashes and underscores
    s = s.strip('-_')
    # Maximum 60 characters
    s = s[:max_length].rstrip('-_')
    if not s:
        return fallback
    return s


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def generate_view(request):
    """
    Handle document generation.
    GET: Renders the frontend SPA generate view.
    POST: Validates session data, builds IEEE document, compiles PDF, and returns application/pdf.
    """
    if request.method == "GET":
        return index(request)

    # 1. Rate limit check
    rate_error = check_rate_limit(request.session)
    if rate_error:
        msg, status_code = rate_error
        return JsonResponse({"status": "error", "error": msg, "rate_limited": True}, status=status_code)

    # 2. Ensure session exists
    if not request.session.session_key:
        request.session.save()
    session_key = request.session.session_key

    # 3. Load all data from session
    session_data = {
        "title": request.session.get("title", ""),
        "authors": request.session.get("authors", []),
        "abstract": request.session.get("abstract", ""),
        "keywords": request.session.get("keywords", ""),
        "sections": request.session.get("sections", []),
        "references": request.session.get("references", []),
    }

    # 4. Validation
    errors, valid_images = validate_report_data(session_data, session_key)
    if errors:
        return JsonResponse({"status": "error", "errors": errors}, status=400)

    # 5. Record rate limit request
    record_rate_limit_request(request.session)

    # 6. Build report
    try:
        doc = build_report(session_data, images=valid_images, session_key=session_key)
    except (ValidationError, PermissionDenied) as e:
        return JsonResponse({"status": "error", "errors": [str(e)]}, status=400)
    except Exception as e:
        logger.exception("Unexpected error building report LaTeX document:")
        return JsonResponse({"status": "error", "errors": [f"Document build error: {e}"]}, status=500)

    # 7. Compile PDF via DynamicDocs
    try:
        pdf_bytes = compile_pdf(doc)
    except DynamicDocsCompileError as e:
        logger.error(f"DynamicDocs compilation error: {e}")
        return JsonResponse({
            "status": "compile_error",
            "error": str(e),
            "log_tail": e.log_tail,
        }, status=502)
    except DynamicDocsTimeoutError as e:
        logger.error(f"DynamicDocs timeout error: {e}")
        return JsonResponse({
            "status": "timeout",
            "error": str(e),
        }, status=504)
    except DynamicDocsConfigError as e:
        logger.error(f"DynamicDocs configuration error: {e}")
        return JsonResponse({
            "status": "config_error",
            "error": str(e),
        }, status=500)
    except DynamicDocsError as e:
        logger.error(f"DynamicDocs error: {e}")
        return JsonResponse({
            "status": "error",
            "error": str(e),
        }, status=502)
    except Exception as e:
        logger.exception("Unexpected error during PDF compilation:")
        return JsonResponse({"status": "error", "error": f"Internal compilation error: {e}"}, status=500)

    # 8. On successful PDF generation, extend expires_at by 7 days for used images
    for img in valid_images.values():
        try:
            img.extend_expiration(days=7)
        except Exception as e:
            logger.warning(f"Could not extend expiration for image #{img.pk}: {e}")

    # 9. Return application/pdf as attachment with sanitized filename
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    safe_name = sanitize_filename(session_data.get("title", ""))
    response["Content-Disposition"] = f'attachment; filename="{safe_name}.pdf"'
    return response


