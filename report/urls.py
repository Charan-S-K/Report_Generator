"""
URL configuration for the report application.
"""
from django.urls import path
from . import views

urlpatterns = [
    # Frontend SPA named routes all pointing to the main template view
    path('', views.index, name='index'),
    path('reference/', views.index, name='reference'),
    path('abstract/', views.index, name='abstract'),
    path('context/', views.index, name='context'),
    path('generate/', views.generate_view, name='generate'),
    path('api/generate/', views.generate_view, name='api_generate'),

    # Health check endpoint
    path('health/', views.health_check, name='health_check'),

    # Step 2: References API endpoints
    path('references/', views.references_view, name='references'),
    path('api/references/', views.references_view, name='api_references'),

    # Step 3: Title & Authors API endpoints
    path('title-authors/', views.title_authors_view, name='title_authors'),
    path('api/title-authors/', views.title_authors_view, name='api_title_authors'),

    # Step 4: Abstract & Keywords API endpoints
    path('abstract-keywords/', views.abstract_keywords_view, name='abstract_keywords'),
    path('api/abstract-keywords/', views.abstract_keywords_view, name='api_abstract_keywords'),

    # Step 5: Sections & Elements API endpoints
    path('sections/', views.sections_view, name='sections'),
    path('api/sections/', views.sections_view, name='api_sections'),

    # Step 6: Image Upload API endpoints
    path('images/upload/', views.upload_image_view, name='upload_image'),
    path('api/images/upload/', views.upload_image_view, name='api_upload_image'),
]

