# config/context_processors.py
from django.conf import settings

def env_variables(request):
    return {
        'ELASTIC_APM_SERVER_URL': settings.ELASTIC_APM_SERVER_URL,
        'ELASTIC_APM_SERVICE_VERSION': settings.ELASTIC_APM_SERVICE_VERSION,
        'ELASTIC_APM_ENVIRONMENT': settings.ELASTIC_APM_ENVIRONMENT
    }