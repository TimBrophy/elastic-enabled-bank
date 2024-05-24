"""
Django settings for config project.

Generated by 'django-admin startproject' using Django 4.1.7.

For more information on this file, see
https://docs.djangoproject.com/en/4.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/4.1/ref/settings/
"""
import os
from pathlib import Path
from urllib.parse import urlparse
import environ
from django.core.management.utils import get_random_secret_key


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Take environment variables from .env file
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))
env = environ.Env()

# Specify the demo user that all views will filter on
DEMO_USER_ID = 1
CRISPY_TEMPLATE_PACK = 'bootstrap4'

# Django secret key specific settings
SECRET_KEY = env('DJANGO_SECRET_KEY',default=get_random_secret_key())

# Application specific settings
GOOGLE_MAPS_API_KEY = env('GOOGLE_MAPS_API_KEY',default=None)
TRANSACTION_INDEX_NAME = env('TRANSACTION_INDEX_NAME',default='search-bank-project-transactions_v1')
TRANSACTION_PIPELINE_NAME = env('TRANSACTION_PIPELINE_NAME',default='ml-inference-search-bank-project-transactions_v1')
MODEL_ID = env('TRANSFORMER_MODEL',default='.elser_model_2_linux-x86_64')
TRANSFORMER_MODEL = MODEL_ID
PRODUCT_INDEX = env('PRODUC_INDEX',default='banking-products')
PRODUCT_INDEX_PIPELINE_NAME = env('PRODUCT_INDEX_PIPELINE_NAME',default='ml-inference-search-bank-project-transactions_v1')
CUSTOMER_SUPPORT_INDEX = env('CUSTOMER_SUPPORT_INDEX',default='search-customer-support')
LLM_AUDIT_LOG_INDEX = env('LLM_AUDIT_LOG_INDEX',default='llm-audit-log')
LLM_AUDIT_LOG_INDEX_PIPELINE_NAME = env('LLM_AUDIT_LOG_INDEX_PIPELINE_NAME',default='ml-inference-sentiment')
LLM_PROVIDER = env('LLM_PROVIDER',default='azure')
openai_api_key = env('openai_api_key',default='')
openai_api_type = env('openai_api_type',default=None)
openai_api_base = env('openai_api_base',default=None)
openai_api_version = env('openai_api_version',default=None)

aws_access_key = env('aws_access_key',default=None)
aws_secret_key = env('aws_secret_key',default=None)
aws_region = env('aws_region',default=None)
aws_model_id = env('aws_model_id',default=None)

elastic_cloud_id = env('ELASTIC_CLOUD_ID',default=None)
elastic_user = env('ELASTIC_USER',default=None)
elastic_password = env('ELASTIC_PASSWORD',default=None)
elastic_api_key = env('ELASTIC_API_KEY',default=None)
kibana_url = env('KIBANA_URL',default=None)

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.messages',
    'django.contrib.sessions',
    'django.contrib.staticfiles',
    'envmanager',
    'markdownify.apps.MarkdownifyConfig',
    'onlinebanking',
    'public',
]

MIDDLEWARE = [
    'elasticapm.contrib.django.middleware.TracingMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [str(BASE_DIR.joinpath('templates'))],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/4.1/ref/settings/#databases

DATABASES = {
    'default': env.db()
}

ELASTIC_APM = {
    'SERVICE_NAME': 'elastic-enabled-bank',
    'DJANGO_TRANSACTION_NAME_FROM_ROUTE': True,
    'DEBUG': True,
}

# Password validation
# https://docs.djangoproject.com/en/4.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.1/topics/i18n/

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.1/howto/static-files/
STATIC_URL = 'static/'

# Base url to serve media files
MEDIA_URL = 'media/'

# Path where media is stored
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Default primary key field type
# https://docs.djangoproject.com/en/4.1/ref/settings/#default-auto-field
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    'handlers': {
        'elasticapm': {
            'level': 'INFO',
            'class': 'elasticapm.contrib.django.handlers.LoggingHandler',
        },
        'console': {
            'level': 'WARNING',
            'class': 'logging.StreamHandler'
        }
    },
    'loggers': {
        'elasticapm': {
            'level': 'WARNING',
            'handlers': ['console']
        },
    },
}

# SECURITY WARNING: It's recommended that you use this when
# running in production. The URL will be known once you first deploy
# to Cloud Run. This code takes the URL and converts it to both these settings formats.
CLOUDRUN_SERVICE_URL = env("CLOUDRUN_SERVICE_URL", default=None)
if CLOUDRUN_SERVICE_URL:
    ALLOWED_HOSTS = [urlparse(CLOUDRUN_SERVICE_URL).netloc , '127.0.0.1']
    CSRF_TRUSTED_ORIGINS = [CLOUDRUN_SERVICE_URL]
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    DEBUG = False
else:
    ALLOWED_HOSTS = ["*"]
    DEBUG = True

# If the flag as been set, configure to use proxy
if os.getenv("USE_CLOUD_SQL_AUTH_PROXY", None):
    DATABASES["default"]["HOST"] = "127.0.0.1"
    DATABASES["default"]["PORT"] = 5432