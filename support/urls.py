"""Customer support routes — one page per dashboard role."""
from django.urls import path

from . import views

urlpatterns = [
    path('player/support', views.player_support, name='player_support'),
    path('organizer/support', views.organizer_support, name='organizer_support'),
]
