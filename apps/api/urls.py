from django.urls import path, include
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.views import LockoutTokenObtainPairView

urlpatterns = [
    path('token/', LockoutTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('v1/', include('apps.api.v1.urls')),
]
