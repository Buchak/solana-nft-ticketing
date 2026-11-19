from django.urls import path

from .views import CryptoPurchaseCreateView

urlpatterns = [
    path(
        "events/<slug:event_slug>/ticket-types/<int:ticket_type_id>/purchase/",
        CryptoPurchaseCreateView.as_view(),
        name="crypto-ticket-purchase",
    ),
]
