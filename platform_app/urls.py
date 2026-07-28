from django.http import HttpResponse
from django.urls import path


def placeholder(request):
    return HttpResponse("independent image platform")


urlpatterns = [
    path("", placeholder, name="batch_list"),
]
