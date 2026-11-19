from django.urls import path

from . import views

urlpatterns = [
    path("mine/", views.OrganizerEventListView.as_view(), name="organizer-event-list"),
    path("", views.EventListCreateView.as_view(), name="event-list"),
    path(
        "<slug:slug>/disputes/",
        views.EventDisputeCreateView.as_view(),
        name="event-dispute-create",
    ),
    path("<slug:slug>/balance/", views.EventAccountBalanceView.as_view(), name="event-account-balance"),
    path("<slug:slug>/", views.EventDetailUpdateView.as_view(), name="event-detail"),
    path(
        "<slug:event_slug>/ticket-types/",
        views.EventTicketTypeListCreateView.as_view(),
        name="event-ticket-types",
    ),
]
