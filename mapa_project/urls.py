from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth.views import LoginView, LogoutView
from analyzer.forms import MapaAuthenticationForm

urlpatterns = [
    path('login/', LoginView.as_view(
        template_name='registration/login.html',
        authentication_form=MapaAuthenticationForm,
        redirect_authenticated_user=True,
    ), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('', include('analyzer.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
