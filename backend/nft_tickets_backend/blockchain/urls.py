from django.urls import path

from .views import (
    ConfirmChainTransactionView,
    CancelEventView,
    InitializePlatformView,
    ResolveFrozenEventView,
    SubmitChainTransactionView,
    WithdrawEventFundsView,
)

urlpatterns = [
    path(
        "platform/initialize/",
        InitializePlatformView.as_view(),
        name="platform-initialize",
    ),
    path(
        "events/<int:event_id>/resolve-frozen/",
        ResolveFrozenEventView.as_view(),
        name="resolve-frozen-event",
    ),
    path(
        "events/<int:event_id>/withdraw/",
        WithdrawEventFundsView.as_view(),
        name="withdraw-event-funds",
    ),
    path(
        "events/<int:event_id>/cancel/",
        CancelEventView.as_view(),
        name="cancel-event",
    ),
    path(
        "transactions/<int:pk>/submit/",
        SubmitChainTransactionView.as_view(),
        name="chain-transaction-submit",
    ),
    path(
        "transactions/<int:pk>/confirm/",
        ConfirmChainTransactionView.as_view(),
        name="chain-transaction-confirm",
    ),
]
