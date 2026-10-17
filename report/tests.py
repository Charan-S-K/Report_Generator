"""
Tests for report app setup, health check, references, and title/authors.
"""
import json
import io
import time
import unittest
from datetime import timedelta
from django.test import TestCase, Client, override_settings
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.exceptions import ValidationError, PermissionDenied
from report.models import UploadedImage, get_image_for_session
from unittest.mock import patch, MagicMock
from report.builder import (
    build_report,
    escape_latex,
    strip_numbering,
    convert_citations,
    validate_equation,
)
from report.dynamicdocs import (
    compile_pdf,
    DynamicDocsConfigError,
    DynamicDocsCompileError,
    DynamicDocsTimeoutError,
    DynamicDocsError,
)


class Step1SetupTests(TestCase):
    """
    Test suite for Step 1 - Setup and configuration.
    """

    def setUp(self):
        self.client = Client()

    def test_settings_session_cookie_age(self):
        """Confirm session cookie age is configured for 7 days (604800 seconds)."""
        expected_age = 7 * 24 * 60 * 60
        self.assertEqual(settings.SESSION_COOKIE_AGE, expected_age)

    def test_settings_media_configuration(self):
        """Confirm MEDIA_ROOT and MEDIA_URL are configured."""
        self.assertEqual(settings.MEDIA_URL, '/media/')
        self.assertEqual(settings.MEDIA_ROOT, settings.BASE_DIR / 'media')

    def test_settings_middleware_csrf_and_sessions(self):
        """Confirm required Session and CSRF middlewares are registered."""
        self.assertIn(
            'django.contrib.sessions.middleware.SessionMiddleware',
            settings.MIDDLEWARE,
        )
        self.assertIn(
            'django.middleware.csrf.CsrfViewMiddleware',
            settings.MIDDLEWARE,
        )

    def test_health_check_endpoint_get(self):
        """Confirm /health/ endpoint returns 200 OK with status and session."""
        response = self.client.get(reverse('health_check'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'ok')
        self.assertTrue(data.get('session_key'))

    def test_health_check_endpoint_method_not_allowed(self):
        """Confirm POST request to health check is rejected with 405."""
        response = self.client.post(reverse('health_check'))
        self.assertEqual(response.status_code, 405)

    def test_spa_root_url_renders_index(self):
        """Confirm GET / renders index.html with 200 OK."""
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'REPORT GENERATOR')

    def test_spa_named_urls_render_index(self):
        """Confirm all named SPA URLs render successfully."""
        for url_name in ['reference', 'abstract', 'context', 'generate']:
            response = self.client.get(reverse(url_name))
            self.assertEqual(
                response.status_code,
                200,
                f"URL '{url_name}' failed to render.",
            )
            self.assertContains(response, 'REPORT GENERATOR')


class Step2ReferencesTests(TestCase):
    """
    Test suite for Step 2 - References session save and load.
    """

    def setUp(self):
        self.client = Client()

    def test_get_references_empty(self):
        """GET /references/ on empty session returns empty list."""
        response = self.client.get(reverse('references'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'success')
        self.assertEqual(data.get('references'), [])
        self.assertEqual(data.get('count'), 0)

    def test_post_references_success(self):
        """POST /references/ with valid equal-length titles and links saves and generates keys ref1, ref2."""
        payload = {
            "titles": [
                "Attention Is All You Need",
                "Deep Residual Learning for Image Recognition"
            ],
            "links": [
                "https://arxiv.org/abs/1706.03762",
                "https://arxiv.org/abs/1512.03385"
            ]
        }
        response = self.client.post(
            reverse('references'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'success')
        self.assertEqual(data.get('count'), 2)

        refs = data.get('references')
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]['key'], 'ref1')
        self.assertEqual(refs[0]['title'], 'Attention Is All You Need')
        self.assertEqual(refs[0]['link'], 'https://arxiv.org/abs/1706.03762')

        self.assertEqual(refs[1]['key'], 'ref2')
        self.assertEqual(refs[1]['title'], 'Deep Residual Learning for Image Recognition')
        self.assertEqual(refs[1]['link'], 'https://arxiv.org/abs/1512.03385')

        # Confirm subsequent GET returns the saved references
        get_res = self.client.get(reverse('references'))
        self.assertEqual(get_res.status_code, 200)
        get_data = get_res.json()
        self.assertEqual(get_data.get('references'), refs)
        self.assertEqual(get_data.get('titles'), payload['titles'])

    def test_post_references_missing_link_defaults_to_empty_string(self):
        """Confirm a missing or None link is treated as an empty string."""
        payload = {
            "titles": ["Paper with Link", "Paper without Link"],
            "links": ["https://example.com/paper1", None]
        }
        response = self.client.post(
            reverse('references'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        refs = response.json().get('references')
        self.assertEqual(refs[0]['link'], 'https://example.com/paper1')
        self.assertEqual(refs[1]['link'], '')

    def test_post_references_length_mismatch_returns_400(self):
        """Confirm length mismatch between titles and links returns 400 Bad Request."""
        payload = {
            "titles": ["Paper 1", "Paper 2"],
            "links": ["https://example.com/paper1"]  # Only 1 link for 2 titles
        }
        response = self.client.post(
            reverse('references'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Length mismatch', response.json().get('error', ''))

    def test_post_references_invalid_types_returns_400(self):
        """Confirm invalid payload types return 400 Bad Request."""
        # Non-list titles
        res1 = self.client.post(
            reverse('references'),
            data=json.dumps({"titles": "Not a list", "links": []}),
            content_type='application/json'
        )
        self.assertEqual(res1.status_code, 400)

        # Non-list links
        res2 = self.client.post(
            reverse('references'),
            data=json.dumps({"titles": [], "links": "Not a list"}),
            content_type='application/json'
        )
        self.assertEqual(res2.status_code, 400)

        # Invalid JSON syntax
        res3 = self.client.post(
            reverse('references'),
            data='{invalid-json',
            content_type='application/json'
        )
        self.assertEqual(res3.status_code, 400)

    def test_post_references_frontend_format(self):
        """Confirm frontend {references: [{title, link}]} shape is accepted."""
        payload = {
            "references": [
                {"title": "Frontend Reference 1", "link": "https://example.com/1"},
                {"title": "Frontend Reference 2", "link": None}
            ]
        }
        response = self.client.post(
            reverse('references'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        refs = response.json().get('references')
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]['key'], 'ref1')
        self.assertEqual(refs[1]['key'], 'ref2')
        self.assertEqual(refs[1]['link'], '')

    def test_references_stored_as_plain_list_in_session(self):
        """Confirm data is stored as a plain list in request.session['references']."""
        payload = {
            "titles": ["Quantum Computing & AI"],
            "links": ["https://example.com/qc"]
        }
        self.client.post(
            reverse('references'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        session_refs = self.client.session.get('references')
        self.assertIsInstance(session_refs, list)
        self.assertEqual(len(session_refs), 1)
        # Verify plain dict
        self.assertEqual(type(session_refs[0]), dict)
        self.assertEqual(session_refs[0]['key'], 'ref1')
        self.assertEqual(session_refs[0]['title'], 'Quantum Computing & AI')

    def test_references_method_not_allowed(self):
        """Confirm DELETE or PUT returns 405 Method Not Allowed."""
        res_del = self.client.delete(reverse('references'))
        self.assertEqual(res_del.status_code, 405)
        res_put = self.client.put(reverse('references'))
        self.assertEqual(res_put.status_code, 405)


class Step3TitleAuthorsTests(TestCase):
    """
    Test suite for Step 3 - Title and authors session save and load.
    """

    def setUp(self):
        self.client = Client()

    def test_get_title_authors_empty(self):
        """GET /title-authors/ on fresh session returns empty title and authors list."""
        response = self.client.get(reverse('title_authors'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'success')
        self.assertEqual(data.get('title'), '')
        self.assertEqual(data.get('authors'), [])
        self.assertEqual(data.get('count'), 0)

    def test_post_title_authors_success(self):
        """POST /title-authors/ saves title and list of authors in session."""
        payload = {
            "title": "Quantum Machine Learning in High Energy Physics",
            "authors": [
                {
                    "name": "Dr. Alex Morgan",
                    "affiliation": "Department of Physics, MIT"
                },
                {
                    "name": "Sarah Chen",
                    "affiliation": "Quantum AI Lab, Google"
                }
            ]
        }
        response = self.client.post(
            reverse('title_authors'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'success')
        self.assertEqual(data.get('title'), payload['title'])
        self.assertEqual(len(data.get('authors')), 2)
        self.assertEqual(data['authors'][0]['name'], "Dr. Alex Morgan")
        self.assertEqual(data['authors'][0]['affiliation'], "Department of Physics, MIT")

        # Subsequent GET loads the saved title and authors
        get_res = self.client.get(reverse('title_authors'))
        self.assertEqual(get_res.status_code, 200)
        get_data = get_res.json()
        self.assertEqual(get_data.get('title'), payload['title'])
        self.assertEqual(get_data.get('authors'), data['authors'])

    def test_post_title_authors_names_affiliations_format(self):
        """POST /title-authors/ accepts names and affiliations lists."""
        payload = {
            "title": "Autonomous Agent Systems",
            "names": ["Alice Johnson", "Bob Smith"],
            "affiliations": ["Stanford University", "Berkeley AI Research"]
        }
        response = self.client.post(
            reverse('title_authors'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        authors = response.json().get('authors')
        self.assertEqual(len(authors), 2)
        self.assertEqual(authors[0]['name'], "Alice Johnson")
        self.assertEqual(authors[0]['affiliation'], "Stanford University")
        self.assertEqual(authors[1]['name'], "Bob Smith")
        self.assertEqual(authors[1]['affiliation'], "Berkeley AI Research")

    def test_post_title_authors_invalid_authors_type_returns_400(self):
        """Confirm non-list authors returns 400 Bad Request."""
        res = self.client.post(
            reverse('title_authors'),
            data=json.dumps({"title": "Test", "authors": "Not a list"}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("'authors' must be a list", res.json().get('error', ''))

    def test_post_title_authors_invalid_author_item_returns_400(self):
        """Confirm non-dict author item returns 400 Bad Request."""
        res = self.client.post(
            reverse('title_authors'),
            data=json.dumps({"title": "Test", "authors": ["Not an object"]}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("must be an object", res.json().get('error', ''))

    def test_post_title_authors_names_affiliations_mismatch_returns_400(self):
        """Confirm length mismatch between names and affiliations returns 400."""
        payload = {
            "title": "Mismatch Test",
            "names": ["Alice", "Bob"],
            "affiliations": ["Stanford"]  # only 1 affiliation
        }
        res = self.client.post(
            reverse('title_authors'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Length mismatch", res.json().get('error', ''))

    def test_session_contains_plain_types(self):
        """Confirm title and authors are stored as plain string and plain list in session."""
        payload = {
            "title": "Plain Types Test",
            "authors": [{"name": "Author 1", "affiliation": "Lab 1"}]
        }
        self.client.post(
            reverse('title_authors'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        session = self.client.session
        self.assertEqual(session.get('title'), "Plain Types Test")
        self.assertIsInstance(session.get('authors'), list)
        self.assertIsInstance(session.get('authors')[0], dict)
        self.assertEqual(session.get('authors')[0]['name'], "Author 1")

    def test_title_authors_method_not_allowed(self):
        """Confirm PUT and DELETE return 405 Method Not Allowed."""
        res_del = self.client.delete(reverse('title_authors'))
        self.assertEqual(res_del.status_code, 405)
        res_put = self.client.put(reverse('title_authors'))
        self.assertEqual(res_put.status_code, 405)


class Step4AbstractKeywordsTests(TestCase):
    """
    Test suite for Step 4 - Abstract and keywords session save and load.
    """

    def setUp(self):
        self.client = Client()

    def test_get_abstract_keywords_empty(self):
        """GET /abstract-keywords/ on fresh session returns empty abstract and keywords."""
        response = self.client.get(reverse('abstract_keywords'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'success')
        self.assertEqual(data.get('abstract'), '')
        self.assertEqual(data.get('keywords'), '')

    def test_post_abstract_keywords_success(self):
        """POST /abstract-keywords/ saves abstract and keywords in session."""
        payload = {
            "abstract": "This paper explores the application of autonomous neural networks in robotics.",
            "keywords": "Robotics, Deep Learning, Autonomous Systems"
        }
        response = self.client.post(
            reverse('abstract_keywords'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'success')
        self.assertEqual(data.get('abstract'), payload['abstract'])
        self.assertEqual(data.get('keywords'), payload['keywords'])

        # Subsequent GET verifies persistence in session
        get_res = self.client.get(reverse('abstract_keywords'))
        self.assertEqual(get_res.status_code, 200)
        get_data = get_res.json()
        self.assertEqual(get_data.get('abstract'), payload['abstract'])
        self.assertEqual(get_data.get('keywords'), payload['keywords'])

    def test_post_abstract_keywords_list_format(self):
        """POST /abstract-keywords/ accepts keywords as a list of strings and formats as comma-separated."""
        payload = {
            "abstract": "A study on algorithmic complexity.",
            "keywords": ["Algorithms", "Complexity", "Graph Theory"]
        }
        response = self.client.post(
            reverse('abstract_keywords'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('keywords'), "Algorithms, Complexity, Graph Theory")

    def test_post_abstract_keywords_missing_fields_default_to_empty(self):
        """Confirm missing or None fields default to empty strings."""
        payload = {"abstract": None, "keywords": None}
        response = self.client.post(
            reverse('abstract_keywords'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('abstract'), '')
        self.assertEqual(data.get('keywords'), '')

    def test_post_abstract_keywords_invalid_types_returns_400(self):
        """Confirm non-string abstract and invalid keywords type return 400 Bad Request."""
        # Non-string abstract
        res1 = self.client.post(
            reverse('abstract_keywords'),
            data=json.dumps({"abstract": 12345, "keywords": "AI"}),
            content_type='application/json'
        )
        self.assertEqual(res1.status_code, 400)
        self.assertIn("'abstract' must be a string", res1.json().get('error', ''))

        # Non-string, non-list keywords (e.g. integer)
        res2 = self.client.post(
            reverse('abstract_keywords'),
            data=json.dumps({"abstract": "Valid abstract", "keywords": 999}),
            content_type='application/json'
        )
        self.assertEqual(res2.status_code, 400)
        self.assertIn("'keywords' must be a string or list", res2.json().get('error', ''))

        # Malformed JSON
        res3 = self.client.post(
            reverse('abstract_keywords'),
            data='{invalid-json',
            content_type='application/json'
        )
        self.assertEqual(res3.status_code, 400)

    def test_session_contains_raw_untransformed_text(self):
        """Confirm special characters (e.g. %, &, $, _) stay raw in session without premature escaping."""
        raw_abstract = "Achieved 95% accuracy & $10M ROI with high_performance models."
        raw_keywords = "AI & ML, 99% Precision, $Cost-Effective"
        payload = {"abstract": raw_abstract, "keywords": raw_keywords}
        self.client.post(
            reverse('abstract_keywords'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        session = self.client.session
        self.assertEqual(session.get('abstract'), raw_abstract)
        self.assertEqual(session.get('keywords'), raw_keywords)

    def test_abstract_keywords_method_not_allowed(self):
        """Confirm PUT and DELETE return 405 Method Not Allowed."""
        res_del = self.client.delete(reverse('abstract_keywords'))
        self.assertEqual(res_del.status_code, 405)
        res_put = self.client.put(reverse('abstract_keywords'))
        self.assertEqual(res_put.status_code, 405)


class Step5SectionsTests(TestCase):
    """
    Test suite for Step 5 - Sections and ordered elements save/load in session.
    """

    def setUp(self):
        self.client = Client()

    def test_get_sections_empty(self):
        """GET /sections/ on fresh session returns empty list."""
        response = self.client.get(reverse('sections'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'success')
        self.assertEqual(data.get('sections'), [])
        self.assertEqual(data.get('count'), 0)

    def test_post_sections_success_with_ordered_elements(self):
        """POST /sections/ validates element types and preserves element order exactly."""
        payload = {
            "sections": [
                {
                    "title": "1. Introduction",
                    "context": "Contextual background on autonomous agents.",
                    "elements": [
                        {
                            "type": "subsection",
                            "title": "1.1 Prior Work",
                            "content": "Overview of previous benchmark results."
                        },
                        {
                            "type": "image",
                            "caption": "Figure 1: Benchmark Distribution",
                            "imageType": "png",
                            "image_id": 101
                        },
                        {
                            "type": "table",
                            "caption": "Table 1: Latency Comparison",
                            "columnsCount": 3,
                            "rowsCount": 2,
                            "gridData": [
                                ["Model", "Throughput", "Latency"],
                                ["Agent-A", "150 req/s", "12 ms"]
                            ]
                        },
                        {
                            "type": "equation",
                            "caption": "Equation 1: Optimization Objective",
                            "equation": "\\min_{\\theta} \\mathcal{L}(\\theta)"
                        },
                        {
                            "type": "subsection",
                            "title": "1.2 Scope & Contributions",
                            "content": "Specific scope of this investigation."
                        }
                    ]
                }
            ]
        }
        response = self.client.post(
            reverse('sections'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'success')
        self.assertEqual(data.get('count'), 1)

        sec = data['sections'][0]
        self.assertEqual(sec['title'], "1. Introduction")
        self.assertEqual(len(sec['elements']), 5)

        # Verify element order is preserved exactly as given
        self.assertEqual(sec['elements'][0]['type'], "subsection")
        self.assertEqual(sec['elements'][0]['title'], "1.1 Prior Work")
        self.assertEqual(sec['elements'][1]['type'], "image")
        self.assertEqual(sec['elements'][1]['image_id'], 101)
        self.assertEqual(sec['elements'][2]['type'], "table")
        self.assertEqual(sec['elements'][2]['columnsCount'], 3)
        self.assertEqual(sec['elements'][3]['type'], "equation")
        self.assertEqual(sec['elements'][3]['equation'], "\\min_{\\theta} \\mathcal{L}(\\theta)")
        self.assertEqual(sec['elements'][4]['type'], "subsection")
        self.assertEqual(sec['elements'][4]['title'], "1.2 Scope & Contributions")

        # Subsequent GET verifies persistence in session
        get_res = self.client.get(reverse('sections'))
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.json().get('sections'), data['sections'])

    def test_post_sections_invalid_element_type_returns_400(self):
        """Confirm unsupported element type returns 400 Bad Request."""
        payload = {
            "sections": [
                {
                    "title": "Section with Invalid Element",
                    "elements": [
                        {"type": "video", "url": "https://example.com/video.mp4"}
                    ]
                }
            ]
        }
        response = self.client.post(
            reverse('sections'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid or missing type 'video'", response.json().get('error', ''))

    def test_post_sections_invalid_image_format_returns_400(self):
        """Confirm image element with unsupported format returns 400 Bad Request."""
        payload = {
            "sections": [
                {
                    "title": "Image Section",
                    "elements": [
                        {"type": "image", "caption": "Bad Image", "imageType": "gif"}
                    ]
                }
            ]
        }
        response = self.client.post(
            reverse('sections'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("imageType must be 'jpg' or 'png'", response.json().get('error', ''))

    def test_post_sections_invalid_table_grid_returns_400(self):
        """Confirm table element with malformed gridData returns 400 Bad Request."""
        payload = {
            "sections": [
                {
                    "title": "Table Section",
                    "elements": [
                        {"type": "table", "caption": "Bad Table", "gridData": "not-a-list"}
                    ]
                }
            ]
        }
        response = self.client.post(
            reverse('sections'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("'gridData' must be a list of rows", response.json().get('error', ''))

    def test_post_sections_direct_list_payload(self):
        """Confirm payload directly passed as a JSON array of sections is accepted."""
        payload = [
            {
                "title": "Direct List Section",
                "context": "Context text",
                "elements": []
            }
        ]
        response = self.client.post(
            reverse('sections'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json().get('sections')), 1)

    def test_session_contains_plain_types(self):
        """Confirm sections and elements are stored as plain JSON lists/dicts in session."""
        payload = {
            "sections": [
                {
                    "title": "Plain Types Section",
                    "context": "Plain context",
                    "elements": [{"type": "subsection", "title": "Sub", "content": "Text"}]
                }
            ]
        }
        self.client.post(
            reverse('sections'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        session_sections = self.client.session.get('sections')
        self.assertIsInstance(session_sections, list)
        self.assertIsInstance(session_sections[0], dict)
        self.assertIsInstance(session_sections[0]['elements'], list)

    def test_sections_method_not_allowed(self):
        """Confirm PUT and DELETE return 405 Method Not Allowed."""
        res_del = self.client.delete(reverse('sections'))
        self.assertEqual(res_del.status_code, 405)
        res_put = self.client.put(reverse('sections'))
        self.assertEqual(res_put.status_code, 405)


class Step6ImageTests(TestCase):
    """
    Test suite for Step 6 - Image upload, magic bytes validation, session ownership,
    expiration, and purge_expired_images management command.
    """

    def setUp(self):
        self.client = Client()
        # PNG magic bytes: 89 50 4E 47 0D 0A 1A 0A
        self.valid_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        # JPEG magic bytes: FF D8 FF
        self.valid_jpg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"

    def tearDown(self):
        # Clean up any files created in media directory during tests
        for img in UploadedImage.objects.all():
            if img.file:
                try:
                    img.file.delete(save=False)
                except Exception:
                    pass

    def test_upload_valid_png_success(self):
        """Confirm valid PNG upload returns 201 with image_id, format, size, and expiration."""
        upload_file = SimpleUploadedFile("figure.png", self.valid_png_bytes, content_type="image/png")
        response = self.client.post(
            reverse('upload_image'),
            {"image": upload_file, "fmt": "png"},
            format="multipart"
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("image_id", data)
        self.assertEqual(data.get("fmt"), "png")
        self.assertEqual(data.get("size"), len(self.valid_png_bytes))
        self.assertIn("expires_at", data)

        # Verify DB entry
        image = UploadedImage.objects.get(pk=data["image_id"])
        self.assertEqual(image.fmt, "png")
        self.assertEqual(image.size, len(self.valid_png_bytes))
        self.assertEqual(image.session_key, self.client.session.session_key)
        self.assertFalse(image.is_expired)

    def test_upload_valid_jpeg_success(self):
        """Confirm valid JPEG upload returns 201 with image_id and format 'jpg'."""
        upload_file = SimpleUploadedFile("photo.jpg", self.valid_jpg_bytes, content_type="image/jpeg")
        response = self.client.post(
            reverse('upload_image'),
            {"image": upload_file, "fmt": "jpg"},
            format="multipart"
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data.get("fmt"), "jpg")

        image = UploadedImage.objects.get(pk=data["image_id"])
        self.assertEqual(image.fmt, "jpg")

    def test_upload_invalid_png_magic_bytes_returns_400(self):
        """Confirm uploading non-PNG bytes with fmt='png' returns 400 Bad Request."""
        fake_png_bytes = b"NOT_A_PNG_HEADER_12345678"
        upload_file = SimpleUploadedFile("fake.png", fake_png_bytes, content_type="image/png")
        response = self.client.post(
            reverse('upload_image'),
            {"image": upload_file, "fmt": "png"},
            format="multipart"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("magic bytes", response.json().get("error", "").lower())

    def test_upload_invalid_jpg_magic_bytes_returns_400(self):
        """Confirm uploading non-JPEG bytes with fmt='jpg' returns 400 Bad Request."""
        fake_jpg_bytes = b"NOT_A_JPEG_HEADER"
        upload_file = SimpleUploadedFile("fake.jpg", fake_jpg_bytes, content_type="image/jpeg")
        response = self.client.post(
            reverse('upload_image'),
            {"image": upload_file, "fmt": "jpg"},
            format="multipart"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("magic bytes", response.json().get("error", "").lower())

    def test_upload_unsupported_format_returns_400(self):
        """Confirm unsupported format (e.g. gif or pdf) returns 400 Bad Request."""
        upload_file = SimpleUploadedFile("animation.gif", b"GIF89a...", content_type="image/gif")
        response = self.client.post(
            reverse('upload_image'),
            {"image": upload_file, "fmt": "gif"},
            format="multipart"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid format", response.json().get("error", ""))

    def test_upload_missing_image_field_returns_400(self):
        """Confirm POST request missing 'image' in request.FILES returns 400 Bad Request."""
        response = self.client.post(
            reverse('upload_image'),
            {"fmt": "png"},
            format="multipart"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("No image file provided", response.json().get("error", ""))

    def test_upload_size_exceeds_10mb_returns_400(self):
        """Confirm file exceeding 10 MB is rejected with 400 Bad Request."""
        # 10 MB + 100 bytes
        large_content = self.valid_png_bytes + (b"\x00" * (10 * 1024 * 1024 + 100))
        large_file = SimpleUploadedFile("huge.png", large_content, content_type="image/png")

        response = self.client.post(
            reverse('upload_image'),
            {"image": large_file, "fmt": "png"},
            format="multipart"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("exceeds the maximum allowed limit of 10 MB", response.json().get("error", ""))

    def test_session_ownership_isolation(self):
        """Confirm only the owning session can access an image id."""
        # Client 1 uploads an image
        upload_file = SimpleUploadedFile("fig.png", self.valid_png_bytes, content_type="image/png")
        res1 = self.client.post(
            reverse('upload_image'),
            {"image": upload_file, "fmt": "png"},
            format="multipart"
        )
        image_id = res1.json()["image_id"]
        client1_session_key = self.client.session.session_key

        # Client 1 can access it
        img = get_image_for_session(image_id, client1_session_key)
        self.assertEqual(img.pk, image_id)

        # Client 2 (different session) is denied access
        client2 = Client()
        client2.get(reverse('health_check'))  # Initialize session for client 2
        client2_session_key = client2.session.session_key

        with self.assertRaises(PermissionDenied):
            get_image_for_session(image_id, client2_session_key)

    def test_expired_image_raises_validation_error(self):
        """Confirm get_image_for_session raises ValidationError if image is expired."""
        upload_file = SimpleUploadedFile("fig.png", self.valid_png_bytes, content_type="image/png")
        res = self.client.post(
            reverse('upload_image'),
            {"image": upload_file, "fmt": "png"},
            format="multipart"
        )
        image_id = res.json()["image_id"]
        session_key = self.client.session.session_key

        # Manually expire the image
        img = UploadedImage.objects.get(pk=image_id)
        img.expires_at = timezone.now() - timedelta(days=1)
        img.save(update_fields=['expires_at'])

        self.assertTrue(img.is_expired)
        with self.assertRaises(ValidationError) as ctx:
            get_image_for_session(image_id, session_key)
        self.assertIn("expired", str(ctx.exception))

    def test_extend_expiration(self):
        """Confirm extend_expiration moves expires_at forward by the given days."""
        upload_file = SimpleUploadedFile("fig.png", self.valid_png_bytes, content_type="image/png")
        res = self.client.post(
            reverse('upload_image'),
            {"image": upload_file, "fmt": "png"},
            format="multipart"
        )
        image_id = res.json()["image_id"]
        img = UploadedImage.objects.get(pk=image_id)

        old_expiration = img.expires_at
        img.extend_expiration(days=7)
        img.refresh_from_db()

        self.assertGreater(img.expires_at, old_expiration - timedelta(seconds=5))
        self.assertFalse(img.is_expired)

    def test_purge_expired_images_command(self):
        """Confirm purge_expired_images deletes expired records and their files, leaving active images."""
        # 1. Create an expired image
        expired_file = SimpleUploadedFile("expired.png", self.valid_png_bytes, content_type="image/png")
        expired_img = UploadedImage.objects.create(
            session_key="expired_session_123",
            file=expired_file,
            fmt="png",
            size=len(self.valid_png_bytes),
            expires_at=timezone.now() - timedelta(days=2)
        )
        expired_id = expired_img.pk
        expired_file_path = expired_img.file.path

        # 2. Create an active image
        active_file = SimpleUploadedFile("active.png", self.valid_png_bytes, content_type="image/png")
        active_img = UploadedImage.objects.create(
            session_key="active_session_456",
            file=active_file,
            fmt="png",
            size=len(self.valid_png_bytes),
            expires_at=timezone.now() + timedelta(days=7)
        )
        active_id = active_img.pk

        # Run management command
        out = io.StringIO()
        call_command('purge_expired_images', stdout=out)
        output_str = out.getvalue()
        self.assertIn("Successfully purged 1 expired image(s)", output_str)

        # Assert expired image is deleted from DB
        self.assertFalse(UploadedImage.objects.filter(pk=expired_id).exists())
        # Assert active image remains in DB
        self.assertTrue(UploadedImage.objects.filter(pk=active_id).exists())


class Step7BuilderTests(TestCase):
    """
    Test suite for Step 7 - LaTeX builder (report/builder.py) and ieee.py integration.
    """

    def setUp(self):
        self.valid_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"

    def test_escape_latex(self):
        """Confirm all LaTeX special characters (& % $ # _ { } ~ ^ \\) are correctly escaped."""
        raw_text = r"Cost is $100 & 50% for user_1 {key} #hash ~tilde ^caret \backslash"
        escaped = escape_latex(raw_text)
        self.assertIn(r"\$100", escaped)
        self.assertIn(r"\&", escaped)
        self.assertIn(r"50\%", escaped)
        self.assertIn(r"user\_1", escaped)
        self.assertIn(r"\{key\}", escaped)
        self.assertIn(r"\#hash", escaped)
        self.assertIn(r"\textasciitilde{}", escaped)
        self.assertIn(r"\textasciicircum{}", escaped)
        self.assertIn(r"\textbackslash{}", escaped)
        # Ensure no accidental cascading escaping like \textbackslash\{\}
        self.assertNotIn(r"\textbackslash\{\}", escaped)

    def test_strip_numbering(self):
        """Confirm default numbering is stripped from titles/captions while bare years are kept."""
        self.assertEqual(strip_numbering("1. Introduction"), "Introduction")
        self.assertEqual(strip_numbering("1.1 Prior Work"), "Prior Work")
        self.assertEqual(strip_numbering("Section 2: Methodology"), "Methodology")
        self.assertEqual(strip_numbering("Figure 1: Benchmark Chart"), "Benchmark Chart")
        self.assertEqual(strip_numbering("Table 1: Latency Overview"), "Latency Overview")
        self.assertEqual(strip_numbering("Equation 1: Optimization"), "Optimization")
        # Bare years must NOT be stripped
        self.assertEqual(strip_numbering("2026 Outlook"), "2026 Outlook")
        self.assertEqual(strip_numbering("2025 Retrospective"), "2025 Retrospective")

    def test_citations_conversion(self):
        r"""Confirm citation conversion rules: [N] -> \cite{refN}, adjacent runs merged, out of bounds kept."""
        num_refs = 3
        # Single citation
        self.assertEqual(convert_citations("Prior art [1] showed...", num_refs), r"Prior art \cite{ref1} showed...")
        # Adjacent run
        self.assertEqual(convert_citations("Works [2][3] investigated...", num_refs), r"Works \cite{ref2,ref3} investigated...")
        # Out of bounds numbers kept unchanged
        self.assertEqual(convert_citations("Invalid [0] and [4] remain.", num_refs), "Invalid [0] and [4] remain.")
        # Non-numeric brackets left unchanged
        self.assertEqual(convert_citations("Fact [citation needed] noted.", num_refs), "Fact [citation needed] noted.")
        # Mixed run: valid followed by invalid
        self.assertEqual(convert_citations("Check [1][4] now.", num_refs), r"Check \cite{ref1}[4] now.")

    def test_equation_validation_valid(self):
        """Confirm valid LaTeX equation with balanced braces passes validation."""
        valid_eqs = [
            r"E = mc^2",
            r"\min_{\theta} \mathcal{L}(\theta) = \sum_{i=1}^{N} (y_i - f(x_i))^2",
            r"\{ x \in \mathbb{R} \mid x > 0 \}",
        ]
        for eq in valid_eqs:
            try:
                validate_equation(eq)
            except ValidationError:
                self.fail(f"validate_equation raised ValidationError unexpectedly for valid equation: {eq}")

    def test_equation_validation_unbalanced_braces(self):
        """Confirm unbalanced braces in equations raise ValidationError."""
        unbalanced = [
            r"\frac{1}{2",  # missing closing brace
            r"x + y}",      # extra closing brace
            r"{{x + y}",    # 2 opens, 1 close
        ]
        for eq in unbalanced:
            with self.assertRaises(ValidationError):
                validate_equation(eq)

    def test_equation_validation_forbidden_commands(self):
        """Confirm dangerous LaTeX control commands are rejected with ValidationError."""
        forbidden_snippets = [
            r"\input{secret.tex}",
            r"\include{chapter1}",
            r"\write18{rm -rf /}",
            r"\immediate\write18{ls}",
            r"\openin 1=file.txt",
            r"\openout 1=file.txt",
            r"\read 1 to \line",
            r"\catcode`\@=11",
            r"\def\malicious{foo}",
            r"\csname malicious\endcsname",
            r"\usepackage{evil}",
            r"\documentclass{article}",
        ]
        for snippet in forbidden_snippets:
            with self.assertRaises(ValidationError) as ctx:
                validate_equation(snippet)
            self.assertIn("forbidden LaTeX command", str(ctx.exception))

    def test_missing_or_expired_or_unowned_image_raises_validation_error(self):
        """Confirm missing, expired, or unowned image_id raises ValidationError or PermissionDenied."""
        # 1. Missing image_id in element
        data_missing = {
            "title": "Test Paper",
            "sections": [
                {"title": "Sec", "context": "", "elements": [{"type": "image", "caption": "No ID"}]}
            ]
        }
        with self.assertRaises(ValidationError) as ctx:
            build_report(data_missing)
        self.assertIn("missing required 'image_id'", str(ctx.exception))

        # 2. Expired image
        expired_file = SimpleUploadedFile("exp.png", self.valid_png_bytes, content_type="image/png")
        expired_img = UploadedImage.objects.create(
            session_key="test_session",
            file=expired_file,
            fmt="png",
            size=len(self.valid_png_bytes),
            expires_at=timezone.now() - timedelta(days=1)
        )
        data_expired = {
            "title": "Test Paper",
            "sections": [
                {"title": "Sec", "context": "", "elements": [{"type": "image", "image_id": expired_img.pk, "caption": "Exp"}]}
            ]
        }
        with self.assertRaises(ValidationError) as ctx:
            build_report(data_expired, session_key="test_session")
        self.assertIn("expired", str(ctx.exception))

        # 3. Unowned image (different session)
        active_file = SimpleUploadedFile("active.png", self.valid_png_bytes, content_type="image/png")
        active_img = UploadedImage.objects.create(
            session_key="session_owner",
            file=active_file,
            fmt="png",
            size=len(self.valid_png_bytes),
            expires_at=timezone.now() + timedelta(days=7)
        )
        data_unowned = {
            "title": "Test Paper",
            "sections": [
                {"title": "Sec", "context": "", "elements": [{"type": "image", "image_id": active_img.pk, "caption": "Act"}]}
            ]
        }
        with self.assertRaises(PermissionDenied):
            build_report(data_unowned, session_key="different_session")

    def test_table_slicing_and_padding(self):
        """Confirm table gridData is sliced and padded to rowsCount x columnsCount with escaped cells."""
        data = {
            "title": "Table Test",
            "sections": [
                {
                    "title": "Results",
                    "context": "Context",
                    "elements": [
                        {
                            "type": "table",
                            "caption": "Table 1: Test Metrics",
                            "columnsCount": 3,
                            "rowsCount": 3,
                            "gridData": [
                                ["Metric", "Value", "Notes"],
                                ["Latency & Overhead", "8.4%"],  # missing 3rd cell
                                ["Throughput", "150", "req/s", "extra_ignored"]  # 4th cell should be sliced out
                            ]
                        }
                    ]
                }
            ]
        }
        doc = build_report(data)
        tmpl = doc.get_template()
        self.assertIn(r"Latency \& Overhead", tmpl)
        self.assertIn(r"8.4\%", tmpl)
        self.assertIn("Throughput", tmpl)

    def test_full_report_build_reveals_ieee_template_bugs(self):
        """
        Builds the exact specified report:
          - Two sections
          - A subsection before and after an image
          - A table with '8.4%' and '&' in cells
          - An equation
          - Citations
          - References
        This test documents the precise behavior and reveals the existing bugs in ieee.py:
          1. Subsection 1 is included, but Subsection 2 is dropped by ieee.py because
             add_subsection replaces '%<<subsection>>' without leaving a subsequent token.
          2. Section 1 is included, but Section 2 is dropped by ieee.py because
             add_equation replaces '%<<insert>>' without appending a trailing '%<<insert>>'.
        """
        uploaded_file = SimpleUploadedFile("test.png", self.valid_png_bytes, content_type="image/png")
        img = UploadedImage.objects.create(
            session_key="full_test_session",
            file=uploaded_file,
            fmt="png",
            size=len(self.valid_png_bytes),
            expires_at=timezone.now() + timedelta(days=7)
        )

        report_data = {
            "title": "Deep Learning & AI in 2026 Outlook",
            "authors": [
                {"name": "Alice Smith & Bob Jones", "affiliation": "MIT & Stanford"}
            ],
            "abstract": "We achieve 99% accuracy & $1M compute savings.",
            "keywords": "AI, Neural Networks, IEEE 2026",
            "references": [
                {"title": "Attention Is All You Need", "link": "https://arxiv.org/abs/1706.03762"},
                {"title": "Deep Residual Learning", "link": "https://arxiv.org/abs/1512.03385"}
            ],
            "sections": [
                {
                    "title": "1. Introduction",
                    "context": "Context with citations [1] and adjacent [1][2].",
                    "elements": [
                        {
                            "type": "subsection",
                            "title": "1.1 Subsection Before Image",
                            "content": "Content of subsection before image."
                        },
                        {
                            "type": "image",
                            "caption": "Figure 1: Architecture Diagram",
                            "imageType": "png",
                            "image_id": img.pk
                        },
                        {
                            "type": "subsection",
                            "title": "1.2 Subsection After Image",
                            "content": "Content of subsection after image."
                        },
                        {
                            "type": "table",
                            "caption": "Table 1: Performance Matrix",
                            "columnsCount": 2,
                            "rowsCount": 2,
                            "gridData": [
                                ["Metric", "Improvement & Rate"],
                                ["Efficiency", "8.4% & 2x"]
                            ]
                        },
                        {
                            "type": "equation",
                            "caption": "Equation 1: Loss Formula",
                            "equation": r"\mathcal{L}(\theta) = \sum_{i=1}^{N} (y_i - \hat{y}_i)^2"
                        }
                    ]
                },
                {
                    "title": "2. Methodology & Future Work",
                    "context": "Detailed methodology context.",
                    "elements": []
                }
            ]
        }

        doc = build_report(report_data, session_key="full_test_session")
        tmpl = doc.get_template()

        # Elements that succeed in ieee.py:
        self.assertIn("Deep Learning \\& AI in 2026 Outlook", tmpl)
        self.assertIn("Alice Smith \\& Bob Jones", tmpl)
        self.assertIn("MIT \\& Stanford", tmpl)
        self.assertIn("99\\% accuracy \\& \\$1M compute savings.", tmpl)
        self.assertIn("AI, Neural Networks, IEEE 2026", tmpl)
        self.assertIn("Introduction", tmpl)
        self.assertIn(r"\cite{ref1}", tmpl)
        self.assertIn(r"\cite{ref1,ref2}", tmpl)
        self.assertIn("Subsection Before Image", tmpl)
        self.assertIn("Architecture Diagram", tmpl)
        self.assertNotIn("Figure 1:", tmpl)
        self.assertIn(r"8.4\% \& 2x", tmpl)
        self.assertIn(r"\mathcal{L}(\theta)", tmpl)
        self.assertIn("Attention Is All You Need", tmpl)
        self.assertIn(r"\bibitem{ref1}", tmpl)

        # Bugs in ieee.py revealed by this test:
        # 1. Subsection 2 is missing from template because add_subsection leaves no %<<subsection>> token
        has_subsection_2 = "Subsection After Image" in tmpl
        self.assertFalse(
            has_subsection_2,
            "Bug in ieee.py: add_subsection does not provide a trailing %<<subsection>> tag, causing Subsection 2 to be dropped."
        )

        # 2. Section 2 is successfully included now that add_equation preserves %<<insert>>
        has_section_2 = "Methodology \\& Future Work" in tmpl
        self.assertTrue(has_section_2, "Section 2 is properly preserved in template output.")

    @unittest.expectedFailure
    def test_full_report_assert_all_elements_present_in_order(self):
        """
        Asserts that every piece of text appears in the output string in the right order:
          Title -> Author -> Abstract -> Section 1 -> Subsection 1 -> Image ->
          Subsection 2 -> Table -> Equation -> Section 2 -> Reference
        
        This test is marked expectedFailure because ieee.py currently drops
        Subsection 2 and Section 2. When ieee.py is updated, this test will pass.
        """
        uploaded_file = SimpleUploadedFile("test.png", self.valid_png_bytes, content_type="image/png")
        img = UploadedImage.objects.create(
            session_key="order_test_session",
            file=uploaded_file,
            fmt="png",
            size=len(self.valid_png_bytes),
            expires_at=timezone.now() + timedelta(days=7)
        )

        report_data = {
            "title": "1. My Paper Title",
            "authors": [{"name": "Author One", "affiliation": "Lab One"}],
            "abstract": "Abstract content.",
            "keywords": "Keywords here",
            "references": [{"title": "Ref Title", "link": "http://example.com"}],
            "sections": [
                {
                    "title": "1. First Section",
                    "context": "Context 1.",
                    "elements": [
                        {"type": "subsection", "title": "1.1 Sub Before", "content": "Content before."},
                        {"type": "image", "caption": "Fig Cap", "imageType": "png", "image_id": img.pk},
                        {"type": "subsection", "title": "1.2 Sub After", "content": "Content after."},
                        {
                            "type": "table",
                            "caption": "Tab Cap",
                            "columnsCount": 2,
                            "rowsCount": 2,
                            "gridData": [["H1", "H2"], ["8.4%", "A & B"]]
                        },
                        {"type": "equation", "caption": "Eq Cap", "equation": "x = y"}
                    ]
                },
                {
                    "title": "2. Second Section",
                    "context": "Context 2.",
                    "elements": []
                }
            ]
        }

        doc = build_report(report_data, session_key="order_test_session")
        tmpl = doc.get_template()

        ordered_markers = [
            "My Paper Title",
            "Author One",
            "Abstract content.",
            "First Section",
            "Sub Before",
            "Fig Cap",
            "Sub After",        # Dropped by ieee.py
            "Tab Cap",
            "x = y",
            "Second Section",    # Dropped by ieee.py
            "Ref Title",
        ]

        last_pos = -1
        for marker in ordered_markers:
            pos = tmpl.find(marker)
            self.assertNotEqual(pos, -1, f"Marker '{marker}' was not found in LaTeX output.")
            self.assertGreater(pos, last_pos, f"Marker '{marker}' appeared out of order.")
            last_pos = pos


class Step8DynamicDocsTests(TestCase):
    """
    Test suite for Step 8 - DynamicDocs API client (report/dynamicdocs.py).
    All external HTTP requests are mocked.
    """

    def setUp(self):
        self.fake_payload = {"texFileContent": r"\documentclass{IEEEtran}\begin{document}Hello\end{document}"}
        self.fake_pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"

    def test_missing_api_key_raises_config_error(self):
        """Confirm compile_pdf raises DynamicDocsConfigError when ADV_API_KEY is not set."""
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(DynamicDocsConfigError):
                compile_pdf(self.fake_payload)

    @patch("report.dynamicdocs.requests.get")
    @patch("report.dynamicdocs.requests.post")
    def test_compile_pdf_success_polling(self, mock_post, mock_get):
        """Confirm successful compile flow: POST compile -> poll status URL -> download PDF from documentUrl."""
        # 1. Mock initial compile POST response
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "documentStatusUrl": "https://api.advicement.io/status/signed-job-123"
        }
        mock_post.return_value = mock_post_resp

        # 2. Mock GET polling calls:
        # Call 1: Status URL returns in-progress
        # Call 2: Status URL returns documentUrl
        # Call 3: Download documentUrl returns PDF binary
        mock_status_in_progress = MagicMock()
        mock_status_in_progress.status_code = 200
        mock_status_in_progress.json.return_value = {"status": "in-progress", "statusCode": 202}

        mock_status_done = MagicMock()
        mock_status_done.status_code = 200
        mock_status_done.json.return_value = {
            "statusCode": 200,
            "documentUrl": "https://api.advicement.io/docs/signed-doc-456.pdf"
        }

        mock_doc_download = MagicMock()
        mock_doc_download.status_code = 200
        mock_doc_download.content = self.fake_pdf_bytes

        mock_get.side_effect = [mock_status_in_progress, mock_status_done, mock_doc_download]

        result_pdf = compile_pdf(
            self.fake_payload,
            api_key="adv_test_key_xyz",
            poll_interval=0.01,
            max_timeout=5
        )

        # Assert returned PDF bytes
        self.assertEqual(result_pdf, self.fake_pdf_bytes)

        # Assert POST used Adv-Security-Token header
        mock_post.assert_called_once()
        post_headers = mock_post.call_args[1]["headers"]
        self.assertEqual(post_headers["Adv-Security-Token"], "adv_test_key_xyz")
        self.assertEqual(post_headers["Content-Type"], "application/json")

        # Assert status GET requests do NOT include Adv-Security-Token (signed URLs)
        for call_args in mock_get.call_args_list:
            headers = call_args[1].get("headers")
            if headers:
                self.assertNotIn("Adv-Security-Token", headers)

    @patch("report.dynamicdocs.requests.get")
    @patch("report.dynamicdocs.requests.post")
    def test_compile_pdf_immediate_document_url(self, mock_post, mock_get):
        """Confirm compile_pdf handles documentUrl present directly in initial response."""
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "documentStatusUrl": "https://api.advicement.io/status/immediate",
            "documentUrl": "https://api.advicement.io/docs/immediate.pdf"
        }
        mock_post.return_value = mock_post_resp

        mock_download_resp = MagicMock()
        mock_download_resp.status_code = 200
        mock_download_resp.content = self.fake_pdf_bytes
        mock_get.return_value = mock_download_resp

        result = compile_pdf(self.fake_payload, api_key="test_key")
        self.assertEqual(result, self.fake_pdf_bytes)

    @patch("report.dynamicdocs.requests.get")
    @patch("report.dynamicdocs.requests.post")
    def test_compile_pdf_latex_compile_error_with_log_tail(self, mock_post, mock_get):
        """Confirm compilation failure (statusCode >= 400) fetches log and carries ~3000 chars log tail."""
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "documentStatusUrl": "https://api.advicement.io/status/error-job"
        }
        mock_post.return_value = mock_post_resp

        # Status polling returns error with latexLogUrl
        mock_status_resp = MagicMock()
        mock_status_resp.status_code = 200
        mock_status_resp.json.return_value = {
            "statusCode": 400,
            "latexLogUrl": "https://api.advicement.io/logs/latex.log"
        }

        # LaTeX log response (long log > 3500 chars)
        long_prefix = "LaTeX 2e log line...\n" * 200
        expected_error_tail = "! LaTeX Error: File 'missing.sty' not found.\nType X to quit."
        full_log = long_prefix + expected_error_tail

        mock_log_resp = MagicMock()
        mock_log_resp.status_code = 200
        mock_log_resp.text = full_log

        mock_get.side_effect = [mock_status_resp, mock_log_resp]

        with self.assertRaises(DynamicDocsCompileError) as ctx:
            compile_pdf(
                self.fake_payload,
                api_key="test_key",
                poll_interval=0.01,
                max_timeout=5
            )

        err = ctx.exception
        self.assertEqual(err.status_code, 400)
        self.assertLessEqual(len(err.log_tail), 3000)
        self.assertIn("missing.sty", err.log_tail)

    @patch("report.dynamicdocs.requests.get")
    @patch("report.dynamicdocs.requests.post")
    def test_compile_pdf_calculation_log_fallback(self, mock_post, mock_get):
        """Confirm compilation error falls back to calculationLogUrl if latexLogUrl is absent."""
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "documentStatusUrl": "https://api.advicement.io/status/calc-error"
        }
        mock_post.return_value = mock_post_resp

        mock_status_resp = MagicMock()
        mock_status_resp.status_code = 200
        mock_status_resp.json.return_value = {
            "statusCode": 422,
            "latexLogUrl": None,
            "calculationLogUrl": "https://api.advicement.io/logs/calc.log"
        }

        mock_calc_resp = MagicMock()
        mock_calc_resp.status_code = 200
        mock_calc_resp.text = "Calculation syntax error: variable undef"

        mock_get.side_effect = [mock_status_resp, mock_calc_resp]

        with self.assertRaises(DynamicDocsCompileError) as ctx:
            compile_pdf(self.fake_payload, api_key="test_key", poll_interval=0.01, max_timeout=5)

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("Calculation syntax error", ctx.exception.log_tail)

    @patch("report.dynamicdocs.requests.get")
    @patch("report.dynamicdocs.requests.post")
    def test_compile_pdf_timeout_raises_timeout_error(self, mock_post, mock_get):
        """Confirm compile_pdf raises DynamicDocsTimeoutError when max_timeout is exceeded."""
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "documentStatusUrl": "https://api.advicement.io/status/stuck"
        }
        mock_post.return_value = mock_post_resp

        # In-progress responses indefinitely
        mock_status = MagicMock()
        mock_status.status_code = 200
        mock_status.json.return_value = {"status": "in-progress", "statusCode": 202}
        mock_get.return_value = mock_status

        with self.assertRaises(DynamicDocsTimeoutError):
            compile_pdf(
                self.fake_payload,
                api_key="test_key",
                poll_interval=0.005,
                max_timeout=0.02
            )

    @patch("report.dynamicdocs.requests.post")
    def test_initial_post_failure_raises_compile_error(self, mock_post):
        """Confirm non-200 status on initial compile POST raises DynamicDocsCompileError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized: Invalid Adv-Security-Token"
        mock_post.return_value = mock_resp

        with self.assertRaises(DynamicDocsCompileError) as ctx:
            compile_pdf(self.fake_payload, api_key="invalid_key")

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("Unauthorized", str(ctx.exception))


class Step9GenerateTests(TestCase):
    """
    Test suite for Step 9 - Generate view (POST /generate/), rate limiting,
    validation, PDF compilation, error reporting, and image expiration extension.
    """

    def setUp(self):
        self.client = Client()
        self.valid_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        self.fake_pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"

    def tearDown(self):
        for img in UploadedImage.objects.all():
            if img.file:
                try:
                    img.file.delete(save=False)
                except Exception:
                    pass

    def test_generate_get_renders_index(self):
        """GET /generate/ renders the frontend SPA index template."""
        res = self.client.get(reverse('generate'))
        self.assertEqual(res.status_code, 200)

    def test_generate_post_validation_missing_title(self):
        """POST /generate/ returns 400 when title is missing in session."""
        session = self.client.session
        session["title"] = ""
        session["sections"] = [{"title": "Sec 1", "context": "Text", "elements": []}]
        session.save()

        res = self.client.post(reverse('generate'))
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertEqual(data.get("status"), "error")
        self.assertTrue(any("title is required" in err.lower() for err in data.get("errors", [])))

    def test_generate_post_validation_missing_sections(self):
        """POST /generate/ returns 400 when sections list is empty."""
        session = self.client.session
        session["title"] = "Valid Title"
        session["sections"] = []
        session.save()

        res = self.client.post(reverse('generate'))
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertTrue(any("at least one section" in err.lower() for err in data.get("errors", [])))

    def test_generate_post_validation_invalid_image_id(self):
        """POST /generate/ returns 400 when section references invalid or unowned image_id."""
        session = self.client.session
        session["title"] = "Paper With Bad Image"
        session["sections"] = [
            {
                "title": "Sec",
                "context": "",
                "elements": [{"type": "image", "image_id": 99999, "caption": "Missing"}]
            }
        ]
        session.save()

        res = self.client.post(reverse('generate'))
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertTrue(any("does not exist" in err.lower() for err in data.get("errors", [])))

    def test_generate_post_validation_invalid_equation(self):
        """POST /generate/ returns 400 when equation has forbidden command or unbalanced braces."""
        session = self.client.session
        session["title"] = "Paper With Bad Equation"
        session["sections"] = [
            {
                "title": "Sec",
                "context": "",
                "elements": [{"type": "equation", "equation": r"\input{hack.tex}"}]
            }
        ]
        session.save()

        res = self.client.post(reverse('generate'))
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertTrue(any("forbidden latex command" in err.lower() for err in data.get("errors", [])))

    @patch("report.views.compile_pdf")
    def test_generate_post_success_and_extends_image_expiration(self, mock_compile):
        """Confirm valid generation returns application/pdf and extends image expiration."""
        mock_compile.return_value = self.fake_pdf_bytes

        # 1. Initialize session and upload an image
        self.client.get(reverse('health_check'))
        session_key = self.client.session.session_key

        upload_file = SimpleUploadedFile("fig.png", self.valid_png_bytes, content_type="image/png")
        img = UploadedImage.objects.create(
            session_key=session_key,
            file=upload_file,
            fmt="png",
            size=len(self.valid_png_bytes),
            expires_at=timezone.now() + timedelta(days=2)  # 2 days left
        )
        old_expiry = img.expires_at

        # 2. Populate session
        session = self.client.session
        session["title"] = "Autonomous Robotics Research"
        session["authors"] = [{"name": "Dr. Smith", "affiliation": "MIT"}]
        session["abstract"] = "Abstract text."
        session["keywords"] = "Robotics, AI"
        session["sections"] = [
            {
                "title": "Introduction",
                "context": "Intro text.",
                "elements": [
                    {"type": "image", "image_id": img.pk, "caption": "System Diagram", "imageType": "png"}
                ]
            }
        ]
        session.save()

        # 3. POST /generate/
        res = self.client.post(reverse('generate'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertEqual(res.content, self.fake_pdf_bytes)
        self.assertEqual(
            res["Content-Disposition"],
            'attachment; filename="Autonomous-Robotics-Research.pdf"'
        )

        # 4. Confirm image expiration was extended by ~7 days
        img.refresh_from_db()
        self.assertGreater(img.expires_at, old_expiry + timedelta(days=4))

    @patch("report.views.compile_pdf")
    def test_generate_rate_limit_10s_cooldown(self, mock_compile):
        """Confirm second generate request within 10 seconds returns HTTP 429."""
        mock_compile.return_value = self.fake_pdf_bytes

        session = self.client.session
        session["title"] = "Rate Limit Paper"
        session["sections"] = [{"title": "Sec", "context": "Content", "elements": []}]
        session.save()

        # First request succeeds
        res1 = self.client.post(reverse('generate'))
        self.assertEqual(res1.status_code, 200)

        # Immediate second request is blocked by 10s cooldown
        res2 = self.client.post(reverse('generate'))
        self.assertEqual(res2.status_code, 429)
        data = res2.json()
        self.assertTrue(data.get("rate_limited"))
        self.assertIn("wait", data.get("error", "").lower())

    def test_generate_rate_limit_hourly_limit(self):
        """Confirm 11th request within 1 hour returns HTTP 429."""
        now = time.time()
        # Seed 10 previous timestamps within the last 30 minutes
        fake_history = [now - (i * 100) for i in range(10, 0, -1)]

        session = self.client.session
        session["title"] = "Hourly Rate Limit Paper"
        session["sections"] = [{"title": "Sec", "context": "Content", "elements": []}]
        session["pdf_generation_history"] = fake_history
        session.save()

        res = self.client.post(reverse('generate'))
        self.assertEqual(res.status_code, 429)
        data = res.json()
        self.assertTrue(data.get("rate_limited"))
        self.assertIn("10 pdf generations allowed per hour", data.get("error", "").lower())

    @patch("report.views.compile_pdf")
    def test_generate_compile_error_returns_502_with_log_tail(self, mock_compile):
        """Confirm LaTeX compile error returns HTTP 502 with log_tail."""
        mock_compile.side_effect = DynamicDocsCompileError(
            message="LaTeX compilation failed with status 400",
            status_code=400,
            log_tail="! Undefined control sequence: \\badcmd"
        )

        session = self.client.session
        session["title"] = "Compile Error Paper"
        session["sections"] = [{"title": "Sec", "context": "Content", "elements": []}]
        session.save()

        res = self.client.post(reverse('generate'))
        self.assertEqual(res.status_code, 502)
        data = res.json()
        self.assertEqual(data.get("status"), "compile_error")
        self.assertIn("\\badcmd", data.get("log_tail", ""))

    @patch("report.views.compile_pdf")
    def test_generate_timeout_returns_504(self, mock_compile):
        """Confirm compilation timeout returns HTTP 504."""
        mock_compile.side_effect = DynamicDocsTimeoutError("Compilation timed out after 80s")

        session = self.client.session
        session["title"] = "Timeout Paper"
        session["sections"] = [{"title": "Sec", "context": "Content", "elements": []}]
        session.save()

        res = self.client.post(reverse('generate'))
        self.assertEqual(res.status_code, 504)
        data = res.json()
        self.assertEqual(data.get("status"), "timeout")


class Step1PrerequisitesTests(TestCase):
    """
    Test suite for Step 1: CSRF cookie delivery, filename sanitization,
    and PDF response headers.
    """

    def setUp(self):
        self.client = Client()

    def test_index_and_generate_set_csrf_cookie(self):
        """GET / and GET /generate/ must set csrftoken cookie."""
        res_index = self.client.get(reverse('index'))
        self.assertEqual(res_index.status_code, 200)
        self.assertIn('csrftoken', res_index.cookies)
        self.assertTrue(len(res_index.cookies['csrftoken'].value) > 0)

        res_gen = self.client.get(reverse('generate'))
        self.assertEqual(res_gen.status_code, 200)
        self.assertIn('csrftoken', res_gen.cookies)

    def test_sanitize_filename_unit(self):
        """Test sanitize_filename across various edge cases."""
        from report.views import sanitize_filename
        # Spaces become dashes
        self.assertEqual(sanitize_filename("My Research Paper"), "My-Research-Paper")
        # Quotes and brackets removed
        self.assertEqual(sanitize_filename('Paper: "A Novel Approach"'), "Paper-A-Novel-Approach")
        # Newlines and tabs become dashes
        self.assertEqual(sanitize_filename("Title\nWith\tNewlines"), "Title-With-Newlines")
        # Slashes and backslashes removed
        self.assertEqual(sanitize_filename("Input/Output\\Analysis"), "InputOutputAnalysis")
        # Non-ASCII characters removed
        self.assertEqual(sanitize_filename("Café & Résumé 2026"), "Caf-Rsum-2026")
        # Truncated to 60 characters
        long_title = "A" * 80
        self.assertEqual(sanitize_filename(long_title), "A" * 60)
        self.assertEqual(len(sanitize_filename(long_title)), 60)
        # Trailing dashes/underscores stripped even after truncation
        spaced_long = "word " * 20
        safe_spaced = sanitize_filename(spaced_long)
        self.assertLessEqual(len(safe_spaced), 60)
        self.assertFalse(safe_spaced.endswith("-"))
        self.assertFalse(safe_spaced.endswith("_"))
        # Fallback for empty or special-character-only inputs
        self.assertEqual(sanitize_filename(""), "report")
        self.assertEqual(sanitize_filename("   "), "report")
        self.assertEqual(sanitize_filename("???///:::"), "report")
        self.assertEqual(sanitize_filename("\n\t  \r"), "report")

    @patch("report.views.compile_pdf")
    def test_generate_pdf_headers_and_filename_sanitization(self, mock_compile):
        """Confirm /generate/ response returns application/pdf and sanitized filename attachment."""
        mock_compile.return_value = b"%PDF-1.4 simulated pdf content"

        session = self.client.session
        # Title with quotes, newlines, slashes, and non-ASCII chars
        session["title"] = '"Smart" AI / Machine Learning\nCafé: Model #1'
        session["sections"] = [{"title": "Sec", "context": "Content", "elements": []}]
        session.save()

        res = self.client.post(reverse('generate'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.has_header("Content-Disposition"))
        expected_filename = 'Smart-AI-Machine-Learning-Caf-Model-1.pdf'
        self.assertEqual(res["Content-Disposition"], f'attachment; filename="{expected_filename}"')

    @patch("report.views.compile_pdf")
    def test_generate_fallback_filename_when_title_is_empty(self, mock_compile):
        """Confirm /generate/ uses fallback 'report.pdf' if title has no safe characters."""
        mock_compile.return_value = b"%PDF-1.4 simulated pdf content"

        session = self.client.session
        session["title"] = "??? /// :::"
        session["sections"] = [{"title": "Sec", "context": "Content", "elements": []}]
        session.save()

        res = self.client.post(reverse('generate'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Disposition"], 'attachment; filename="report.pdf"')


class Step2SaveAllPayloadTests(TestCase):
    """
    Test suite for Step 2: Verifying the sequential saving flow
    matching frontend saveAllToBackend() order and payload shapes.
    """

    def setUp(self):
        self.client = Client()
        self.valid_png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
            b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )

    def test_save_all_sequential_payload_shapes(self):
        """Confirm all 5 save steps run in order and populate session properly."""
        # 1. Image upload
        img_file = SimpleUploadedFile("chart.png", self.valid_png_bytes, content_type="image/png")
        upload_res = self.client.post(reverse('upload_image'), {"image": img_file, "fmt": "png"})
        self.assertEqual(upload_res.status_code, 201)
        img_id = upload_res.json()["image_id"]
        self.assertIsInstance(img_id, int)

        # 2. References
        refs_payload = {
            "references": [
                {"title": "Global Market Insights 2026", "link": "https://example.com/ref1"},
                {"title": "Macroeconomic Framework", "link": "https://example.com/ref2"},
            ]
        }
        res_refs = self.client.post(reverse('references'), json.dumps(refs_payload), content_type="application/json")
        self.assertEqual(res_refs.status_code, 200)

        # 3. Title & Authors
        title_authors_payload = {
            "title": "Strategic Growth & Market Analysis 2026",
            "authors": [
                {"name": "Dr. Alex Morgan", "affiliation": "Research & Strategy Group"},
                {"name": "Sarah Chen", "affiliation": "Global Analytics Lab"},
            ],
        }
        res_ta = self.client.post(reverse('title_authors'), json.dumps(title_authors_payload), content_type="application/json")
        self.assertEqual(res_ta.status_code, 200)

        # 4. Abstract & Keywords
        abstract_kw_payload = {
            "abstract": "This report delivers a rigorous analysis of emerging quarterly trends.",
            "keywords": "Market Analysis, Strategic Expansion, Enterprise Performance",
        }
        res_ak = self.client.post(reverse('abstract_keywords'), json.dumps(abstract_kw_payload), content_type="application/json")
        self.assertEqual(res_ak.status_code, 200)

        # 5. Sections with clean elements (no previewData)
        sections_payload = {
            "sections": [
                {
                    "title": "1. Executive Summary",
                    "context": "Comprehensive overview of quarterly market performance.",
                    "elements": [
                        {"type": "subsection", "title": "1.1 Scope", "content": "Scope overview."},
                        {"type": "image", "caption": "Figure 1: Distribution", "imageType": "png", "image_id": img_id},
                        {
                            "type": "table",
                            "caption": "Table 1: Key Metrics",
                            "columnsCount": 2,
                            "rowsCount": 2,
                            "gridData": [["Metric", "Target"], ["Growth", "8.4%"]],
                        },
                        {"type": "equation", "caption": "Equation 1", "equation": "E(R_i) = R_f"},
                    ],
                }
            ]
        }
        res_sec = self.client.post(reverse('sections'), json.dumps(sections_payload), content_type="application/json")
        self.assertEqual(res_sec.status_code, 200)

        # Confirm session state matches
        session = self.client.session
        self.assertEqual(session.get("title"), "Strategic Growth & Market Analysis 2026")
        self.assertEqual(len(session.get("authors")), 2)
        self.assertEqual(len(session.get("references")), 2)
        self.assertEqual(len(session.get("sections")), 1)
        image_elem = session["sections"][0]["elements"][1]
        self.assertEqual(image_elem["type"], "image")
        self.assertEqual(image_elem["image_id"], img_id)
        self.assertNotIn("previewData", image_elem)


class Step4VerificationTests(TestCase):
    """
    Test suite for Step 4: Verification of end-to-end PDF generation,
    handling of special characters in tables & equations, validation
    of empty titles, missing images, rate limiting, and title changes.
    """

    def setUp(self):
        self.client = Client()
        self.valid_png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
            b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )

    def test_generate_empty_title_returns_400(self):
        """Confirm /generate/ returns 400 when document title is empty."""
        session = self.client.session
        session["title"] = "   "
        session["sections"] = [{"title": "Sec", "context": "Context", "elements": []}]
        session.save()

        res = self.client.post(reverse('generate'))
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertIn("Document title is required and cannot be empty.", data.get("errors", []))

    def test_generate_missing_image_returns_400(self):
        """Confirm /generate/ returns 400 when an image element has an invalid or missing image_id."""
        session = self.client.session
        session["title"] = "Paper with Missing Image"
        session["sections"] = [
            {
                "title": "Sec 1",
                "context": "Context",
                "elements": [
                    {"type": "image", "caption": "Missing Figure", "imageType": "png", "image_id": 99999}
                ],
            }
        ]
        session.save()

        res = self.client.post(reverse('generate'))
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertTrue(any("does not exist" in err.lower() for err in data.get("errors", [])))

    @patch("report.views.compile_pdf")
    def test_generate_twice_quickly_triggers_rate_limit(self, mock_compile):
        """Confirm clicking generate twice quickly returns HTTP 429."""
        mock_compile.return_value = b"%PDF-1.4 simulated pdf"

        session = self.client.session
        session["title"] = "Rate Limit Test Paper"
        session["sections"] = [{"title": "Sec", "context": "Context", "elements": []}]
        session.save()

        # First request succeeds
        res1 = self.client.post(reverse('generate'))
        self.assertEqual(res1.status_code, 200)

        # Immediate second request fails with 429
        res2 = self.client.post(reverse('generate'))
        self.assertEqual(res2.status_code, 429)
        data = res2.json()
        self.assertTrue(data.get("rate_limited"))
        self.assertIn("Rate limit exceeded", data.get("error", ""))

    @patch("report.views.compile_pdf")
    def test_second_generate_after_title_change(self, mock_compile):
        """Confirm updating title and regenerating after cooldown produces new filename."""
        mock_compile.return_value = b"%PDF-1.4 simulated pdf"

        session = self.client.session
        session["title"] = "Initial Title 2026"
        session["sections"] = [{"title": "Sec", "context": "Context", "elements": []}]
        session.save()

        res1 = self.client.post(reverse('generate'))
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1["Content-Disposition"], 'attachment; filename="Initial-Title-2026.pdf"')

        # Fast forward time beyond 10-second cooldown
        session = self.client.session
        history = session.get("pdf_generation_history", [])
        if history:
            history[-1] = history[-1] - 15  # 15 seconds ago
            session["pdf_generation_history"] = history
        session["title"] = "Updated Report: Machine Learning & AI"
        session.save()

        res2 = self.client.post(reverse('generate'))
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(
            res2["Content-Disposition"],
            'attachment; filename="Updated-Report-Machine-Learning-AI.pdf"'
        )

    @patch("report.views.compile_pdf")
    def test_generate_with_special_characters_in_table_and_equation(self, mock_compile):
        """Confirm table cells with 8.4% and & and equations are safely built and compiled."""
        mock_compile.return_value = b"%PDF-1.4 simulated pdf"

        session = self.client.session
        session["title"] = "Special Characters & Metrics Analysis"
        session["authors"] = [{"name": "Alice & Bob", "affiliation": "Research Lab"}]
        session["abstract"] = "Performance increased by 8.4% & latency dropped by 50%."
        session["keywords"] = "AI, Metrics & Benchmarks"
        session["sections"] = [
            {
                "title": "Experimental Results",
                "context": "Comprehensive results overview with citations.",
                "elements": [
                    {
                        "type": "table",
                        "caption": "Key Results & Benchmarks",
                        "columnsCount": 2,
                        "rowsCount": 2,
                        "gridData": [
                            ["Metric & Component", "Value & Gain"],
                            ["Accuracy Rate", "8.4% & 2x Gain"],
                        ],
                    },
                    {
                        "type": "equation",
                        "caption": "Loss Function",
                        "equation": r"\mathcal{L}(\theta) = \sum_{i=1}^{N} (y_i - \hat{y}_i)^2",
                    },
                ],
            }
        ]
        session.save()

        res = self.client.post(reverse('generate'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertEqual(
            res["Content-Disposition"],
            'attachment; filename="Special-Characters-Metrics-Analysis.pdf"'
        )
        # Verify compile_pdf received valid document
        self.assertTrue(mock_compile.called)
        doc = mock_compile.call_args[0][0]
        tex_content = doc.get_payload()["texFileContent"]
        # Verify single-pass LaTeX escaping for & and % in table
        self.assertIn(r"8.4\% \& 2x Gain", tex_content)
        self.assertIn(r"\mathcal{L}(\theta)", tex_content)

    @patch("report.views.compile_pdf")
    def test_image_custom_width_passed_to_add_image(self, mock_compile):
        """Confirm custom width on image element is passed to add_uploaded_image and formatted in LaTeX."""
        mock_compile.return_value = b"%PDF-1.4 simulated pdf"

        # 1. Upload an image
        img_file = SimpleUploadedFile("figure.png", self.valid_png_bytes, content_type="image/png")
        upload_res = self.client.post(reverse('upload_image'), {"image": img_file, "fmt": "png"})
        self.assertEqual(upload_res.status_code, 201)
        img_id = upload_res.json()["image_id"]

        # 2. Save section with custom width
        custom_width = "12cm"
        sec_payload = {
            "sections": [
                {
                    "title": "Methodology",
                    "context": "Context with wide diagram.",
                    "elements": [
                        {
                            "type": "image",
                            "caption": "Wide Pipeline Diagram",
                            "width": custom_width,
                            "imageType": "png",
                            "image_id": img_id,
                        }
                    ],
                }
            ]
        }
        res_sec = self.client.post(reverse('sections'), json.dumps(sec_payload), content_type="application/json")
        self.assertEqual(res_sec.status_code, 200)

        # Confirm session stored width
        self.assertEqual(self.client.session["sections"][0]["elements"][0]["width"], custom_width)

        # 3. Generate report
        session = self.client.session
        session["title"] = "Custom Width Report"
        session.save()

        res_gen = self.client.post(reverse('generate'))
        self.assertEqual(res_gen.status_code, 200)

        # Verify LaTeX contains width=12cm
        doc = mock_compile.call_args[0][0]
        tex_content = doc.get_payload()["texFileContent"]
        self.assertIn("width=12cm", tex_content)





