from django.urls import path

from . import views

urlpatterns = [
    path("nonce/", views.WalletNonceView.as_view(), name="wallet-login-nonce"),
    path("verify/", views.WalletLoginView.as_view(), name="wallet-login-verify"),
    path("logout/", views.WalletLogoutView.as_view(), name="wallet-logout"),
    path("me/", views.WalletMeView.as_view(), name="wallet-me"),
]
