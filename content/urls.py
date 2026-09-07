from django.urls import path

from . import views

urlpatterns = [
    path('content/', views.feed, name='content_feed'),
    path('content/upload', views.content_upload, name='content_upload'),
    path('content/<int:pk>/', views.content_detail, name='content_detail'),
    path('content/<int:pk>/like', views.content_like, name='content_like'),
    path('content/<int:pk>/comment', views.content_comment, name='content_comment'),
    path('content/<int:pk>/share', views.content_share, name='content_share'),
    path('content/<int:pk>/view', views.content_view_ping, name='content_view_ping'),
]
