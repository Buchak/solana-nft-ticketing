from django.urls import path

from . import views

urlpatterns = [
    path("scan/", views.TicketScanView.as_view(), name="ticket-scan"),
    path(
        "scan/<int:transaction_id>/",
        views.TicketScanStatusView.as_view(),
        name="ticket-scan-status",
    ),
    path("mine/", views.OwnedTicketListView.as_view(), name="ticket-mine"),
    path("<int:ticket_id>/refund/", views.TicketRefundView.as_view(), name="ticket-refund"),
    path("<int:ticket_id>/qr/", views.TicketQRView.as_view(), name="ticket-qr"),
]
